#!/usr/bin/env python3
"""Run the room ban/quota policy and its real store against faults and reboots."""

from pathlib import Path
import os
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest
import zlib

from test_room_topic_store import HARNESS as FILESYSTEM_HARNESS, STAT_MOCK
from test_replay_reset_integration import extract_braced

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
#include <array>
#define HEX 16 // Arduino's numeric-format macro must remain usable.
#include <helpers/RoomAccessPolicy.h>
static_assert(HEX == 16, "room policy must preserve Arduino HEX");
using Policy = mesh::RoomAccessPolicy;
using BanResult = Policy::BanResult;
static const char* P = mesh::ROOM_ACCESS_PRIMARY_PATH;
static const char* T = mesh::ROOM_ACCESS_TEMP_PATH;
static const char* B = mesh::ROOM_ACCESS_BACKUP_PATH;
static const std::vector<uint8_t> OLD = { @OLD_VECTOR@ };
static const std::vector<uint8_t> NEW = { @NEW_VECTOR@ };
static unsigned scenarios = 0;
static std::array<uint8_t, 32> key(uint8_t suffix) {
  std::array<uint8_t, 32> result;
  result.fill(0xab); result.back() = suffix; return result;
}
static std::string hex(const uint8_t* key) {
  static const char digits[] = "0123456789abcdef";
  std::string result;
  for (size_t i = 0; i < 32; ++i) { result += digits[key[i] >> 4]; result += digits[key[i] & 15]; }
  return result;
}
static bool load(Policy& policy, MemoryFS& fs) {
  metadata_filesystem = &fs;
  const bool okay = policy.load(&fs);
  assert(fs.lock_depth == 0); return okay;
}
static void loaded(MemoryFS& fs, unsigned post, unsigned poll, bool banned = true) {
  Policy rebooted; assert(load(rebooted, fs));
  assert(rebooted.healthy() && rebooted.postRate() == post && rebooted.pollRate() == poll);
  assert(rebooted.isBanned(key(1).data()) == banned);
  assert(rebooted.allowsIdentity(key(1).data()) == !banned);
  assert(rebooted.allowsIdentity(key(2).data()));
}
static void rechecksum(std::vector<uint8_t>& image) {
  uint32_t sum = 0xffffffffu;
  for (size_t i = 0; i + 4 < image.size(); ++i) {
    sum ^= image[i];
    for (unsigned bit = 0; bit < 8; ++bit) sum = (sum >> 1) ^ ((sum & 1) ? 0xedb88320u : 0);
  }
  sum ^= 0xffffffffu;
  for (unsigned i = 0; i < 4; ++i) image[image.size() - 4 + i] = uint8_t(sum >> (8 * i));
}
static mesh::RoomAccessSettings nextSettings() {
  mesh::RoomAccessSettings result;
  result.posts_per_minute = 5; result.polls_per_minute = 7; result.ban_count = 1;
  memcpy(result.bans[0], key(1).data(), 32); return result;
}
static void ordinaryFault(MemoryFS& fs) {
  Policy policy; assert(load(policy, fs));
  assert(!policy.setRates(&fs, 5, 7));
  assert(policy.healthy() && policy.postRate() == 2 && policy.pollRate() == 3);
  assert(!policy.allowsIdentity(key(1).data()) && policy.allowsIdentity(key(2).data()));
  fs.reset(); loaded(fs, 2, 3); assert(fs.get(P) == OLD);
  assert(!fs.files.count(T) && !fs.files.count(B)); ++scenarios;
}

