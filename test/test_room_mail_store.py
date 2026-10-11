#!/usr/bin/env python3
"""Run the actual owner-bound mailbox transaction under ASAN and storage faults."""

from pathlib import Path
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

from test_room_topic_store import HARNESS as FILESYSTEM_HARNESS, STAT_MOCK
from test_room_board_store import LFS_MOCK

ROOT = Path(__file__).resolve().parents[1]

# Host-only adapter supplies the same incremental SHA256 interface as Arduino
# Crypto, using independently implemented OpenSSL for reproducible digests.
SHA_MOCK = r'''
#pragma once
#include <cassert>
#include <cstring>
#include <openssl/evp.h>
class SHA256 {
  EVP_MD_CTX* context;
public:
  SHA256(): context(EVP_MD_CTX_new()) { assert(context && EVP_DigestInit_ex(context, EVP_sha256(), nullptr)); }
  ~SHA256() { EVP_MD_CTX_free(context); }
  void update(const void* input, size_t length) { assert(EVP_DigestUpdate(context, input, length)); }
  void finalize(void* output, size_t length) {
    unsigned count = 0; unsigned char digest[32];
    assert(EVP_DigestFinal_ex(context, digest, &count) && count == 32 && length <= 32);
    memcpy(output, digest, length);
  }
};
'''

