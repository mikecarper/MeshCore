#!/usr/bin/env python3
"""Exercise actual Companion tracker callbacks and the shared wire codec."""

from pathlib import Path
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

from test_replay_reset_integration import extract_braced

ROOT = Path(__file__).resolve().parents[1]
SANITIZERS = (["-fsanitize=address,undefined", "-fno-sanitize-recover=all",
               "-fno-pie", "-no-pie"] if sys.platform.startswith("linux") else [])

HARNESS = r'''
#include <cassert>
#include <cmath>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <climits>
#include <vector>
#include "examples/companion_radio/CompanionFeatures.h"
#include <helpers/TrackerProtocol.h>
#if MESH_ENABLE_LOST_REPLY
#include <helpers/CompanionLostReply.h>
#endif
#define PUB_KEY_SIZE 32
#define ADV_TYPE_CHAT 1
#define ADV_TYPE_REPEATER 2
#define ADV_TYPE_ROOM 3
#define ADV_TYPE_SENSOR 4
#define REQ_TYPE_GET_TELEMETRY_DATA 3
#define TELEM_MODE_ALLOW_FLAGS 1
#define TELEM_MODE_ALLOW_ALL 2
#define TELEM_PERM_BASE 1
#define TELEM_PERM_LOCATION 2
#define TELEM_PERM_ENVIRONMENT 4
#define TELEM_CHANNEL_SELF 1
using std::isnan;

struct ContactInfo {
  struct Identity { uint8_t pub_key[32] = {}; } id;
  uint8_t type = ADV_TYPE_SENSOR, flags = 0;
  bool transient = false;
  int32_t gps_lat = 100, gps_lon = 200;
  uint32_t last_advert_timestamp = 900, lastmod = 800;
};
struct Clock { uint32_t now = 0; uint32_t getMillis() const { return now; } };
struct RTC { uint32_t now = 1000; uint32_t getCurrentTime() const { return now; } };
struct Board {
  uint16_t getBattMilliVolts() { return 3700; }
  float getMCUTemperature() { return NAN; }
};
struct Sensors { void querySensors(uint8_t, ...) {} };
struct Telemetry {
  void reset() {} void addVoltage(uint8_t, float) {} void addTemperature(uint8_t, float) {}
  uint8_t getSize() { return 0; } const uint8_t* getBuffer() { return nullptr; }
};
class MyMesh {
public:
  struct Prefs {
    uint8_t lost_reply = 0, telemetry_mode_base = 0;
    uint8_t telemetry_mode_loc = 0, telemetry_mode_env = 0;
  } _prefs;
  Clock clock; Clock* _ms = &clock; RTC rtc;
  Board board; Sensors sensors; Telemetry telemetry;
  std::vector<ContactInfo> contacts;
  bool mutable_contacts = true;
  unsigned notifications = 0, queries = 0;
  uint32_t user_ack_table[8] = {11,22,33,44,55,66,77,88};
  uint32_t user_ack_timeout = 7654321;
#if MESH_ENABLE_LOST_REPLY
  mesh::companion::LostReplyLimiter lost_reply_limiter;
  uint8_t handleTrackerStatusRequest(const ContactInfo&, uint32_t, const uint8_t*, uint8_t, uint8_t*);
#endif
  uint8_t onContactRequest(const ContactInfo&, uint32_t, const uint8_t*, uint8_t, uint8_t*);
  bool isTransientContact(const ContactInfo& contact) { return contact.transient; }
  bool canMutateContacts() { return mutable_contacts; }
  RTC* getRTCClock() { return &rtc; }
  ContactInfo* lookupPersistentContactByPubKey(const uint8_t* key, size_t length) {
    assert(length == 32);
    ++queries;
    for (auto& contact : contacts) {
      if (!contact.transient && memcmp(contact.id.pub_key, key, 32) == 0) return &contact;
    }
    return nullptr;
  }
  void onDiscoveredContact(ContactInfo& contact, bool added, uint8_t length, const uint8_t* path) {
    assert(!added && length == 0 && path == nullptr);
    assert(contact.lastmod == rtc.now);
    ++notifications;
  }
  ContactInfo& saved(uint8_t key = 1) {
    contacts.emplace_back();
    contacts.back().id.pub_key[0] = key;
    return contacts.back();
  }
};
@METHODS@

static mesh::tracker::StatusRequest report(bool gps = false) {
  mesh::tracker::StatusRequest value;
  value.battery_mv = 3712;
  if (gps) {
    value.flags = mesh::tracker::FreshGps;
    value.latitude = 471234567;
    value.longitude = -1222345678;
  }
  return value;
}

static uint8_t query(MyMesh& mesh, const ContactInfo& peer, uint32_t tag,
                     const mesh::tracker::StatusRequest& value,
                     uint8_t expected, bool padded = true) {
  uint8_t bytes[28] = {};
  assert(mesh::tracker::makeStatusRequest(value, bytes, sizeof(bytes)) == 13);
  uint8_t answer[18]; memset(answer, 0xa5, sizeof(answer));
  uint32_t ack_before[8]; memcpy(ack_before, mesh.user_ack_table, sizeof(ack_before));
  const uint32_t timeout_before = mesh.user_ack_timeout;
  const auto prefs_before = mesh._prefs;
  const uint8_t length = mesh.onContactRequest(peer, tag, bytes,
                                              padded ? 28 : 13, answer + 1);
  assert(length == expected && answer[0] == 0xa5);
  for (size_t i = 1 + length; i < sizeof(answer); ++i) assert(answer[i] == 0xa5);
  assert(memcmp(ack_before, mesh.user_ack_table, sizeof(ack_before)) == 0);
  assert(mesh.user_ack_timeout == timeout_before);
  assert(memcmp(&prefs_before, &mesh._prefs, sizeof(prefs_before)) == 0);
  if (length) {
    uint8_t status = 99; uint16_t awake = 999;
    assert(mesh::tracker::parseStatusResponse(answer + 1, length, tag, status, awake));
    const uint8_t expected_status = mesh._prefs.lost_reply <= 2 ? mesh._prefs.lost_reply : 0;
    assert(status == expected_status && awake == (status == 2 ? 120 : 0));
  }
  return length;
}

static void codec() {
  const auto original = report(true);
  uint8_t bytes[40] = {};
  assert(mesh::tracker::makeStatusRequest(original, bytes, sizeof(bytes)) == 13);
  assert(bytes[0] == 0x0c && bytes[1] == 1 && bytes[2] == 1);
  assert(bytes[11] == 0x80 && bytes[12] == 0x0e);
  for (size_t length = 0; length <= sizeof(bytes); ++length) {
    mesh::tracker::StatusRequest parsed;
    parsed.latitude = 77;
    const bool valid = mesh::tracker::parseStatusRequest(bytes, length, parsed);
    assert(valid == (length == 13 || length == 28));
    if (valid) {
      assert(parsed.flags == original.flags && parsed.latitude == original.latitude);
      assert(parsed.longitude == original.longitude && parsed.battery_mv == original.battery_mv);
    } else assert(parsed.latitude == 77);
  }
  mesh::tracker::StatusRequest parsed;
  assert(!mesh::tracker::parseStatusRequest(nullptr, 28, parsed));
  for (size_t i = 13; i < 28; ++i) {
    bytes[i] = 1;
    assert(!mesh::tracker::parseStatusRequest(bytes, 28, parsed));
    bytes[i] = 0;
  }
  for (uint8_t flags : {uint8_t(3), uint8_t(4), uint8_t(255)}) {
    bytes[2] = flags;
    assert(!mesh::tracker::parseStatusRequest(bytes, 13, parsed));
  }
  for (int32_t bad : {INT32_MIN, INT32_MAX, -900000001, 900000001}) {
    auto value = original; value.latitude = bad;
    assert(mesh::tracker::makeStatusRequest(value, bytes, sizeof(bytes)) == 0);
    assert(mesh::tracker::makeStatusRequest(original, bytes, sizeof(bytes)) == 13);
    mesh::tracker::put32(bytes + 3, uint32_t(bad));
    assert(!mesh::tracker::parseStatusRequest(bytes, 13, parsed));
  }
  for (int32_t bad : {INT32_MIN, INT32_MAX, -1800000001, 1800000001}) {
    auto value = original; value.longitude = bad;
    assert(mesh::tracker::makeStatusRequest(value, bytes, sizeof(bytes)) == 0);
    assert(mesh::tracker::makeStatusRequest(original, bytes, sizeof(bytes)) == 13);
    mesh::tracker::put32(bytes + 7, uint32_t(bad));
    assert(!mesh::tracker::parseStatusRequest(bytes, 13, parsed));
  }
  for (int32_t edge : {-900000000, 900000000}) {
    auto value = original; value.latitude = edge;
    assert(mesh::tracker::makeStatusRequest(value, bytes, sizeof(bytes)) == 13);
    assert(mesh::tracker::parseStatusRequest(bytes, 13, parsed) && parsed.latitude == edge);
  }
  auto no_fix = report(); no_fix.flags = mesh::tracker::GpsAttemptFailed;
  assert(mesh::tracker::makeStatusRequest(no_fix, bytes, sizeof(bytes)) == 13);
  no_fix.latitude = 1;
  assert(mesh::tracker::makeStatusRequest(no_fix, bytes, sizeof(bytes)) == 0);
  assert(mesh::tracker::makeStatusRequest(original, nullptr, 40) == 0);
  assert(mesh::tracker::makeStatusRequest(original, bytes, 12) == 0);

  uint8_t answer[32] = {};
  assert(mesh::tracker::makeStatusResponse(0x87654321, 2, 120, answer, 32) == 9);
  assert(answer[0] == 0x21 && answer[1] == 0x43 && answer[2] == 0x65 && answer[3] == 0x87);
  assert(answer[4] == 12 && answer[5] == 1 && answer[6] == 2 && answer[7] == 120 && answer[8] == 0);
  for (size_t length = 0; length <= sizeof(answer); ++length) {
    uint8_t status = 99; uint16_t awake = 999;
    const bool valid = mesh::tracker::parseStatusResponse(answer, length, 0x87654321, status, awake);
    assert(valid == (length == 9 || length == 16));
    assert(status == (valid ? 2 : 99) && awake == (valid ? 120 : 999));
  }
  uint8_t status = 99; uint16_t awake = 999;
  assert(!mesh::tracker::parseStatusResponse(answer, 16, 0x87654322, status, awake));
  for (size_t i = 9; i < 16; ++i) {
    answer[i] = 1;
    assert(!mesh::tracker::parseStatusResponse(answer, 16, 0x87654321, status, awake));
    answer[i] = 0;
  }
  for (size_t index : {size_t(4), size_t(5), size_t(6)}) {
    const uint8_t saved = answer[index]; answer[index] = 255;
    assert(!mesh::tracker::parseStatusResponse(answer, 9, 0x87654321, status, awake));
    answer[index] = saved;
  }
  assert(!mesh::tracker::parseStatusResponse(nullptr, 16, 1, status, awake));
  assert(mesh::tracker::makeStatusResponse(1, 3, 0, answer, 32) == 0);
  assert(mesh::tracker::makeStatusResponse(1, 2, 120, answer, 8) == 0);
  assert(mesh::tracker::makeStatusResponse(1, 2, 120, nullptr, 32) == 0);
}

static void callbacks() {
#if MESH_ENABLE_LOST_REPLY
  for (uint8_t state : {uint8_t(0), uint8_t(1), uint8_t(2), uint8_t(255)}) {
    for (uint8_t type : {uint8_t(ADV_TYPE_SENSOR), uint8_t(ADV_TYPE_CHAT)}) {
      MyMesh mesh; auto& peer = mesh.saved(); peer.type = type; mesh._prefs.lost_reply = state;
      query(mesh, peer, 1000, report(), 9);
      assert(mesh.notifications == 0 && peer.gps_lat == 100 && peer.gps_lon == 200);
    }
  }
  for (uint8_t type : {uint8_t(0), uint8_t(ADV_TYPE_ROOM), uint8_t(ADV_TYPE_REPEATER), uint8_t(255)}) {
    MyMesh mesh; auto& peer = mesh.saved(); peer.type = type;
    query(mesh, peer, 1000, report(true), 0);
  }
  {
    MyMesh mesh; auto& peer = mesh.saved(); peer.transient = true;
    query(mesh, peer, 1000, report(true), 0);
    peer.transient = false;
    ContactInfo unknown = peer; unknown.id.pub_key[31] = 1;
    query(mesh, unknown, 1000, report(true), 0);
    assert(mesh.notifications == 0);
  }
  {
    MyMesh mesh; auto& peer = mesh.saved(); mesh._prefs.lost_reply = 2;
    uint8_t bytes[40] = {};
    assert(mesh::tracker::makeStatusRequest(report(true), bytes, 40) == 13);
    uint8_t answer[16]; memset(answer, 0xa5, sizeof(answer));
    assert(mesh.onContactRequest(peer, 1000, nullptr, 13, answer) == 0);
    assert(mesh.onContactRequest(peer, 1000, bytes, 0, answer) == 0);
    assert(mesh.onContactRequest(peer, 1000, bytes, 13, nullptr) == 0);
    for (uint8_t length : {uint8_t(1), uint8_t(12), uint8_t(14), uint8_t(27), uint8_t(29), uint8_t(40)})
      assert(mesh.onContactRequest(peer, 1000, bytes, length, answer) == 0);
    bytes[1] = 2;
    assert(mesh.onContactRequest(peer, 1000, bytes, 28, answer) == 0); bytes[1] = 1;
    bytes[13] = 1;
    assert(mesh.onContactRequest(peer, 1000, bytes, 28, answer) == 0); bytes[13] = 0;
    assert(mesh.notifications == 0);
    query(mesh, peer, 1000, report(true), 9);
    assert(peer.gps_lat == 47123456 && peer.gps_lon == -122234567);
    assert(peer.last_advert_timestamp == 1000 && peer.lastmod == 1000 && mesh.notifications == 1);
    query(mesh, peer, 1000, report(true), 0); // Same logical request cannot produce a second reply.
    mesh.clock.now = 60000;
    query(mesh, peer, 1000, report(true), 0); // Exact retry remains suppressed beyond cooldown.
    query(mesh, peer, 1001, report(true), 9, false);
    assert(peer.last_advert_timestamp == 1001 && mesh.notifications == 2);
  }
  for (uint32_t tag : {uint32_t(699), uint32_t(700), uint32_t(1300), uint32_t(1301), UINT32_MAX}) {
    MyMesh mesh; auto& peer = mesh.saved(); peer.last_advert_timestamp = 1;
    query(mesh, peer, tag, report(true), 9);
    const bool accepted = tag >= 700 && tag <= 1300;
    assert(mesh.notifications == (accepted ? 1U : 0U));
    assert(peer.last_advert_timestamp == (accepted ? tag : 1));
  }
  for (uint32_t floor : {uint32_t(999), uint32_t(1000), uint32_t(1001)}) {
    MyMesh mesh; auto& peer = mesh.saved(); peer.last_advert_timestamp = floor;
    query(mesh, peer, 1000, report(true), 9);
    assert(mesh.notifications == (floor < 1000 ? 1U : 0U));
    assert(peer.last_advert_timestamp == (floor < 1000 ? 1000 : floor));
  }
  {
    MyMesh mesh; auto& peer = mesh.saved(); mesh.mutable_contacts = false;
    query(mesh, peer, 1000, report(true), 9);
    assert(mesh.notifications == 0 && peer.last_advert_timestamp == 900);
  }
  {
    MyMesh mesh; mesh.contacts.reserve(2);
    auto& first = mesh.saved(1); auto& second = mesh.saved(2);
    query(mesh, first, 1000, report(), 9);
    query(mesh, second, 1000, report(), 0);
    mesh.clock.now = 10000;
    query(mesh, second, 1000, report(), 9);
    mesh.clock.now = 59999;
    query(mesh, first, 1001, report(), 0);
    mesh.clock.now = 60000;
    query(mesh, first, 1001, report(), 9);
  }
#else
  MyMesh mesh; auto& peer = mesh.saved(); mesh._prefs.lost_reply = 2;
  query(mesh, peer, 1000, report(true), 0);
  assert(mesh.notifications == 0 && peer.last_advert_timestamp == 900);
#endif
}

int main() {
  codec(); callbacks();
  puts("PASS: tracker owner production callback and wire codec");
}
'''


