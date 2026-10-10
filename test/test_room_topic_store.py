#!/usr/bin/env python3
"""Execute the actual room-topic transaction with filesystem faults and power cuts."""

from pathlib import Path
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest
import zlib

ROOT = Path(__file__).resolve().parents[1]

HARNESS = r'''
#include <algorithm>
#include <cassert>
#include <cerrno>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <limits>
#include <map>
#include <memory>
#include <set>
#include <string>
#include <vector>

struct PowerCut {};
class MemoryFS;
static MemoryFS* metadata_filesystem = nullptr;
class File {
  MemoryFS* fs = nullptr;
  std::shared_ptr<std::vector<uint8_t>> bytes;
  std::string path;
  size_t cursor = 0;
  bool writable = false, directory = false, valid = false;
public:
  File() = default;
  File(MemoryFS*, std::shared_ptr<std::vector<uint8_t>>, const char*, bool, bool);
  operator bool() const { return valid; }
  bool isDirectory() const { return directory; }
  size_t size();
  int read(uint8_t*, size_t);
  size_t write(const uint8_t*, size_t);
  void flush();
  void close();
};
class MemoryFS {
public:
  std::map<std::string, std::shared_ptr<std::vector<uint8_t>>> files;
  std::set<std::string> faults, directories;
  std::vector<std::string> trace;
  size_t boundary = 0, cut_at = 0;
  bool short_write = false, short_read = false;
  std::vector<uint8_t> replacement_on_close;
  unsigned write_opens = 0;
  void point(const std::string& event) {
    trace.push_back(event);
    if (++boundary == cut_at) throw PowerCut{};
  }
  bool begin(const std::string& op) { point("before:" + op); return !faults.count(op); }
  void end(const std::string& op) { point("after:" + op); }
  bool exists(const char* path) {
    const std::string op = std::string("stat:") + path;
    const bool okay = begin(op);
    const bool present = files.count(path) || directories.count(path);
    end(op);
    return okay && present;
  }
  bool remove(const char* path) {
    const std::string op = std::string("remove:") + path;
    if (!begin(op)) return false;
    const bool removed = files.erase(path) + directories.erase(path) != 0;
    end(op); return removed;
  }
  bool rename(const char* from, const char* to) {
    const std::string op = std::string("rename:") + from + ":" + to;
    if (!begin(op) || !files.count(from) || files.count(to) || directories.count(to)) return false;
    files[to] = files[from]; files.erase(from); end(op); return true;
  }
  File open(const char* path, const char* mode = "r", bool create = false) {
    (void)create;
    const bool write = mode[0] == 'w';
    const std::string op = std::string("open:") + (write ? "w:" : "r:") + path;
    if (!begin(op)) return {};
    if (write) {
      ++write_opens;
      files[path] = std::make_shared<std::vector<uint8_t>>();
      if (faults.count(std::string("directory:") + path)) directories.insert(path);
    }
    if (!files.count(path) && !directories.count(path)) { end(op); return {}; }
    if (!files.count(path)) files[path] = std::make_shared<std::vector<uint8_t>>();
    File file(this, files[path], path, write, directories.count(path));
    end(op); return file;
  }
  File open(const char* path, int mode) { assert(mode == 1); return open(path, "w"); }
  void put(const char* path, const std::vector<uint8_t>& bytes) {
    files[path] = std::make_shared<std::vector<uint8_t>>(bytes);
  }
  std::vector<uint8_t> get(const char* path) const { return *files.at(path); }
  void reset() { trace.clear(); boundary = cut_at = 0; faults.clear(); short_write = short_read = false; write_opens = 0; }
};
File::File(MemoryFS* owner, std::shared_ptr<std::vector<uint8_t>> data,
           const char* name, bool write, bool dir)
    : fs(owner), bytes(data), path(name), writable(write), directory(dir), valid(true) {}
size_t File::size() {
  const std::string op = "size:" + path;
  const bool okay = fs->begin(op); const size_t size = okay ? bytes->size() : 0;
  fs->end(op); return size;
}
int File::read(uint8_t* output, size_t length) {
  const std::string op = "read:" + path;
  if (!valid || directory || !fs->begin(op)) return -1;
  length = std::min(length, bytes->size() - cursor);
  if (fs->short_read && length) --length;
  if (length) memcpy(output, bytes->data() + cursor, length);
  cursor += length; fs->end(op); return static_cast<int>(length);
}
size_t File::write(const uint8_t* data, size_t length) {
  const std::string op = "write:" + path;
  if (!valid || directory || !fs->begin(op)) return 0;
  if (fs->short_write && length) --length;
  bytes->insert(bytes->end(), data, data + length); fs->end(op); return length;
}
void File::flush() {
  const std::string op = "flush:" + path;
  if (!fs->begin(op) && !bytes->empty()) bytes->pop_back();
  fs->end(op);
}
void File::close() {
  const std::string op = std::string("close:") + (writable ? "w:" : "r:") + path;
  if (!fs->begin(op) && writable && !bytes->empty()) (*bytes)[0] ^= 1;
  if (writable && !fs->replacement_on_close.empty()) *bytes = fs->replacement_on_close;
  valid = false; fs->end(op);
}
#define FILE_O_WRITE 1
#include <helpers/RoomTopicStore.h>

static const char* P = mesh::ROOM_TOPIC_PRIMARY_PATH;
static const char* T = mesh::ROOM_TOPIC_TEMP_PATH;
static const char* B = mesh::ROOM_TOPIC_BACKUP_PATH;
static const std::vector<uint8_t> OLD = { @OLD_VECTOR@ };
static const std::vector<uint8_t> NEW = { @NEW_VECTOR@ };
static unsigned scenarios = 0;
static bool save(MemoryFS& fs, const char* text) {
  metadata_filesystem = &fs; return mesh::saveRoomTopic(&fs, text);
}
static bool load(MemoryFS& fs, char (&out)[152]) {
  metadata_filesystem = &fs; return mesh::loadRoomTopic(&fs, out);
}
static void loaded(MemoryFS& fs, const char* expected) {
  char text[152]; memset(text, 0x7f, sizeof(text));
  assert(load(fs, text) && strcmp(text, expected) == 0);
}
static void checkFailedSave(MemoryFS& fs) {
  assert(!save(fs, "New topic"));
  fs.reset(); loaded(fs, "Old topic");
  assert(fs.get(P) == OLD);
  assert(!fs.files.count(T) && !fs.files.count(B)); ++scenarios;
}
static void crc(std::vector<uint8_t>& image) {
  uint32_t sum = 0xffffffffu;
  for (size_t i = 0; i < 156; ++i) {
    sum ^= image[i];
    for (unsigned bit = 0; bit < 8; ++bit) sum = (sum >> 1) ^ ((sum & 1) ? 0xedb88320u : 0);
  }
  sum ^= 0xffffffffu;
  for (unsigned i = 0; i < 4; ++i) image[156 + i] = uint8_t(sum >> (8 * i));
}
int main() {
  { MemoryFS fs; loaded(fs, ""); assert(save(fs, "Old topic"));
    assert(fs.get(P) == OLD); loaded(fs, "Old topic");
    assert(save(fs, "New topic")); assert(fs.get(P) == NEW); loaded(fs, "New topic");
    assert(save(fs, "")); loaded(fs, ""); assert(fs.files.count(P)); ++scenarios; }
  { MemoryFS fs; char maximum[152]; memset(maximum, 'x', 151); maximum[151] = 0;
    assert(save(fs, maximum)); loaded(fs, maximum);
    const auto before = fs.get(P); fs.reset(); char oversized[153]; memset(oversized, 'x', 152); oversized[152] = 0;
    assert(!save(fs, oversized) && !save(fs, nullptr) && fs.trace.empty());
    assert(fs.get(P) == before); ++scenarios; }
  { char output[152]; memset(output, 7, sizeof(output));
    assert(!mesh::loadRoomTopic(static_cast<MemoryFS*>(nullptr), output));
    assert(output[0] == 0 && !mesh::saveRoomTopic(static_cast<MemoryFS*>(nullptr), "x")); ++scenarios; }

  std::vector<std::vector<uint8_t>> invalid = { {}, {1}, std::vector<uint8_t>(159), std::vector<uint8_t>(161), OLD };
  invalid.back()[159] ^= 1;
  auto future = OLD; future[3] = 2; crc(future); invalid.push_back(future);
  auto magic = OLD; magic[0] = 'X'; crc(magic); invalid.push_back(magic);
  auto unterminated = OLD; std::fill(unterminated.begin() + 4, unterminated.begin() + 156, 'x'); crc(unterminated); invalid.push_back(unterminated);
  auto padding = OLD; padding[100] = 'x'; crc(padding); invalid.push_back(padding);
  for (const auto& bytes : invalid) {
    MemoryFS fs; fs.put(P, bytes); fs.put(B, OLD); fs.put(T, NEW);
    char text[152]; assert(!load(fs, text) && text[0] == 0);
    assert(!save(fs, "New topic")); assert(fs.get(P) == bytes && fs.get(B) == OLD && fs.get(T) == NEW);
    assert(fs.write_opens == 0); ++scenarios;
    MemoryFS only_backup; only_backup.put(B, bytes); only_backup.put(T, NEW);
    assert(!load(only_backup, text) && !save(only_backup, "New topic"));
    assert(!only_backup.files.count(P) && only_backup.get(B) == bytes && only_backup.get(T) == NEW); ++scenarios;
  }
  for (size_t position = 0; position < OLD.size(); ++position) {
    MemoryFS fs; auto corrupted = OLD; corrupted[position] ^= 1;
    fs.put(P, corrupted); fs.put(B, NEW); fs.put(T, NEW);
    char text[152]; memset(text, 0x7f, sizeof(text));
    assert(!load(fs, text));
    assert(std::all_of(text, text + sizeof(text), [](char value) { return value == 0; }));
    assert(!save(fs, "New topic") && fs.get(P) == corrupted);
    assert(fs.get(B) == NEW && fs.get(T) == NEW && fs.write_opens == 0); ++scenarios;
  }
  { MemoryFS fs; fs.put(P, NEW); fs.put(B, OLD); fs.put(T, OLD);
    loaded(fs, "New topic"); assert(!fs.files.count(B) && !fs.files.count(T)); ++scenarios; }
  { MemoryFS fs; fs.put(B, OLD); fs.put(T, NEW);
    loaded(fs, "Old topic"); assert(fs.get(P) == OLD && !fs.files.count(T)); ++scenarios; }
  { MemoryFS fs; fs.put(T, NEW); loaded(fs, ""); assert(!fs.files.count(P) && !fs.files.count(T)); ++scenarios; }
  { MemoryFS fs; fs.put(B, OLD); fs.faults.insert(std::string("rename:") + B + ":" + P);
    char text[152]; assert(!load(fs, text) && !save(fs, "New topic"));
    assert(fs.get(B) == OLD && !fs.files.count(P)); ++scenarios; }

  for (const std::string& fault : {std::string("open:w:") + T, std::string("write:") + T,
      std::string("flush:") + T, std::string("close:w:") + T,
      std::string("open:r:") + T, std::string("read:") + T,
      std::string("size:") + T, std::string("rename:") + P + ":" + B,
      std::string("rename:") + T + ":" + P, std::string("directory:") + T}) {
    MemoryFS fs; fs.put(P, OLD); fs.faults.insert(fault); checkFailedSave(fs);
  }
  { MemoryFS fs; fs.put(P, OLD); fs.short_write = true; checkFailedSave(fs); }
  { MemoryFS fs; fs.put(P, OLD); fs.short_read = true; checkFailedSave(fs); }
  { MemoryFS fs; fs.put(P, OLD); fs.replacement_on_close = OLD;
    // A valid but stale image must fail exact readback, not pass on CRC alone.
    checkFailedSave(fs); }
  { MemoryFS fs; fs.put(P, OLD); fs.faults.insert(std::string("rename:") + T + ":" + P);
    fs.faults.insert(std::string("rename:") + B + ":" + P);
    fs.faults.insert(std::string("remove:") + T); assert(!save(fs, "New topic"));
    assert(!fs.files.count(P) && fs.get(B) == OLD && fs.get(T) == NEW);
    fs.reset(); loaded(fs, "Old topic"); assert(fs.get(P) == OLD); ++scenarios; }
  { MemoryFS fs; fs.faults.insert(std::string("rename:") + T + ":" + P);
    fs.faults.insert(std::string("remove:") + T); assert(!save(fs, "New topic"));
    fs.reset(); loaded(fs, ""); assert(!fs.files.count(P)); ++scenarios; }

  for (const char* artifact : {B, T}) {
    MemoryFS fs; fs.put(P, NEW); fs.put(artifact, OLD);
    fs.faults.insert(std::string("remove:") + artifact);
    loaded(fs, "New topic"); const unsigned writes = fs.write_opens;
    assert(!save(fs, "Old topic") && fs.write_opens == writes && fs.get(P) == NEW);
    fs.reset(); assert(save(fs, "Old topic")); loaded(fs, "Old topic"); ++scenarios;
  }
  { MemoryFS fs; fs.put(P, OLD); fs.faults.insert(std::string("remove:") + B);
    assert(save(fs, "New topic") && fs.get(P) == NEW && fs.get(B) == OLD);
    loaded(fs, "New topic"); assert(!save(fs, "Old topic"));
    fs.reset(); loaded(fs, "New topic"); ++scenarios; }
  for (const char* path : {P, B}) {
    MemoryFS fs; fs.put(path, OLD); fs.faults.insert(std::string("open:r:") + path);
    char text[152]; assert(!load(fs, text) && !save(fs, "New topic"));
    assert(fs.get(path) == OLD && fs.write_opens == 0); ++scenarios;
    MemoryFS directory; directory.put(path, OLD); directory.directories.insert(path);
    assert(!load(directory, text) && !save(directory, "New topic")); assert(directory.get(path) == OLD); ++scenarios;
  }
#if defined(ESP32_PLATFORM)
  for (bool present : {false, true}) {
    MemoryFS fs; if (present) fs.put(P, OLD);
    fs.faults.insert(std::string("stat:") + P); char text[152];
    assert(!load(fs, text) && !save(fs, "New topic") && fs.write_opens == 0);
    assert(fs.files.count(P) == size_t(present)); ++scenarios;
  }
  { MemoryFS fs; fs.put(P, OLD); fs.put(B, NEW); fs.faults.insert(std::string("stat:") + B);
    loaded(fs, "Old topic"); assert(!save(fs, "New topic") && fs.get(P) == OLD); ++scenarios; }
#endif

  // Cut power before and after every filesystem boundary, including all read
  // verification operations. Reboot must select exactly old or committed new.
  for (bool existing : {false, true}) {
    MemoryFS baseline; if (existing) baseline.put(P, OLD);
    assert(save(baseline, "New topic")); const size_t boundaries = baseline.boundary;
    for (size_t cut = 1; cut <= boundaries; ++cut) {
      MemoryFS fs; if (existing) fs.put(P, OLD); fs.cut_at = cut;
      try { save(fs, "New topic"); } catch (const PowerCut&) {}
      const bool committed = fs.files.count(P) && fs.get(P) == NEW;
      fs.reset(); loaded(fs, committed ? "New topic" : (existing ? "Old topic" : ""));
      assert(!fs.files.count(T) && !fs.files.count(B));
      if (committed || existing) assert(fs.get(P) == (committed ? NEW : OLD));
      else assert(!fs.files.count(P));
      ++scenarios;
    }
  }
  printf("PASS: %u room topic persistence and power-cut scenarios\n", scenarios);
}
'''