TESTS = r'''
#define FILE_O_WRITE 1
#include <helpers/RoomMailStore.h>
using Result = mesh::RoomMailResult;
using Settings = mesh::RoomMailSettings;
using Mode = mesh::RoomMailMode;
using Message = mesh::RoomMailMessage;
using Status = mesh::RoomMailStatus;
static const char* P = mesh::ROOM_MAIL_PRIMARY_PATH;
static const char* T = mesh::ROOM_MAIL_TEMP_PATH;
static const char* B = mesh::ROOM_MAIL_BACKUP_PATH;
static unsigned scenarios = 0;
static uint8_t OWNER[32] = {1}, OTHER[32] = {2}, SENDER[32] = {3};
static const std::string OLD_BODY = "Existing private message";
static const std::string NEW_BODY(512, 'n');
static void use(MemoryFS& fs) { metadata_filesystem = &fs; }
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
static Status status(MemoryFS& fs, const uint8_t* owner = OWNER) {
  use(fs); Status out;
  assert(mesh::getRoomMailStatus(&fs, owner, out) == Result::Success); return out;
}
static Result provision(MemoryFS& fs, const uint8_t* owner = OWNER, Mode mode = Mode::Public) {
  use(fs); Settings settings; settings.mode = mode;
  return mesh::saveRoomMailSettings(&fs, owner, settings);
}
static Result send(MemoryFS& fs, uint64_t nonce, const std::string& body, uint32_t& id,
                   const uint8_t* owner = OWNER, const uint8_t* sender = SENDER, uint32_t created = 42) {
  use(fs); return mesh::sendRoomMail(&fs, sender, owner, nonce, body.data(), body.size(), id, created);
}
static void bodyIs(MemoryFS& fs, const uint8_t* owner, uint32_t id, const std::string& expected) {
  use(fs); std::string assembled; Message info;
  for (size_t offset = 0; offset <= expected.size();) {
    uint8_t output[39]; memset(output, 0x7f, sizeof(output)); size_t copied = 999;
    assert(mesh::readRoomMail(&fs, owner, id, offset, output + 1, 37, copied, info) == Result::Success);
    assert(output[0] == 0x7f && output[38] == 0x7f && copied <= 37 && info.length == expected.size());
    assembled.append(reinterpret_cast<const char*>(output + 1), copied); offset += copied;
    if (!copied) break;
  }
  assert(assembled == expected);
}
static void checksum(std::vector<uint8_t>& image) {
  const uint32_t value = mesh::room_mail_detail::crc(0xffffffffu, image.data(), image.size() - 4) ^ 0xffffffffu;
  mesh::room_mail_detail::put32(image.data() + image.size() - 4, value);
}
static uint32_t firstId(MemoryFS& fs) {
  use(fs); Message list[4]; size_t copied;
  assert(mesh::listRoomMail(&fs, OWNER, 0, 0, list, 4, copied) == Result::Success && copied == 1);
  return list[0].id;
}
static void preserveUnavailable(MemoryFS& fs) {
  use(fs); const auto before = snapshot(fs); const auto directories = fs.directories;
  Status out; out.count = 99; out.revision = 99;
  assert(mesh::getRoomMailStatus(&fs, OWNER, out) == Result::Unavailable && !out.count && !out.revision);
  uint32_t id = 123;
  assert(send(fs, 2, NEW_BODY, id) == Result::Unavailable && id == 0);
  assert(mesh::acknowledgeRoomMail(&fs, OWNER, 2) == Result::Unavailable);
  assert(provision(fs) == Result::Unavailable);
  assert(snapshot(fs) == before && fs.directories == directories && fs.write_opens == 0);
  ++scenarios;
}
int main() {
  { MemoryFS fs; use(fs); Status out;
    assert(mesh::getRoomMailStatus(&fs, OWNER, out) == Result::NotFound && out.settings.mode == Mode::Closed && !out.count);
    bool mailbox_only = true; assert(mesh::getRoomMailboxOnly(&fs, OWNER, mailbox_only) == Result::Success && !mailbox_only);
    uint32_t id; assert(send(fs, 1, "No mailbox", id) == Result::NotFound);
    assert(fs.files.empty() && fs.write_opens == 0);
    assert(provision(fs, OWNER, Mode::Closed) == Result::Success);
    Settings stale; stale.mode = Mode::Public;
    const auto before = snapshot(fs);
    assert(mesh::saveRoomMailSettings(&fs, OWNER, stale, 0) == Result::StaleVersion);
    assert(snapshot(fs) == before && status(fs).settings.mode == Mode::Closed);
    assert(send(fs, 1, "Denied", id) == Result::Forbidden);
    assert(send(fs, 1, "Owner always permitted", id, OWNER, OWNER) == Result::Success);
    assert(mesh::acknowledgeRoomMail(&fs, OWNER, id) == Result::Success);
    assert(mesh::acknowledgeRoomMail(&fs, OWNER, id) == Result::Success);
    assert(send(fs, 1, "Owner always permitted", id, OWNER, OWNER) == Result::Duplicate);
    assert(send(fs, 1, "Changed", id, OWNER, OWNER) == Result::Mismatch);
    ++scenarios;
  }
  { MemoryFS fs; assert(provision(fs) == Result::Success); Settings settings;
    settings.mode = Mode::Private; settings.mailbox_only = true; settings.allowed_count = 2;
    memcpy(settings.allowed[0], SENDER, 32); memcpy(settings.allowed[1], OTHER, 32);
    assert(mesh::saveRoomMailSettings(&fs, OWNER, settings, 1) == Result::Success);
    Status own = status(fs); assert(own.settings.mailbox_only && own.settings.allowed_count == 2);
    assert(memcmp(own.settings.allowed[0], OTHER, 32) == 0); // Canonical full-key order.
    bool only = false; assert(mesh::getRoomMailboxOnly(&fs, OWNER, only) == Result::Success && only);
    uint32_t id; assert(send(fs, UINT64_C(0x100000001), u8"Caf\u00e9 \u5730\u56f3 \U0001f4e1", id) == Result::Success);
    assert(send(fs, UINT64_C(0x200000001), "Different high nonce bits", id) == Result::Success);
    uint8_t collision[32] = {3}; collision[31] = 1;
    assert(send(fs, 4, "Short key collision", id, OWNER, collision) == Result::Forbidden);
    assert(mesh::saveRoomMailSettings(&fs, OWNER, settings, 2) == Result::StaleVersion);
    assert(provision(fs, OTHER) == Result::Success);
    Message list[4]; size_t copied;
    assert(mesh::listRoomMail(&fs, OTHER, 0, 0, list, 4, copied) == Result::Success && copied == 0);
    uint8_t output[8]; Message info;
    assert(mesh::readRoomMail(&fs, OTHER, 3, 0, output, sizeof(output), copied, info) == Result::NotFound);
    assert(mesh::acknowledgeRoomMail(&fs, OTHER, 3) == Result::NotFound);
    assert(mesh::deleteRoomMail(&fs, OTHER, 3) == Result::NotFound);
    assert(status(fs).count == 2); ++scenarios;
  }
  { MemoryFS fs; assert(provision(fs) == Result::Success); uint32_t id;
    assert(send(fs, 1, NEW_BODY, id) == Result::Success); bodyIs(fs, OWNER, id, NEW_BODY);
    use(fs); Message info; uint8_t output[128]; size_t copied;
    assert(mesh::readRoomMail(&fs, OWNER, id, 512, output, 128, copied, info) == Result::Success && copied == 0);
    assert(mesh::readRoomMail(&fs, OWNER, id, 513, output, 128, copied, info) == Result::Invalid && !copied);
    assert(mesh::readRoomMail(&fs, OWNER, id, 0, output, 129, copied, info) == Result::Invalid && !copied);
    assert(send(fs, 1, NEW_BODY, id, OWNER, SENDER, 9876) == Result::Duplicate);
    ++scenarios;
  }
  { MemoryFS fs; assert(provision(fs) == Result::Success); uint32_t id;
    use(fs); uint8_t zero[32] = {}; Settings bad; bad.allowed_count = 9;
    assert(mesh::saveRoomMailSettings(&fs, OWNER, bad) == Result::Invalid);
    bad.allowed_count = 1; assert(mesh::saveRoomMailSettings(&fs, OWNER, bad) == Result::Invalid);
    assert(mesh::saveRoomMailSettings(&fs, zero, Settings{}) == Result::Invalid);
    const auto before = snapshot(fs); fs.reset();
    for (const std::string& body : {std::string(), std::string(513, 'x'), std::string("a\0b", 3), std::string("\xc0\xaf"), std::string("\xed\xa0\x80"), std::string("\xf4\x90\x80\x80"), std::string("\xe2\x82")})
      assert(send(fs, 1, body, id) == Result::Invalid && !id);
    assert(send(fs, 0, "x", id) == Result::Invalid);
    assert(send(fs, 1, "x", id, OWNER, zero) == Result::Invalid);
    assert(snapshot(fs) == before && fs.write_opens == 0); ++scenarios;
  }
  { MemoryFS fs; uint32_t id;
    for (uint8_t owner = 1; owner <= 32; ++owner) {
      uint8_t key[32] = {owner}; assert(provision(fs, key) == Result::Success);
      if (owner <= 4) for (uint64_t nonce = 1; nonce <= 4; ++nonce) assert(send(fs, nonce, "Queue", id, key) == Result::Success);
    }
    uint8_t extra[32] = {33}; assert(provision(fs, extra) == Result::Full);
    assert(send(fs, 5, "Per owner full", id) == Result::Full);
    uint8_t fifth[32] = {5}; assert(send(fs, 1, "Global full", id, fifth) == Result::Full);
    use(fs); mesh::RoomMailStats stats; assert(mesh::getRoomMailStats(&fs, stats) == Result::Success && stats.owners == 32 && stats.messages == 16);
    uint8_t keys[3][32]; size_t copied;
    assert(mesh::listRoomMailOwners(&fs, stats.revision, 2, keys, 3, copied, stats) == Result::Success && copied == 3 && keys[0][0] == 3);
    assert(mesh::listRoomMailOwners(&fs, 1, 0, keys, 3, copied, stats) == Result::StaleVersion);
    assert(mesh::deleteRoomMail(&fs, OWNER, 0) == Result::Success);
    assert(mesh::getRoomMailStats(&fs, stats) == Result::Success && stats.messages == 12);
    assert(send(fs, 1, "Queue", id) == Result::Duplicate);
    assert(send(fs, 5, "Space reclaimed", id) == Result::Success); ++scenarios;
  }
  { MemoryFS fs; assert(provision(fs) == Result::Success); uint32_t live, id;
    assert(send(fs, 100, "Keep this old queued message", live) == Result::Success);
    for (uint64_t nonce = 1; nonce <= 20; ++nonce) {
      assert(send(fs, nonce, "Retired", id) == Result::Success);
      assert(mesh::deleteRoomMail(&fs, OWNER, id) == Result::Success);
      assert(send(fs, nonce, "Retired", id) == Result::Duplicate);
    }
    bodyIs(fs, OWNER, live, "Keep this old queued message");
    assert(send(fs, 100, "Keep this old queued message", id) == Result::Duplicate && id == live);
    assert(mesh::deleteRoomMail(&fs, OWNER, 0) == Result::Success && !status(fs).count); ++scenarios;
  }

  MemoryFS initial; assert(provision(initial) == Result::Success); uint32_t original_id;
  assert(send(initial, 1, OLD_BODY, original_id) == Result::Success); initial.reset();
  const auto old_primary = initial.get(P);
  const std::string old_path = "/room_mail_0.b", new_path = "/room_mail_0.a";

  // Every byte of selected owner bank and registry is protected. A corrupt
  // primary cannot be replaced by backups or uncommitted temporary images.
  for (const std::string& path : {std::string(P), old_path}) {
    const auto original = initial.get(path.c_str());
    for (size_t position = 0; position < original.size(); ++position) {
      MemoryFS fs = clone(initial); auto corrupted = original; corrupted[position] ^= 1;
      fs.put(path.c_str(), corrupted); fs.put(B, old_primary); fs.put(T, old_primary);
      preserveUnavailable(fs);
    }
  }
  { MemoryFS fs = clone(initial); auto future = fs.get(P); future[3] = 2; checksum(future); fs.put(P, future); preserveUnavailable(fs); }
  { MemoryFS fs = clone(initial); auto invalid = fs.get(old_path.c_str()); invalid[42] = 5; checksum(invalid); fs.put(old_path.c_str(), invalid); preserveUnavailable(fs); }
  { MemoryFS fs = clone(initial); auto invalid = fs.get(P); invalid[51] = 1; checksum(invalid); fs.put(P, invalid); preserveUnavailable(fs); }
  { MemoryFS fs = clone(initial); fs.files.erase(P); fs.put(B, old_primary); fs.put(T, old_primary);
    bodyIs(fs, OWNER, original_id, OLD_BODY); assert(fs.files.count(P) && !fs.files.count(B)); ++scenarios; }
  { MemoryFS fs = clone(initial); fs.files.erase(P); auto broken = old_primary; broken[0] ^= 1;
    fs.put(B, broken); fs.put(T, old_primary); preserveUnavailable(fs); }

  // Faults before publication preserve the previous committed mailbox. The
  // inactive bank may contain an orphan; it is never selected without commit.
  for (const std::string& fault : {std::string("open:w:") + new_path, std::string("write:") + new_path,
       std::string("flush:") + new_path, std::string("close:w:") + new_path,
       std::string("open:r:") + new_path, std::string("directory:") + new_path,
       std::string("open:w:") + T, std::string("write:") + T, std::string("flush:") + T,
       std::string("close:w:") + T, std::string("open:r:") + T,
       std::string("rename:") + P + ":" + B, std::string("rename:") + T + ":" + P}) {
    MemoryFS fs = clone(initial); fs.faults.insert(fault); uint32_t id;
    assert(send(fs, 2, NEW_BODY, id) == Result::WriteFailure && !id);
    fs.reset(); assert(status(fs).count == 1 && firstId(fs) == original_id); bodyIs(fs, OWNER, original_id, OLD_BODY); ++scenarios;
  }
  { MemoryFS fs = clone(initial); fs.short_write = true; uint32_t id;
    assert(send(fs, 2, NEW_BODY, id) == Result::WriteFailure && !id); fs.reset(); assert(status(fs).count == 1); ++scenarios; }
  { MemoryFS fs = clone(initial); fs.short_read = true; uint32_t id;
    assert(send(fs, 2, NEW_BODY, id) == Result::Unavailable); fs.reset(); assert(status(fs).count == 1); ++scenarios; }
  for (size_t budget = 0; budget < 1800; ++budget) {
    MemoryFS fs = clone(initial); fs.write_budget = budget; uint32_t id;
    const Result result = send(fs, 2, NEW_BODY, id); fs.reset();
    assert(result == Result::Success || result == Result::WriteFailure);
    assert(status(fs).count == (result == Result::Success ? 2 : 1)); bodyIs(fs, OWNER, original_id, OLD_BODY); ++scenarios;
  }

  // Reboot at every observable filesystem boundary of send, ACK, purge and
  // settings changes. Each recovered state is entirely old or entirely new.
  for (uint8_t operation = 0; operation < 4; ++operation) {
    auto mutate = [&](MemoryFS& fs) -> Result {
      use(fs); uint32_t id;
      if (operation == 0) return send(fs, 2, NEW_BODY, id);
      if (operation == 1) return mesh::acknowledgeRoomMail(&fs, OWNER, original_id);
      if (operation == 2) return mesh::deleteRoomMail(&fs, OWNER, 0);
      Settings settings; settings.mode = Mode::Private; settings.mailbox_only = true;
      return mesh::saveRoomMailSettings(&fs, OWNER, settings);
    };
    MemoryFS successful = clone(initial); assert(mutate(successful) == Result::Success);
    const size_t boundaries = successful.boundary;
    for (size_t cut = 1; cut <= boundaries; ++cut) {
      MemoryFS fs = clone(initial); fs.cut_at = cut;
      try { mutate(fs); } catch (const PowerCut&) {}
      fs.reset(); const Status restored = status(fs);
      if (operation == 0) {
        assert(restored.count == 1 || restored.count == 2); bodyIs(fs, OWNER, original_id, OLD_BODY);
        uint32_t id; const Result retry = send(fs, 2, NEW_BODY, id);
        assert(retry == Result::Success || retry == Result::Duplicate);
        assert(status(fs).count == 2); bodyIs(fs, OWNER, id, NEW_BODY);
      } else if (operation < 3) {
        assert(restored.count <= 1); assert(mutate(fs) == Result::Success && status(fs).count == 0);
        uint32_t id; assert(send(fs, 1, OLD_BODY, id) == Result::Duplicate && id == original_id);
      } else {
        assert(restored.count == 1 && (restored.settings.mode == Mode::Public || restored.settings.mode == Mode::Private));
        bodyIs(fs, OWNER, original_id, OLD_BODY);
        assert(mutate(fs) == Result::Success && status(fs).settings.mailbox_only);
      }
      ++scenarios;
    }
  }
  printf("%u room mailbox ownership, policy, bounded storage, retry, fault and power-cut scenarios passed\n", scenarios);
}
'''