class TrackerOwnerResponseTests(unittest.TestCase):
    def test_actual_owner_callback_and_codec(self):
        compiler = os.environ.get("CXX") or shutil.which("g++") or shutil.which("clang++")
        if compiler is None:
            self.skipTest("a host C++ compiler is required")
        source = (ROOT / "examples/companion_radio/MyMesh.cpp").read_text()
        methods = extract_braced(source, "uint8_t MyMesh::onContactRequest(")
        methods += "\n#if MESH_ENABLE_LOST_REPLY\n" + extract_braced(
            source, "uint8_t MyMesh::handleTrackerStatusRequest(") + "\n#endif\n"
        with tempfile.TemporaryDirectory(prefix="meshcore-tracker-owner-") as directory:
            work = Path(directory)
            cpp = work / "owner.cpp"
            cpp.write_text(HARNESS.replace("@METHODS@", methods), encoding="ascii")
            for flags in ([], ["-DSTM32_PLATFORM=1"],
                          ["-DSTM32_PLATFORM=1", "-DMESH_ENABLE_LOST_REPLY=1"],
                          ["-DMESH_ENABLE_LOST_REPLY=0"]):
                with self.subTest(flags=flags):
                    binary = work / "owner"
                    built = subprocess.run([
                        compiler, "-std=c++11", "-Wall", "-Wextra", "-Werror",
                        "-Wno-unused-parameter", *SANITIZERS, *flags,
                        "-I", str(ROOT / "src"), "-I", str(ROOT),
                        str(cpp), "-o", str(binary),
                    ], text=True, capture_output=True, timeout=60)
                    self.assertEqual(built.returncode, 0, built.stdout + built.stderr)
                    ran = subprocess.run([str(binary)], text=True, capture_output=True, timeout=20)
                    self.assertEqual(ran.returncode, 0, ran.stdout + ran.stderr)
                    self.assertIn("PASS: tracker owner production callback and wire codec", ran.stdout)


if __name__ == "__main__":
    unittest.main()