static void admissionAndBans() {
  MemoryFS fs; Policy policy;
  assert(!policy.healthy() && !policy.allowsIdentity(key(1).data()));
  uint16_t count = 0; assert(!policy.consumePost(count) && count == 0);
  assert(!policy.load(static_cast<MemoryFS*>(nullptr)));
  assert(load(policy, fs) && policy.postRate() == 0 && policy.pollRate() == 0);
  assert(!policy.allowsIdentity(nullptr)); uint8_t zero[32] = {};
  assert(!policy.allowsIdentity(zero) && !policy.isBanned(nullptr));
  assert(policy.addBan(&fs, nullptr) == BanResult::InvalidKey);
  assert(policy.addBan(&fs, zero) == BanResult::InvalidKey && fs.write_opens == 0);
  assert(policy.addBan(&fs, key(1).data()) == BanResult::Saved);
  assert(!policy.allowsIdentity(key(1).data()) && policy.allowsIdentity(key(2).data()));
  // Keys share their entire first31 bytes: prefixes cannot broaden a ban.
  for (uint8_t suffix = 2; suffix < 34; ++suffix) assert(policy.allowsIdentity(key(suffix).data()));
  const unsigned writes = fs.write_opens;
  assert(policy.addBan(&fs, key(1).data()) == BanResult::Unchanged && fs.write_opens == writes);
  assert(policy.removeBan(&fs, key(2).data()) == BanResult::Unchanged && fs.write_opens == writes);
  Policy rebooted; assert(load(rebooted, fs)); assert(rebooted.banCount() == 1);
  assert(!rebooted.allowsIdentity(key(1).data()));
  for (uint8_t suffix = 2; suffix <= 32; ++suffix) {
    assert(policy.addBan(&fs, key(suffix).data()) == BanResult::Saved);
  }
  assert(policy.banCount() == 32);
  const auto full = fs.get(P); const unsigned full_writes = fs.write_opens;
  assert(policy.addBan(&fs, key(33).data()) == BanResult::Full);
  assert(fs.get(P) == full && fs.write_opens == full_writes && policy.allowsIdentity(key(33).data()));
  for (uint8_t suffix : {uint8_t(32), uint8_t(1), uint8_t(16)}) {
    assert(policy.removeBan(&fs, key(suffix).data()) == BanResult::Saved);
    assert(policy.allowsIdentity(key(suffix).data()));
  }
  assert(policy.banCount() == 29);
  assert(policy.banAt(29) == nullptr && policy.banAt(size_t(-1)) == nullptr);
  assert(policy.addBan(&fs, key(33).data()) == BanResult::Saved);
  Policy final; assert(load(final, fs));
  assert(final.banCount() == 30 && !final.allowsIdentity(key(33).data()));
  assert(final.allowsIdentity(key(1).data()) && final.allowsIdentity(key(16).data())); ++scenarios;
}

static void aclPersistenceAndQuotas() {
  for (unsigned permissions = 0; permissions < 256; ++permissions) {
    assert(mesh::roomAclShouldPersist(permissions, true, 7, 3));
    assert(mesh::roomAclShouldPersist(permissions, false, 7, 3) == ((permissions & 7) == 3));
  }
  MemoryFS fs; Policy policy; assert(load(policy, fs));
  struct Counts { uint16_t posts = 0, polls = 0; } users[2];
  unsigned resets = 0;
  const auto reset = [&] { ++resets; for (auto& u : users) u.posts = u.polls = 0; };
  policy.serviceQuotaWindow(1000, reset); assert(resets == 1);
  users[0].posts = users[0].polls = UINT16_MAX;
  assert(policy.consumePost(users[0].posts) && policy.consumeKeepAlive(users[0].polls));
  assert(users[0].posts == UINT16_MAX && users[0].polls == UINT16_MAX); // Disabled is free.
  assert(policy.setRates(&fs, 2, 3));
  policy.serviceQuotaWindow(2000, reset); assert(resets == 2);
  for (unsigned n = 0; n < 2; ++n) assert(policy.consumePost(users[0].posts));
  assert(!policy.consumePost(users[0].posts) && users[0].posts == 2);
  for (unsigned n = 0; n < 3; ++n) assert(policy.consumeKeepAlive(users[0].polls));
  assert(!policy.consumeKeepAlive(users[0].polls) && users[0].polls == 3);
  assert(policy.consumePost(users[1].posts) && users[1].posts == 1); // Actual separate identity state.
  policy.serviceQuotaWindow(61999, reset); assert(resets == 2 && users[0].posts == 2);
  policy.serviceQuotaWindow(62000, reset); assert(resets == 3 && users[0].posts == 0);
  assert(policy.addBan(&fs, key(1).data()) == BanResult::Saved);
  assert(policy.consumePost(users[1].posts));
  policy.serviceQuotaWindow(62001, reset); assert(resets == 3 && users[1].posts == 1); // Ban doesn't reset quotas.
  assert(policy.setRates(&fs, 2, 3)); policy.serviceQuotaWindow(62002, reset);
  assert(resets == 3); // Idempotent config cannot renew a user's budget.
  assert(policy.setRates(&fs, UINT16_MAX, UINT16_MAX));
  policy.serviceQuotaWindow(UINT32_MAX - 30000U, reset); const unsigned before_wrap = resets;
  users[0].posts = users[0].polls = UINT16_MAX - 1;
  assert(policy.consumePost(users[0].posts) && users[0].posts == UINT16_MAX);
  assert(!policy.consumePost(users[0].posts));
  assert(policy.consumeKeepAlive(users[0].polls) && users[0].polls == UINT16_MAX);
  assert(!policy.consumeKeepAlive(users[0].polls));
  policy.serviceQuotaWindow(29998, reset); assert(resets == before_wrap);
  policy.serviceQuotaWindow(29999, reset); assert(resets == before_wrap + 1);
  assert(users[0].posts == 0 && users[0].polls == 0);
  policy.serviceQuotaWindow(UINT32_C(0x80007530), reset); assert(resets == before_wrap + 2);
  loaded(fs, UINT16_MAX, UINT16_MAX);
  ++scenarios;
}