class RoomMailStoreTest(unittest.TestCase):
    def test_actual_store_with_all_backend_modes_faults_and_powercuts(self):
        compiler = os.environ.get("CXX") or shutil.which("g++") or shutil.which("clang++")
        if compiler is None:
            self.skipTest("a host C++ compiler is required")
        filesystem = FILESYSTEM_HARNESS.split("#define FILE_O_WRITE 1")[0]
        # Real Adafruit LittleFS files are filesystem-bound and have no default
        # constructor. A permissive mock must not hide unsupported handles.
        filesystem = filesystem.replace("  File() = default;", """#if defined(NRF52_PLATFORM) || defined(STM32_PLATFORM)
  File() = delete;
#else
  File() = default;
#endif
  explicit File(MemoryFS* owner): fs(owner) {}""")
        filesystem = filesystem.replace("return {};", "return File(this);")
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
        with tempfile.TemporaryDirectory(prefix="meshcore-room-mail-") as temporary:
            work = Path(temporary)
            (work / "sys").mkdir()
            (work / "sys/stat.h").write_text(STAT_MOCK)
            (work / "SHA256.h").write_text(SHA_MOCK)
            source = work / "mail.cpp"
            for platform in (None, "NRF52_PLATFORM", "STM32_PLATFORM", "RP2040_PLATFORM", "ESP32_PLATFORM"):
                with self.subTest(platform=platform):
                    source.write_text(filesystem + (LFS_MOCK if platform in ("NRF52_PLATFORM", "STM32_PLATFORM") else "") + TESTS)
                    binary = work / (platform or "generic")
                    defines = ["-D" + platform + "=1"] if platform else []
                    built = subprocess.run([compiler, "-std=c++11", "-Wall", "-Wextra", "-Werror", *sanitizers,
                                            *defines, "-I" + str(work), "-I" + str(ROOT / "src"), str(source), "-lcrypto", "-o", str(binary)],
                                           capture_output=True, text=True, timeout=60)
                    self.assertEqual(built.returncode, 0, built.stdout + built.stderr)
                    checked = subprocess.run([str(binary)], capture_output=True, text=True, timeout=120)
                    self.assertEqual(checked.returncode, 0, checked.stdout + checked.stderr)
                    self.assertIn("room mailbox ownership, policy, bounded storage, retry, fault and power-cut scenarios passed", checked.stdout)
                    print(checked.stdout.strip())


if __name__ == "__main__":
    unittest.main()
