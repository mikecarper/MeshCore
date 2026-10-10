#!/usr/bin/env python3
"""Exercise the real bounded room board with faults, paging, and power cuts."""

from pathlib import Path
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

from test_room_topic_store import HARNESS as FILESYSTEM_HARNESS, STAT_MOCK

ROOT = Path(__file__).resolve().parents[1]

LFS_MOCK = r'''
struct lfs_info {};
static constexpr int LFS_ERR_NOENT = -2;
static int lfs_stat(MemoryFS* fs, const char* path, struct lfs_info*) {
  assert(fs && fs->lock_depth == 1);
  const std::string op = std::string("stat:") + path;
  const bool okay = fs->begin(op);
  const bool present = fs->files.count(path) || fs->directories.count(path);
  fs->end(op);
  return !okay ? -5 : present ? 0 : LFS_ERR_NOENT;
}
'''

TESTS = r'''
#define FILE_O_WRITE 1
#include <helpers/RoomBoardStore.h>
using Result = mesh::RoomBoardResult;
using Index = mesh::RoomBoardIndex;
static const char* P = mesh::ROOM_BOARD_PRIMARY_PATH;
static const char* T = mesh::ROOM_BOARD_TEMP_PATH;
static const char* B = mesh::ROOM_BOARD_BACKUP_PATH;
static unsigned scenarios = 0;
static const std::string OLD_BODY = "Original room directions";
static const std::string NEW_BODY = std::string(2048, 'n');
static bool load(MemoryFS& fs, Index& index) {
  metadata_filesystem = &fs; return mesh::loadRoomBoard(&fs, index);
}
static Result save(MemoryFS& fs, uint8_t id, const char* title,
                   const std::string& body, uint32_t version = 0, bool short_reader = false) {
  metadata_filesystem = &fs;
  return mesh::saveRoomBoardArticle(&fs, id, title, body.size(),
      [&](size_t offset, uint8_t* out, size_t length) -> size_t {
        assert(length <= 64 && offset + length <= body.size());
        memcpy(out, body.data() + offset, length);
        return short_reader && length ? length - 1 : length;
      }, version);
}
static Result erase(MemoryFS& fs, uint8_t id, uint32_t version = 0) {
  metadata_filesystem = &fs; return mesh::deleteRoomBoardArticle(&fs, id, version);
}
static Result read(MemoryFS& fs, uint8_t id, uint32_t version, size_t offset,
                   uint8_t* out, size_t capacity, size_t& count) {
  metadata_filesystem = &fs;
  return mesh::readRoomBoardArticle(&fs, id, version, offset, out, capacity, count);
}
static MemoryFS clone(const MemoryFS& source) {
  MemoryFS fs; fs.directories = source.directories;
  for (const auto& file : source.files) fs.put(file.first.c_str(), *file.second);
  return fs;
}
static std::map<std::string, std::vector<uint8_t>> snapshot(const MemoryFS& fs) {
  std::map<std::string, std::vector<uint8_t>> result;
  for (const auto& file : fs.files) result[file.first] = *file.second;
  return result;
}
static Index loaded(MemoryFS& fs, uint32_t revision, uint8_t count) {
  Index index; assert(load(fs, index));
  assert(index.revision == revision && index.count == count); return index;
}
static std::string bodyPath(const mesh::RoomBoardArticle& article) {
  char path[24]; mesh::room_board_detail::articlePath(article, path); return path;
}
static void bodyIs(MemoryFS& fs, const mesh::RoomBoardArticle& article, const std::string& expected) {
  assert(article.body_length == expected.size());
  std::string assembled;
  for (size_t offset = 0; offset <= expected.size();) {
    uint8_t out[39]; memset(out, 0x7f, sizeof(out)); size_t length = 999;
    assert(read(fs, article.id, article.version, offset, out + 1, 37, length) == Result::Success);
    assert(out[0] == 0x7f && out[38] == 0x7f && length <= 37);
    assembled.append(reinterpret_cast<const char*>(out + 1), length);
    offset += length;
    if (length == 0) break;
  }
  assert(assembled == expected);
}
static void checksum(std::vector<uint8_t>& image) {
  const uint32_t value = mesh::room_board_detail::crc(0xffffffffu, image.data(), image.size() - 4) ^ 0xffffffffu;
  mesh::room_board_detail::put32(image.data() + image.size() - 4, value);
}
static void preserveUnavailable(MemoryFS& fs) {
  const auto before = snapshot(fs); const auto directories = fs.directories;
  Index index; index.count = 7; index.revision = 99;
  assert(!load(fs, index) && index.count == 0 && index.revision == 0);
  assert(save(fs, 1, "Changed", NEW_BODY) == Result::Unavailable);
  assert(erase(fs, 1) == Result::Unavailable);
  uint8_t out[128]; size_t count = 99;
  assert(read(fs, 1, 1, 0, out, sizeof(out), count) == Result::Unavailable && count == 0);
  assert(snapshot(fs) == before && fs.directories == directories && fs.write_opens == 0);
  ++scenarios;
}
static void failedUpdate(MemoryFS& fs) {
  assert(save(fs, 1, "New title", NEW_BODY) != Result::Success);
  fs.reset(); Index index = loaded(fs, 1, 1); bodyIs(fs, index.articles[0], OLD_BODY); ++scenarios;
}
int main() {
  MemoryFS initial; Index empty = loaded(initial, 0, 0); (void)empty;
  assert(save(initial, 1, "Old title", OLD_BODY) == Result::Success);
  Index old = loaded(initial, 1, 1); bodyIs(initial, old.articles[0], OLD_BODY);
  initial.reset();
  const auto old_index = initial.get(P);
  const std::string old_path = bodyPath(old.articles[0]);
  const std::string new_path = "/room_board_1.b";

  { MemoryFS fs = clone(initial);
    assert(save(fs, 1, "New title", NEW_BODY, 1) == Result::Success);
    Index next = loaded(fs, 2, 1); assert(next.articles[0].version == 2 && next.articles[0].bank == 1);
    bodyIs(fs, next.articles[0], NEW_BODY);
    assert(save(fs, 1, "Again", "third", 2) == Result::Success);
    next = loaded(fs, 3, 1); assert(next.articles[0].bank == 0); bodyIs(fs, next.articles[0], "third");
    assert(erase(fs, 1, 3) == Result::Success); loaded(fs, 4, 0);
    assert(save(fs, 1, "Replacement", "", 0) == Result::Success);
    next = loaded(fs, 5, 1); bodyIs(fs, next.articles[0], ""); ++scenarios;
  }
  { MemoryFS fs;
    for (uint8_t id = 8; id != 0; --id) assert(save(fs, id, "Directions", std::string(2048, char('a' + id))) == Result::Success);
    Index index = loaded(fs, 8, 8);
    for (uint8_t i = 0; i < 8; ++i) { assert(index.articles[i].id == i + 1); bodyIs(fs, index.articles[i], std::string(2048, char('a' + i + 1))); }
    assert(fs.files.size() == 9); // One metadata image and one streamed body per article.
    assert(erase(fs, 4, index.articles[3].version) == Result::Success);
    index = loaded(fs, 9, 7); assert(index.articles[3].id == 5);
    assert(save(fs, 4, "Returned", "body") == Result::Success);
    index = loaded(fs, 10, 8); assert(index.articles[3].id == 4 && index.articles[3].version == 10); ++scenarios;
  }
  { MemoryFS fs = clone(initial);
    const std::string maximum_title(63, 't'); assert(save(fs, 2, maximum_title.c_str(), "body") == Result::Success);
    assert(save(fs, 3, u8"Café 地図 📡", "body") == Result::Success);
    fs.reset(); const auto before = snapshot(fs);
    for (const std::string& title : {std::string(""), std::string(64, 't'), std::string("\xc0\xaf"), std::string("\xed\xa0\x80"), std::string("\xf4\x90\x80\x80"), std::string("\xe2\x82"), std::string("line\nfeed")}) {
      assert(save(fs, 1, title.c_str(), "body") == Result::Invalid);
    }
    assert(save(fs, 0, "Title", "body") == Result::Invalid);
    assert(save(fs, 9, "Title", "body") == Result::Invalid);
    assert(save(fs, 1, nullptr, "body") == Result::Invalid);
    assert(save(fs, 1, "Title", std::string(2049, 'x')) == Result::Invalid);
    assert(fs.trace.empty() && snapshot(fs) == before); ++scenarios;
  }
  { MemoryFS fs = clone(initial); uint8_t out[130]; size_t count;
    assert(read(fs, 1, 2, 0, out, 128, count) == Result::StaleVersion && count == 0);
    assert(read(fs, 2, 1, 0, out, 128, count) == Result::NotFound && count == 0);
    assert(read(fs, 1, 0, 0, out, 128, count) == Result::Invalid && count == 0);
    assert(read(fs, 1, 1, OLD_BODY.size() + 1, out, 128, count) == Result::Invalid && count == 0);
    assert(read(fs, 1, 1, 0, out, 129, count) == Result::Invalid && count == 0);
    assert(read(fs, 1, 1, 0, out, 0, count) == Result::Invalid && count == 0);
    assert(read(fs, 1, 1, 0, nullptr, 128, count) == Result::Invalid && count == 0);
    const auto before = snapshot(fs);
    assert(save(fs, 1, "Changed", NEW_BODY, 2) == Result::StaleVersion);
    assert(save(fs, 2, "Changed", NEW_BODY, 2) == Result::StaleVersion);
    assert(erase(fs, 1, 2) == Result::StaleVersion && erase(fs, 2) == Result::NotFound);
    assert(snapshot(fs) == before);
    assert(save(fs, 1, "Changed", NEW_BODY, 1) == Result::Success);
    assert(read(fs, 1, 1, 0, out, 128, count) == Result::StaleVersion && count == 0);
    assert(erase(fs, 1, 2) == Result::Success);
    assert(read(fs, 1, 1, 0, out, 128, count) == Result::NotFound && count == 0);
    assert(save(fs, 1, "Reused", "new") == Result::Success);
    assert(read(fs, 1, 1, 0, out, 128, count) == Result::StaleVersion && count == 0); ++scenarios;
  }
  { MemoryFS fs = clone(initial); auto image = fs.get(P);
    mesh::room_board_detail::put32(image.data() + 4, UINT32_MAX); checksum(image); fs.put(P, image);
    loaded(fs, UINT32_MAX, 1); const auto before = snapshot(fs); fs.reset();
    assert(save(fs, 1, "Changed", NEW_BODY) == Result::Unavailable);
    assert(erase(fs, 1) == Result::Unavailable && fs.write_opens == 0 && snapshot(fs) == before); ++scenarios;
  }
  { Index index; assert(!mesh::loadRoomBoard(static_cast<MemoryFS*>(nullptr), index));
    uint8_t out[8]; size_t count;
    assert(mesh::readRoomBoardArticle(static_cast<MemoryFS*>(nullptr), 1, 1, 0, out, 8, count) == Result::Unavailable);
    assert(mesh::deleteRoomBoardArticle(static_cast<MemoryFS*>(nullptr), 1) == Result::Unavailable); ++scenarios;
  }

  // Every single-byte corruption in the real index/body must fail closed and
  // preserve the primary plus its backup/temp evidence without any writes.
  for (const std::string& path : {std::string(P), old_path}) {
    const auto original = initial.get(path.c_str());
    for (size_t byte = 0; byte < original.size(); ++byte) {
      MemoryFS fs = clone(initial); auto corrupted = original; corrupted[byte] ^= 1;
      fs.put(path.c_str(), corrupted); fs.put(B, old_index); fs.put(T, old_index); preserveUnavailable(fs);
    }
    for (const auto& invalid : {std::vector<uint8_t>{}, std::vector<uint8_t>{1}, std::vector<uint8_t>(original.begin(), original.end() - 1)}) {
      MemoryFS fs = clone(initial); fs.put(path.c_str(), invalid); fs.put(B, old_index); fs.put(T, old_index); preserveUnavailable(fs);
    }
    auto trailing = original; trailing.push_back(0);
    MemoryFS fs = clone(initial); fs.put(path.c_str(), trailing); fs.put(B, old_index); preserveUnavailable(fs);
    auto future = original; future[3] = 2; checksum(future);
    fs = clone(initial); fs.put(path.c_str(), future); fs.put(B, old_index); preserveUnavailable(fs);
    fs = clone(initial); fs.directories.insert(path); preserveUnavailable(fs);
    for (const std::string operation : {"open:r:", "read:", "size:"}) {
      fs = clone(initial); fs.faults.insert(operation + path); preserveUnavailable(fs);
    }
  }
  for (const auto& mutation : {std::make_pair(size_t(9), uint8_t(1)), std::make_pair(size_t(8), uint8_t(9)),
      std::make_pair(size_t(16), uint8_t(0)), std::make_pair(size_t(17), uint8_t(2)),
      std::make_pair(size_t(19), uint8_t(9)), std::make_pair(size_t(20), uint8_t(0)),
      std::make_pair(size_t(24), uint8_t(0)), std::make_pair(size_t(70), uint8_t('x'))}) {
    MemoryFS fs = clone(initial); auto image = old_index; image[mutation.first] = mutation.second; checksum(image);
    fs.put(P, image); preserveUnavailable(fs);
  }
  { MemoryFS fs = clone(initial); auto image = fs.get(old_path.c_str()); image[12] = 1; checksum(image);
    fs.put(old_path.c_str(), image); preserveUnavailable(fs); }
  { MemoryFS fs = clone(initial); fs.put(B, old_index); fs.files.erase(P);
    Index index = loaded(fs, 1, 1); bodyIs(fs, index.articles[0], OLD_BODY); assert(!fs.files.count(B)); ++scenarios; }
  { MemoryFS fs; fs.put(T, old_index); fs.put(old_path.c_str(), initial.get(old_path.c_str()));
    loaded(fs, 0, 0); assert(!fs.files.count(P) && !fs.files.count(T)); ++scenarios; }
  { MemoryFS fs = clone(initial); fs.files.erase(P); auto future = old_index; future[3] = 2; checksum(future);
    fs.put(B, future); fs.put(T, old_index); preserveUnavailable(fs); }

  for (const std::string& fault : {std::string("open:w:") + new_path, std::string("write:") + new_path,
      std::string("flush:") + new_path, std::string("close:w:") + new_path,
      std::string("open:r:") + new_path, std::string("read:") + new_path, std::string("size:") + new_path,
      std::string("directory:") + new_path, std::string("open:w:") + T, std::string("write:") + T,
      std::string("flush:") + T, std::string("close:w:") + T, std::string("open:r:") + T,
      std::string("read:") + T, std::string("size:") + T,
      std::string("rename:") + P + ":" + B, std::string("rename:") + T + ":" + P}) {
    MemoryFS fs = clone(initial); fs.faults.insert(fault); failedUpdate(fs);
  }
  { MemoryFS fs = clone(initial); fs.short_write = true; failedUpdate(fs); }
  { MemoryFS fs = clone(initial); fs.short_read = true; failedUpdate(fs); }
  { MemoryFS fs = clone(initial); assert(save(fs, 1, "Changed", NEW_BODY, 0, true) == Result::WriteFailure);
    fs.reset(); loaded(fs, 1, 1); ++scenarios; }
  { MemoryFS fs = clone(initial); fs.replacement_on_close = initial.get(old_path.c_str()); failedUpdate(fs); }
  // A full filesystem can truncate any streamed body or metadata boundary.
  for (size_t budget : {size_t(0), size_t(15), size_t(16), size_t(100), size_t(2067), size_t(2068), size_t(2100), size_t(2159)}) {
    MemoryFS fs = clone(initial); fs.write_budget = budget; failedUpdate(fs);
  }
  { MemoryFS fs = clone(initial); fs.put(new_path.c_str(), std::vector<uint8_t>{1});
    fs.faults.insert("remove:" + new_path); failedUpdate(fs); }
  for (const char* artifact : {T, B}) {
    MemoryFS fs = clone(initial); fs.put(artifact, old_index); fs.faults.insert(std::string("remove:") + artifact);
    loaded(fs, 1, 1); fs.write_opens = 0; assert(save(fs, 1, "Changed", NEW_BODY) == Result::Unavailable && fs.write_opens == 0);
    fs.reset(); loaded(fs, 1, 1); ++scenarios;
  }
  { MemoryFS fs = clone(initial); fs.faults.insert(std::string("remove:") + B);
    assert(save(fs, 1, "Changed", NEW_BODY) == Result::Success);
    loaded(fs, 2, 1); assert(fs.files.count(B));
    assert(save(fs, 1, "Changed again", "x") == Result::Unavailable);
    fs.reset(); loaded(fs, 2, 1); ++scenarios; }
  { MemoryFS fs = clone(initial); fs.faults.insert(std::string("rename:") + T + ":" + P);
    fs.faults.insert(std::string("rename:") + B + ":" + P); fs.faults.insert(std::string("remove:") + T);
    assert(save(fs, 1, "Changed", NEW_BODY) == Result::WriteFailure && !fs.files.count(P) && fs.files.count(B));
    fs.reset(); Index index = loaded(fs, 1, 1); bodyIs(fs, index.articles[0], OLD_BODY); ++scenarios; }
#if defined(NRF52_PLATFORM) || defined(STM32_PLATFORM) || defined(ESP32_PLATFORM)
  { MemoryFS fs = clone(initial); fs.faults.insert(std::string("stat:") + P); preserveUnavailable(fs); }
  { MemoryFS fs = clone(initial); fs.files.erase(P); fs.put(B, old_index);
    fs.faults.insert(std::string("stat:") + B); preserveUnavailable(fs); }
#endif

  // Cut before and after every filesystem operation in publish, update, and
  // delete. Reboot may expose only the preceding image or the committed image.
  for (unsigned mode = 0; mode < 3; ++mode) {
    MemoryFS baseline = mode == 0 ? MemoryFS{} : clone(initial);
    assert((mode == 2 ? erase(baseline, 1) : save(baseline, 1, "New title", NEW_BODY)) == Result::Success);
    const auto committed_image = baseline.get(P); const size_t boundaries = baseline.boundary;
    for (size_t cut = 1; cut <= boundaries; ++cut) {
      MemoryFS fs = mode == 0 ? MemoryFS{} : clone(initial); fs.cut_at = cut;
      try { if (mode == 2) erase(fs, 1); else save(fs, 1, "New title", NEW_BODY); } catch (const PowerCut&) {}
      const bool committed = fs.files.count(P) && fs.get(P) == committed_image;
      fs.reset();
      Index index = loaded(fs, committed ? (mode == 0 ? 1 : 2) : (mode == 0 ? 0 : 1),
                           committed && mode == 2 ? 0 : (mode == 0 && !committed ? 0 : 1));
      if (index.count) bodyIs(fs, index.articles[0], committed ? NEW_BODY : OLD_BODY);
      assert(!fs.files.count(T) && !fs.files.count(B)); ++scenarios;
    }
  }
  printf("PASS: %u room board persistence, version/paging, fault and power-cut scenarios\n", scenarios);
}
'''