static void cliCases() {
  MemoryFS fs; Policy policy; assert(load(policy, fs)); char reply[160];
  const auto run = [&](const std::string& command) {
    memset(reply, 0xa5, sizeof(reply));
    assert(policy.handleConfig(&fs, command.c_str(), reply, 157));
    assert(uint8_t(reply[157]) == 0xa5);
  };
  run("get room.post.rate"); assert(!strcmp(reply, "> 0/min (0=off)"));
  run("set room.post.rate 12"); assert(!strncmp(reply, "OK", 2));
  run("set room.poll.rate 7"); assert(policy.postRate() == 12 && policy.pollRate() == 7);
  run("get room.post.rate "); assert(!strcmp(reply, "> 12/min (0=off)"));
  run("get room.poll.rate"); assert(!strcmp(reply, "> 7/min (0=off)"));
  const unsigned saved = fs.write_opens;
  for (const char* rate : {"", "-1", "+1", "65536", "9999999999999999999999", "1/min", "1 2", "1.0", "1;2"}) {
    run(std::string("set room.post.rate ") + rate); assert(!strncmp(reply, "Err", 3));
    assert(policy.postRate() == 12 && fs.write_opens == saved);
  }
  uint8_t parsed[32]; memset(parsed, 0xcc, sizeof(parsed));
  const std::string first = hex(key(1).data()), second = hex(key(2).data());
  for (const std::string& bad : {first.substr(0,8), first.substr(0,12), first.substr(0,63), first + "0", std::string("all"), std::string(64,'0'), std::string(64,'g')}) {
    assert(!Policy::parseFullKey(bad.c_str(), parsed));
    for (uint8_t value : parsed) assert(value == 0xcc);
    run("room.ban " + bad); assert(!strncmp(reply, "Err", 3) && policy.banCount() == 0);
  }
  assert(!Policy::parseFullKey(nullptr, parsed));
  std::string uppercase = first; for (char& c : uppercase) if (c >= 'a' && c <= 'f') c -= 'a' - 'A';
  assert(Policy::parseFullKey(uppercase.c_str(), parsed) && memcmp(parsed,key(1).data(),32) == 0);
  run("room.ban " + first); assert(policy.banCount() == 1);
  run("room.ban " + second); assert(policy.banCount() == 2);
  run("room.ban " + hex(key(3).data())); assert(policy.banCount() == 3);
  run("get room.bans"); assert(std::string(reply) == "Room bans 1/2\n" + first + "\n" + second);
  run("get room.bans 2"); assert(std::string(reply) == "Room bans 2/2\n" + hex(key(3).data()));
  for (const char* page : {"0", "3", "65536", "-1", "1 2", "abc"}) {
    run(std::string("get room.bans ") + page); assert(!strncmp(reply, "Err", 3));
  }
  char tiny[65]; memset(tiny,0xa5,sizeof(tiny)); policy.formatBanPage(1,tiny,64);
  assert(!strncmp(tiny,"Err",3) && uint8_t(tiny[64]) == 0xa5);
  assert(!policy.handleConfig(&fs,"room.ban-other",reply,157));
  assert(!policy.handleConfig(&fs,"get room.bans-other",reply,157));
  assert(!policy.handleConfig(&fs,"set room.poll.rate-other",reply,157));
  assert(!policy.handleConfig(&fs,nullptr,reply,157));
  assert(!policy.handleConfig(&fs,"get room.bans",nullptr,157));
  run("room.unban " + first); assert(policy.allowsIdentity(key(1).data()));
  run("room.unban " + first); assert(!strncmp(reply,"OK",2));
  ++scenarios;
}

