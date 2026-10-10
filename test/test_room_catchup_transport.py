#!/usr/bin/env python3
"""Execute actual Mesh cancellation and production room delivery identities."""

import hashlib
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

from test_replay_reset_integration import extract_braced

ROOT = Path(__file__).resolve().parents[1]

HARNESS = r'''
#include <cassert>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <vector>
#include <openssl/sha.h>
#include <MeshCore.h>
#include <helpers/RoomClientPathCommand.h>
@CONSTANTS@
namespace mesh {
struct Packet {};
struct Mesh {
  @DIRECT_ENTRY@;
  @FLOOD_ENTRY@;
  DirectRetryEntry _direct_retries[TOTAL_DIRECT_RETRY_SLOTS]{};
  FloodRetryEntry _flood_retries[TOTAL_FLOOD_RETRY_SLOTS]{};
  std::vector<int> direct_retired, flood_retired;
  void retireDirectRetrySlot(int index) {
    assert(_direct_retries[index].active); direct_retired.push_back(index);
    memset(&_direct_retries[index], 0, sizeof(_direct_retries[index]));
  }
  void retireFloodRetrySlot(int index) {
    assert(_flood_retries[index].active); flood_retired.push_back(index);
    memset(&_flood_retries[index], 0, sizeof(_flood_retries[index]));
  }
  bool cancelActiveMessageRetries(const uint8_t*, uint32_t);
};
@CANCEL@
}
static void hash(uint8_t* output, size_t size, const uint8_t* input, size_t length) {
  uint8_t digest[SHA256_DIGEST_LENGTH];
  assert(size <= sizeof(digest));
  SHA256(input, length, digest); memcpy(output, digest, size);
}
static void makeKey(uint8_t* output, const uint8_t* room, const uint8_t* recipient, bool topic) {
  mesh::roomDeliveryRetryKey(output, MAX_HASH_SIZE, room, recipient, topic, hash);
}
static void identityKeys() {
  uint8_t room[32], recipient[32]; memset(room, 1, 32); memset(recipient, 2, 32);
  uint8_t primary[MAX_HASH_SIZE], topic[MAX_HASH_SIZE], other_room[MAX_HASH_SIZE], other_user[MAX_HASH_SIZE];
  makeKey(primary, room, recipient, false); makeKey(topic, room, recipient, true);
  const uint8_t expected[] = {@EXPECTED_KEY@};
  assert(memcmp(primary, expected, sizeof(expected)) == 0);
  room[31] = 3; makeKey(other_room, room, recipient, false);
  room[31] = 1; recipient[31] = 3; makeKey(other_user, room, recipient, false);
  assert(memcmp(primary, topic, MAX_HASH_SIZE) != 0);
  assert(memcmp(primary, other_room, MAX_HASH_SIZE) != 0);
  assert(memcmp(primary, other_user, MAX_HASH_SIZE) != 0);
  recipient[31] = 2; uint8_t repeated[MAX_HASH_SIZE]; makeKey(repeated, room, recipient, false);
  assert(memcmp(primary, repeated, MAX_HASH_SIZE) == 0);
  puts("full room and recipient identities and topic namespace match SHA256 reference passed");
}
template <typename Entry>
static void seed(Entry& entry, const uint8_t* key, uint32_t timestamp, bool recognized = true) {
  entry.active = true; entry.message_timestamp = timestamp;
  entry.has_message_replacement_key = recognized;
  memcpy(entry.message_replacement_key, key, MAX_HASH_SIZE);
}
static void cancellationCases() {
  uint8_t room[32], user[32]; memset(room, 1, 32); memset(user, 2, 32);
  uint8_t primary[MAX_HASH_SIZE], topic[MAX_HASH_SIZE], other[MAX_HASH_SIZE];
  makeKey(primary, room, user, false); makeKey(topic, room, user, true);
  user[31] ^= 1; makeKey(other, room, user, false);
  mesh::Mesh mesh;
  for (int profile = 0; profile < 2; ++profile) {
    const int d = profile * MAX_DIRECT_RETRY_SLOTS, f = profile * MAX_FLOOD_RETRY_SLOTS;
    seed(mesh._direct_retries[d], primary, 500); seed(mesh._flood_retries[f], primary, 500);
    seed(mesh._direct_retries[d + 1], primary, 501); seed(mesh._flood_retries[f + 1], primary, 501);
    seed(mesh._direct_retries[d + 2], topic, 500); seed(mesh._flood_retries[f + 2], topic, 500);
    seed(mesh._direct_retries[d + 3], other, 500); seed(mesh._flood_retries[f + 3], other, 500);
    seed(mesh._direct_retries[d + 4], primary, 500, false); seed(mesh._flood_retries[f + 4], primary, 500, false);
    mesh._direct_retries[d].queued = profile == 0;
    mesh._flood_retries[f].waiting_final_echo = profile == 1;
  }
  assert(!mesh.cancelActiveMessageRetries(nullptr, 500));
  assert(!mesh.cancelActiveMessageRetries(primary, 499));
  const auto before = mesh;
  assert(mesh.cancelActiveMessageRetries(primary, 500));
  assert(mesh.direct_retired.size() == 2 && mesh.flood_retired.size() == 2);
  for (int profile = 0; profile < 2; ++profile) {
    const int d = profile * MAX_DIRECT_RETRY_SLOTS, f = profile * MAX_FLOOD_RETRY_SLOTS;
    assert(!mesh._direct_retries[d].active && !mesh._flood_retries[f].active);
    for (int offset = 1; offset < MAX_DIRECT_RETRY_SLOTS; ++offset) {
      assert(memcmp(&mesh._direct_retries[d + offset], &before._direct_retries[d + offset],
                    sizeof(mesh._direct_retries[0])) == 0);
    }
    for (int offset = 1; offset < MAX_FLOOD_RETRY_SLOTS; ++offset) {
      assert(memcmp(&mesh._flood_retries[f + offset], &before._flood_retries[f + offset],
                    sizeof(mesh._flood_retries[0])) == 0);
    }
  }
  assert(!mesh.cancelActiveMessageRetries(primary, 500));
  // Retirement clears the slot's key. An input alias must survive all matches.
  mesh::Mesh alias;
  seed(alias._direct_retries[0], primary, 500);
  seed(alias._direct_retries[MAX_DIRECT_RETRY_SLOTS], primary, 500);
  seed(alias._flood_retries[0], primary, 500);
  seed(alias._flood_retries[MAX_FLOOD_RETRY_SLOTS], primary, 500);
  assert(alias.cancelActiveMessageRetries(alias._direct_retries[0].message_replacement_key, 500));
  assert(alias.direct_retired.size() == 2 && alias.flood_retired.size() == 2);
  puts("actual cancellation retires one delivery across profiles and preserves every unrelated retry passed");
}
int main(int argc, char** argv) {
  assert(argc == 2);
  if (!strcmp(argv[1], "keys")) identityKeys();
  else if (!strcmp(argv[1], "cancel")) cancellationCases();
  else assert(false);
}
'''