class RoomBoardStoreTest(unittest.TestCase):
    def test_real_store_with_all_backend_modes_and_faults(self):
        compiler = os.environ.get("CXX") or shutil.which("g++") or shutil.which("clang++")
        if compiler is None:
            self.skipTest("a host C++ compiler is required")
        filesystem = FILESYSTEM_HARNESS.split("#define FILE_O_WRITE 1")[0]
        filesystem = filesystem.replace("  void _lockFS() {}\n  void _unlockFS() {}\n  MemoryFS* _getFS() { return this; }\n", "", 1)
        filesystem = filesystem.replace("  void point(const std::string& event) {", """  int lock_depth = 0;
  void _lockFS() { assert(lock_depth++ == 0); }
  void _unlockFS() { assert(--lock_depth == 0); }
  MemoryFS* _getFS() { return this; }
  size_t write_budget = std::numeric_limits<size_t>::max();
  void point(const std::string& event) {""")
        filesystem = filesystem.replace("  bytes->insert(bytes->end(), data, data + length);", """  length = std::min(length, fs->write_budget);
  fs->write_budget -= length;
  bytes->insert(bytes->end(), data, data + length);""")
        filesystem = filesystem.replace("void reset() { trace.clear();", "void reset() { lock_depth = 0; write_budget = std::numeric_limits<size_t>::max(); trace.clear();")
        sanitizers = (["-fsanitize=address,undefined", "-fno-sanitize-recover=all", "-fno-pie", "-no-pie"]
                      if sys.platform.startswith("linux") else [])
        with tempfile.TemporaryDirectory(prefix="meshcore-room-board-") as temporary:
            work = Path(temporary)
            (work / "sys").mkdir()
            (work / "sys/stat.h").write_text(STAT_MOCK)
            source = work / "board.cpp"
            for platform in (None, "NRF52_PLATFORM", "STM32_PLATFORM", "RP2040_PLATFORM", "ESP32_PLATFORM"):
                with self.subTest(platform=platform):
                    source.write_text(filesystem + (LFS_MOCK if platform in ("NRF52_PLATFORM", "STM32_PLATFORM") else "") + TESTS)
                    binary = work / (platform or "generic")
                    defines = ["-D" + platform + "=1"] if platform else []
                    built = subprocess.run([compiler, "-std=c++11", "-Wall", "-Wextra", "-Werror", *sanitizers,
                                            *defines, "-I" + str(work), "-I" + str(ROOT / "src"), str(source), "-o", str(binary)],
                                           capture_output=True, text=True, timeout=60)
                    self.assertEqual(built.returncode, 0, built.stdout + built.stderr)
                    checked = subprocess.run([str(binary)], capture_output=True, text=True, timeout=60)
                    self.assertEqual(checked.returncode, 0, checked.stdout + checked.stderr)
                    self.assertIn("room board persistence, version/paging, fault and power-cut scenarios", checked.stdout)
                    print(checked.stdout.strip())


if __name__ == "__main__":
    unittest.main()