STAT_MOCK = r'''
#pragma once
struct stat {};
inline int stat(const char* absolute, struct stat*) {
  if (!metadata_filesystem || strncmp(absolute, "/spiffs", 7) != 0) { errno = EIO; return -1; }
  const char* path = absolute + 7;
  const std::string op = std::string("stat:") + path;
  const bool okay = metadata_filesystem->begin(op);
  const bool present = metadata_filesystem->files.count(path) || metadata_filesystem->directories.count(path);
  metadata_filesystem->end(op);
  if (!okay) { errno = EIO; return -1; }
  if (!present) { errno = ENOENT; return -1; }
  return 0;
}
'''


def image(text):
    payload = b"RTP\x01" + text.encode().ljust(152, b"\x00")
    return payload + struct.pack("<I", zlib.crc32(payload))


class RoomTopicStoreTest(unittest.TestCase):
    def test_real_store_with_all_backend_modes_and_faults(self):
        compiler = os.environ.get("CXX") or shutil.which("g++") or shutil.which("clang++")
        if compiler is None:
            self.skipTest("a host C++ compiler is required")
        harness = HARNESS
        for marker, text in (("@OLD_VECTOR@", "Old topic"), ("@NEW_VECTOR@", "New topic")):
            harness = harness.replace(marker, ",".join(str(value) for value in image(text)))
        sanitizers = (["-fsanitize=address,undefined", "-fno-sanitize-recover=all",
                       "-fno-pie", "-no-pie"] if sys.platform.startswith("linux") else [])
        with tempfile.TemporaryDirectory(prefix="meshcore-room-topic-") as temporary:
            work = Path(temporary)
            (work / "sys").mkdir()
            (work / "sys/stat.h").write_text(STAT_MOCK)
            source = work / "topic.cpp"
            source.write_text(harness)
            for platform in (None, "NRF52_PLATFORM", "STM32_PLATFORM", "RP2040_PLATFORM", "ESP32_PLATFORM"):
                with self.subTest(platform=platform):
                    binary = work / (platform or "generic")
                    defines = ["-D" + platform + "=1"] if platform else []
                    built = subprocess.run([compiler, "-std=c++11", "-Wall", "-Wextra", "-Werror",
                                            *sanitizers, *defines, "-I" + str(work), "-I" + str(ROOT / "src"),
                                            str(source), "-o", str(binary)],
                                           capture_output=True, text=True, timeout=60)
                    self.assertEqual(built.returncode, 0, built.stdout + built.stderr)
                    checked = subprocess.run([str(binary)], capture_output=True, text=True, timeout=30)
                    self.assertEqual(checked.returncode, 0, checked.stdout + checked.stderr)
                    self.assertIn("room topic persistence and power-cut scenarios", checked.stdout)
                    print(checked.stdout.strip())


if __name__ == "__main__":
    unittest.main()