static void persistenceAndFaults() {
  { MemoryFS fs; fs.put(P,OLD); loaded(fs,2,3);
    Policy policy; assert(load(policy,fs)); assert(policy.setRates(&fs,5,7));
    assert(fs.get(P) == NEW); loaded(fs,5,7); ++scenarios; }
  { mesh::RoomAccessSettings bad; bad.ban_count = 33; MemoryFS fs;
    assert(mesh::room_access_detail::save(&fs,bad) == mesh::room_access_detail::SaveResult::WriteFailure);
    bad.ban_count = 1;
    assert(mesh::room_access_detail::save(&fs,bad) == mesh::room_access_detail::SaveResult::WriteFailure);
    memcpy(bad.bans[0],key(1).data(),32); memcpy(bad.bans[1],key(1).data(),32); bad.ban_count = 2;
    assert(mesh::room_access_detail::save(&fs,bad) == mesh::room_access_detail::SaveResult::WriteFailure);
    assert(fs.trace.empty()); ++scenarios; }
  std::vector<std::vector<uint8_t>> invalid = {{}, {1}, std::vector<uint8_t>(15), OLD};
  invalid.back().push_back(0);
  auto future = OLD; future[3] = 2; rechecksum(future); invalid.push_back(future);
  auto reserved = OLD; reserved[10] = 1; rechecksum(reserved); invalid.push_back(reserved);
  auto too_many = OLD; too_many[8] = 33; rechecksum(too_many); invalid.push_back(too_many);
  auto zero_key = OLD; std::fill(zero_key.begin()+12,zero_key.end()-4,0); rechecksum(zero_key); invalid.push_back(zero_key);
  auto duplicate = OLD; duplicate.insert(duplicate.end()-4,OLD.begin()+12,OLD.end()-4);
  duplicate[8] = 2; rechecksum(duplicate); invalid.push_back(duplicate);
  for (size_t byte = 0; byte < OLD.size(); ++byte) {
    auto corrupt = OLD; corrupt[byte] ^= 1; invalid.push_back(corrupt);
  }
  for (const auto& bytes : invalid) {
    MemoryFS fs; fs.put(P,bytes); fs.put(B,OLD); fs.put(T,NEW); Policy policy;
    assert(!load(policy,fs) && !policy.healthy());
    assert(!policy.allowsIdentity(key(2).data())); uint16_t count = 0;
    assert(!policy.consumePost(count) && !policy.consumeKeepAlive(count) && count == 0);
    assert(!policy.setRates(&fs,5,7) && policy.addBan(&fs,key(2).data()) == BanResult::StorageFailure);
    assert(fs.get(P) == bytes && fs.get(B) == OLD && fs.get(T) == NEW && fs.write_opens == 0); ++scenarios;
  }
  { MemoryFS fs; fs.put(B,OLD); fs.put(T,NEW); loaded(fs,2,3);
    assert(fs.get(P) == OLD && !fs.files.count(T)); ++scenarios; }
  { MemoryFS fs; fs.put(T,NEW); loaded(fs,0,0,false);
    assert(!fs.files.count(P) && !fs.files.count(T)); ++scenarios; }
  for (const std::string& fault : {std::string("open:w:")+T,std::string("write:")+T,
      std::string("flush:")+T,std::string("close:w:")+T,std::string("open:r:")+T,
      std::string("read:")+T,std::string("size:")+T,std::string("rename:")+P+":"+B,
      std::string("rename:")+T+":"+P,std::string("directory:")+T}) {
    MemoryFS fs; fs.put(P,OLD); fs.faults.insert(fault); ordinaryFault(fs);
  }
  { MemoryFS fs; fs.put(P,OLD); Policy policy; assert(load(policy,fs)); fs.short_write = true;
    assert(!policy.setRates(&fs,5,7) && policy.postRate() == 2); fs.reset(); loaded(fs,2,3); ++scenarios; }
  { MemoryFS fs; fs.put(P,OLD); Policy policy; assert(load(policy,fs)); fs.replacement_on_close = OLD;
    assert(!policy.setRates(&fs,5,7) && policy.postRate() == 2); fs.reset(); loaded(fs,2,3); ++scenarios; }
  { MemoryFS fs; fs.put(P,OLD); Policy policy; assert(load(policy,fs));
    fs.faults.insert(std::string("open:r:")+P);
    assert(!policy.setRates(&fs,5,7) && !policy.healthy() && !policy.allowsIdentity(key(2).data()));
    assert(fs.write_opens == 0 && fs.get(P) == OLD); fs.reset(); assert(load(policy,fs)); ++scenarios; }
  { MemoryFS fs; fs.put(P,OLD); Policy policy; assert(load(policy,fs));
    fs.faults.insert(std::string("rename:")+T+":"+P);
    fs.faults.insert(std::string("rename:")+B+":"+P);
    fs.faults.insert(std::string("remove:")+T);
    assert(!policy.setRates(&fs,5,7) && policy.postRate() == 2);
    assert(!fs.files.count(P) && fs.get(B) == OLD && fs.get(T) == NEW);
    fs.reset(); loaded(fs,2,3); ++scenarios; }
  { MemoryFS fs; fs.put(P,OLD); Policy policy; assert(load(policy,fs));
    fs.faults.insert(std::string("remove:")+B);
    assert(policy.setRates(&fs,5,7) && policy.postRate() == 5 && fs.get(P) == NEW);
    loaded(fs,5,7); assert(!policy.setRates(&fs,2,3));
    fs.reset(); loaded(fs,5,7); ++scenarios; }
#if defined(NRF52_PLATFORM) || defined(STM32_PLATFORM) || defined(ESP32_PLATFORM)
  for (bool present : {false,true}) {
    MemoryFS fs; if (present) fs.put(P,OLD);
    fs.faults.insert(std::string("stat:")+P); Policy policy;
    assert(!load(policy,fs) && !policy.allowsIdentity(key(1).data()));
    assert(!policy.setRates(&fs,5,7) && fs.write_opens == 0);
    assert(fs.files.count(P) == size_t(present)); ++scenarios;
  }
#endif
  for (bool existing : {false,true}) {
    MemoryFS baseline; if (existing) baseline.put(P,OLD); metadata_filesystem = &baseline;
    assert(mesh::room_access_detail::save(&baseline,nextSettings()) == mesh::room_access_detail::SaveResult::Saved);
    const size_t boundaries = baseline.boundary;
    for (size_t cut = 1; cut <= boundaries; ++cut) {
      MemoryFS fs; if (existing) fs.put(P,OLD); fs.cut_at = cut; metadata_filesystem = &fs;
      try { mesh::room_access_detail::save(&fs,nextSettings()); } catch (const PowerCut&) {}
      const bool committed = fs.files.count(P) && fs.get(P) == NEW;
      fs.reset(); fs.lock_depth = 0; // Power loss also releases the abandoned mount lock.
      loaded(fs,committed ? 5 : existing ? 2 : 0, committed ? 7 : existing ? 3 : 0,committed || existing);
      assert(!fs.files.count(T) && !fs.files.count(B));
      if (committed || existing) assert(fs.get(P) == (committed ? NEW : OLD));
      else assert(!fs.files.count(P));
      ++scenarios;
    }
  }
}
int main() {
  admissionAndBans(); aclPersistenceAndQuotas(); cliCases(); persistenceAndFaults();
  printf("PASS: %u room access identity/quota/storage/power-cut scenarios\n",scenarios);
}
'''


def image(post, poll, bans):
    payload = b"RAP\x01" + struct.pack("<HHB3x", post, poll, len(bans)) + b"".join(bans)
    return payload + struct.pack("<I", zlib.crc32(payload))


class RoomAccessPolicyTests(unittest.TestCase):
    def test_actual_client_layout_has_no_32bit_ram_growth(self):
        compiler = shutil.which("arm-none-eabi-g++")
        if compiler is None:
            packages = Path.home() / ".platformio/packages"
            compiler = next((str(path) for path in sorted(packages.glob("toolchain-gccarmnoneeabi*/bin/arm-none-eabi-g++"))), None)
        if compiler is None:
            if os.environ.get("MESHCORE_REQUIRE_ARM_COMPILER") == "1":
                self.fail("ARM compiler required for actual 32-bit room client layout proof")
            self.skipTest("ARM compiler not installed")
        header = (ROOT / "src/helpers/ClientACL.h").read_text()
        current = extract_braced(header, "struct ClientInfo {")
        self.assertIn("bool permissions_are_explicit;", current)
        self.assertIn("uint16_t post_quota_used;", current)
        self.assertIn("uint16_t poll_quota_used;", current)
        prior = current.replace("struct ClientInfo {", "struct PriorClientInfo {")
        prior = re.sub(r"^\s*(?:bool permissions_are_explicit|uint16_t (?:post|poll)_quota_used);[^\n]*$",
                       "", prior, flags=re.MULTILINE)
        roles = "\n".join(line for line in header.splitlines() if line.startswith("#define PERM_ACL_"))
        source = """
#include <stdint.h>
#include <stddef.h>
#define PUB_KEY_SIZE 32
#define MAX_PATH_SIZE 64
namespace mesh { struct Identity { uint8_t pub_key[PUB_KEY_SIZE]; }; }
""" + roles + "\n" + prior + ";\n" + current + ";\n" + """
static_assert(sizeof(void*) == 4 && sizeof(unsigned long) == 4, "target ABI must be 32-bit");
static_assert(sizeof(ClientInfo) == sizeof(PriorClientInfo), "room counters/explicit marker must use existing padding");
static_assert(sizeof(ClientInfo) <= (MESH_CLIENT_REPEATER_ONLY ? 284 : 320), "existing client RAM bound must hold");
#if !MESH_CLIENT_REPEATER_ONLY
static_assert(sizeof(((ClientInfo*)0)->extra.room) <= sizeof(((ClientInfo*)0)->extra.sensor), "room counters must fit existing sensor union");
#endif
"""
        with tempfile.TemporaryDirectory(prefix="room-access-layout-") as temporary:
            work = Path(temporary)
            cpp = work / "layout.cpp"
            cpp.write_text(source)
            for repeater_only in (0, 1):
                with self.subTest(repeater_only=repeater_only):
                    compiled = subprocess.run([compiler, "-std=c++11", "-Wall", "-Wextra", "-Werror",
                                               "-DMESH_CLIENT_REPEATER_ONLY=" + str(repeater_only),
                                               "-fsyntax-only", str(cpp)], capture_output=True, text=True, timeout=30)
                    self.assertEqual(compiled.returncode, 0, compiled.stdout + compiled.stderr)

    def test_production_policy_store_cli_and_faults_on_all_backends(self):
        compiler = os.environ.get("CXX") or shutil.which("g++") or shutil.which("clang++")
        self.assertIsNotNone(compiler)
        filesystem = FILESYSTEM_HARNESS[:FILESYSTEM_HARNESS.index("#define FILE_O_WRITE")]
        # Reuse the existing power-cut filesystem without changing that suite.
        # Native LittleFS metadata probes also prove the lock is released.
        default_lock = ("  void _lockFS() {}\n"
                        "  void _unlockFS() {}\n"
                        "  MemoryFS* _getFS() { return this; }\n")
        self.assertIn(default_lock, filesystem)
        filesystem = filesystem.replace(default_lock,
                                        "  int lock_depth = 0;\n"
                                        "  void _lockFS() { assert(lock_depth++ == 0); }\n"
                                        "  void _unlockFS() { assert(--lock_depth == 0); }\n"
                                        "  MemoryFS* _getFS() { return this; }\n", 1)
        tests = TESTS
        key = bytes([0xAB] * 31 + [1])
        for marker, settings in (("@OLD_VECTOR@", (2, 3, [key])), ("@NEW_VECTOR@", (5, 7, [key]))):
            tests = tests.replace(marker, ",".join(str(byte) for byte in image(*settings)))
        sanitizers = (["-fsanitize=address,undefined", "-fno-sanitize-recover=all", "-fno-pie", "-no-pie"]
                      if sys.platform.startswith("linux") else [])
        with tempfile.TemporaryDirectory(prefix="room-access-policy-") as temporary:
            work = Path(temporary)
            (work / "sys").mkdir()
            (work / "sys/stat.h").write_text(STAT_MOCK)
            source = work / "access.cpp"
            for platform in (None, "NRF52_PLATFORM", "STM32_PLATFORM", "RP2040_PLATFORM", "ESP32_PLATFORM"):
                with self.subTest(platform=platform):
                    source.write_text(filesystem + (LFS_MOCK if platform in ("NRF52_PLATFORM", "STM32_PLATFORM") else "") + tests)
                    binary = work / (platform or "generic")
                    defines = ["-D" + platform + "=1"] if platform else []
                    built = subprocess.run([compiler, "-std=c++11", "-O1", "-Wall", "-Wextra", "-Werror",
                                            *sanitizers, *defines, "-I" + str(work), "-I" + str(ROOT / "src"),
                                            str(source), "-o", str(binary)],
                                           capture_output=True, text=True, timeout=60)
                    self.assertEqual(built.returncode, 0, built.stdout + built.stderr)
                    checked = subprocess.run([str(binary)], capture_output=True, text=True, timeout=30)
                    self.assertEqual(checked.returncode, 0, checked.stdout + checked.stderr)
                    self.assertIn("room access identity/quota/storage/power-cut scenarios", checked.stdout)
                    print((platform or "generic") + ": " + checked.stdout.strip())


if __name__ == "__main__":
    unittest.main()