def production_source(cancel_override=None):
    source = (ROOT / "src/Mesh.cpp").read_text()
    header = (ROOT / "src/Mesh.h").read_text()
    constants = []
    for name in ("MAX_DIRECT_RETRY_SLOTS", "MAX_FLOOD_RETRY_SLOTS", "TOTAL_DIRECT_RETRY_SLOTS", "TOTAL_FLOOD_RETRY_SLOTS"):
        found = re.search(r"^\s*#define\s+" + name + r"\s+.*$", header, re.MULTILINE)
        if not found:
            raise AssertionError("production retry capacity not found: " + name)
        constants.append(found[0])
    digest = hashlib.sha256(b"room\x00" + bytes([1]) * 32 + bytes([2]) * 32).digest()[:8]
    replacements = {
        "@CONSTANTS@": "\n".join(constants),
        "@DIRECT_ENTRY@": extract_braced(header, "struct DirectRetryEntry {"),
        "@FLOOD_ENTRY@": extract_braced(header, "struct FloodRetryEntry {"),
        "@CANCEL@": cancel_override or extract_braced(source, "bool Mesh::cancelActiveMessageRetries("),
        "@EXPECTED_KEY@": ",".join("0x%02X" % byte for byte in digest),
    }
    generated = HARNESS
    for marker, value in replacements.items():
        generated = generated.replace(marker, value)
    return generated


class RoomCatchUpTransportTests(unittest.TestCase):
    @classmethod
    def build(cls, name, source):
        work = Path(cls.directory.name)
        path = work / (name + ".cpp"); path.write_text(source, encoding="ascii")
        binary = work / name
        command = [cls.compiler, "-std=c++17", "-O1", "-Wall", "-Wextra", "-Werror", "-Wno-unused-parameter",
                   "-I" + str(ROOT / "src"), str(path), "-lcrypto", "-o", str(binary)]
        if sys.platform.startswith("linux"):
            command[1:1] = ["-fsanitize=address,undefined", "-fno-sanitize-recover=all",
                            "-fno-omit-frame-pointer", "-fno-pie", "-no-pie"]
        checked = subprocess.run(command, capture_output=True, text=True, timeout=60)
        if checked.returncode:
            raise AssertionError(checked.stdout + checked.stderr)
        return binary

    @classmethod
    def setUpClass(cls):
        cls.compiler = shutil.which("g++") or shutil.which("clang++")
        if not cls.compiler:
            raise AssertionError("a host C++17 compiler is required")
        cls.directory = tempfile.TemporaryDirectory(prefix="room-catchup-transport-")
        cls.addClassCleanup(cls.directory.cleanup)
        cls.binary = cls.build("production", production_source())

    def run_case(self, name, expected):
        checked = subprocess.run([str(self.binary), name], capture_output=True, text=True, timeout=15)
        self.assertEqual(checked.returncode, 0, checked.stdout + checked.stderr)
        self.assertIn(expected, checked.stdout)

    def test_actual_domain_key_covers_full_room_recipient_and_topic_namespace(self):
        self.run_case("keys", "full room and recipient identities and topic namespace match SHA256 reference passed")

    def test_actual_mesh_cancellation_key_timestamp_kind_and_both_profiles(self):
        self.run_case("cancel", "actual cancellation retires one delivery across profiles and preserves every unrelated retry passed")

    def test_negative_controls_detect_broad_timestamp_and_key_cancellation(self):
        source = (ROOT / "src/Mesh.cpp").read_text()
        cancel = extract_braced(source, "bool Mesh::cancelActiveMessageRetries(")
        for kind, before in (("timestamp", ".message_timestamp == message_timestamp"),
                             ("recognition", ".has_message_replacement_key")):
            with self.subTest(control=kind):
                self.assertEqual(cancel.count(before), 2)
                changed = cancel.replace(before, before + " || true")
                # Preserve expression grouping while deliberately relaxing just this gate.
                for table in ("_direct_retries[i]", "_flood_retries[i]"):
                    changed = changed.replace(table + before + " || true", "(" + table + before + " || true)")
                binary = self.build("negative-" + kind, production_source(changed))
                checked = subprocess.run([str(binary), "cancel"], capture_output=True, text=True, timeout=15)
                self.assertNotEqual(checked.returncode, 0, "negative control missed " + kind)
                self.assertIn("Assertion", checked.stderr, checked.stdout + checked.stderr)


if __name__ == "__main__":
    unittest.main()
