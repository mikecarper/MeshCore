#!/usr/bin/env python3
"""Run the actual room login handler against the actual bounded replay store."""
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

from test_replay_reset_integration import extract_braced

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "test/fixtures/client_acl_spiffs"

HARNESS = r'''
#define main existing_acl_fixture_main
#include "test_client_acl_spiffs.cpp"
#undef main
#include <helpers/ClientPathObservation.h>
#include <helpers/LazyPersistence.h>
#include <helpers/RoomAccessPolicy.h>
#include <helpers/RoomLoginAuthorization.h>
@CONSTANTS@
#define PAYLOAD_TYPE_RESPONSE 0x01
#define PAYLOAD_TYPE_ANON_REQ 0x07
namespace mesh {
// Direct ANON_REQ only: replay admission, not reply routing, is under test.
struct Packet {
  uint8_t path_len = 0, path[MAX_PATH_SIZE] = {};
  uint8_t getPayloadType() const { return PAYLOAD_TYPE_ANON_REQ; }
  bool isRouteFlood() const { return false; }
  uint8_t getPathHashSize() const { return 1; }
  static bool isValidPathLen(uint8_t length) { return length <= 63; }
};
}
struct MyMesh {
  FakeFilesystem* _fs;
  ClientACL acl;
  mesh::RoomAccessPolicy room_access;
  struct Prefs {
    char password[16] = "admin-secret";
    char guest_password[16] = "published";
    bool allow_read_only = false;
  } _prefs;
  struct Clock {
    uint32_t now = 9000;
    uint32_t getCurrentTime() { return now; }
    uint32_t getCurrentTimeUnique() { return ++now; }
  } clock;
  struct Rng { void random(uint8_t* out, size_t length) { memset(out, 0x5A, length); } } rng;
  unsigned long dirty_contacts_expiry = 0, next_push = 0;
  uint8_t contacts_save_failures = 0, reply_data[16] = {};
  unsigned replies = 0;
  mesh::Packet response;
  explicit MyMesh(FakeFilesystem* fs) : _fs(fs) {
    // Same order as MyMesh::begin().
    acl.load(fs, SELF);
    acl.protectExplicitPermissions();
    CHECK(room_access.load(fs));
  }
  Clock* getRTCClock() { return &clock; }
  Rng* getRNG() { return &rng; }
  unsigned long futureMillis(unsigned long delay) { return 1000 + delay; }
  mesh::Packet* createDatagram(uint8_t, const mesh::Identity&, const uint8_t*,
                               const uint8_t* data, size_t length) {
    CHECK(length == 13 && data[4] == RESP_SERVER_LOGIN_OK);
    ++replies;
    return &response;
  }
  mesh::Packet* createPathReturn(const mesh::Identity& sender, const uint8_t* secret,
      const uint8_t*, uint8_t, uint8_t type, const uint8_t* data, size_t length) {
    return createDatagram(type, sender, secret, data, length);
  }
  bool sendFloodReply(mesh::Packet*, unsigned long, uint8_t) { return true; }
  void sendDirect(mesh::Packet*, const uint8_t*, uint8_t, unsigned long) {}
  static bool saveFilter(ClientInfo*);
  void onAnonDataRecv(mesh::Packet*, const uint8_t*, const mesh::Identity&, uint8_t*, size_t);
};
@SAVE_FILTER@
@LOGIN_HANDLER@

static const unsigned STORE_LIMIT = mesh::MAX_CLIENT_LOGIN_REPLAY_IDENTITIES;
static mesh::Identity member(unsigned n, uint8_t tag) {
  uint8_t key[PUB_KEY_SIZE] = {};
  key[0] = tag; key[30] = uint8_t(n >> 8); key[31] = uint8_t(n);
  return mesh::Identity(key);
}
static bool login(MyMesh& room, const mesh::Identity& sender, const char* password,
                  uint32_t timestamp) {
  // The real decrypt dispatcher provides one writable terminator byte.
  std::vector<uint8_t> data(8 + strlen(password) + 1, 0xEE);
  const uint32_t sync_since = 0;
  memcpy(data.data(), &timestamp, sizeof(timestamp));
  memcpy(data.data() + 4, &sync_since, sizeof(sync_since));
  memcpy(data.data() + 8, password, strlen(password));
  mesh::Packet packet;
  const unsigned before = room.replies;
  room.onAnonDataRecv(&packet, sender.pub_key, sender, data.data(), data.size() - 1);
  return room.replies == before + 1;
}
static size_t storedIdentities(FakeFilesystem& fs) {
  const auto image = fs.files.find(PRIMARY);
  if (image == fs.files.end()) return 0;
  CHECK(validateLoginReplayFileIntegrity(&fs, PRIMARY));
  return (image->second.size() - mesh::CLIENT_LOGIN_REPLAY_TRAILER_SIZE)
      / mesh::CLIENT_LOGIN_REPLAY_RECORD_SIZE;
}

static void guestChurnCannotLockOutNewIdentities() {
  FakeFilesystem fs;
  MyMesh room(&fs);
  const auto writer = member(1, 0xA1), admin = member(2, 0xAD);
  CHECK(room.acl.applyPermissions(SELF, writer.pub_key, PUB_KEY_SIZE, PERM_ACL_READ_WRITE));
  // One fresh guest-password identity per store slot, each reconnecting
  // after its first reservation while its transient Read/Write entry is live.
  for (unsigned i = 0; i < STORE_LIMIT; ++i) {
    const auto guest = member(i, 0x6E);
    CHECK(login(room, guest, "published", 1000 + i));
    CHECK(!login(room, guest, "published", 1000 + i));
    CHECK(login(room, guest, "published", 2000 + i));
  }
  // A guest without a durable record loses its exact live floor on eviction.
  // Re-admission starts another transient writer session, whose immediate
  // replay is rejected without allocating a permanent replay-store slot.
  const auto evicted_guest = member(0, 0x6E);
  CHECK(!room.acl.getClient(evicted_guest.pub_key, PUB_KEY_SIZE));
  CHECK(storedIdentities(fs) == 0);
  CHECK(login(room, evicted_guest, "published", 1000));
  const auto* readmitted = room.acl.getClient(evicted_guest.pub_key, PUB_KEY_SIZE);
  CHECK(readmitted && readmitted->permissions == PERM_ACL_READ_WRITE
        && !readmitted->permissions_are_explicit);
  CHECK(!login(room, evicted_guest, "published", 1000));
  CHECK(storedIdentities(fs) == 0);
  // A new Admin device and an assigned writer's first login are still admitted.
  CHECK(login(room, admin, "admin-secret", 3000));
  CHECK(login(room, writer, "", 3000));
  CHECK(storedIdentities(fs) == 2);
  for (unsigned i = STORE_LIMIT; i < STORE_LIMIT + 8; ++i) {
    CHECK(login(room, member(i, 0x6E), "published", 1000 + i));
  }
  CHECK(storedIdentities(fs) == 2);
  // Their durable boundaries survive reboot, including the reserved tail.
  CHECK(room.acl.save(&fs, MyMesh::saveFilter));
  MyMesh rebooted(&fs);
  CHECK(!login(rebooted, admin, "admin-secret", 3000));
  CHECK(!login(rebooted, writer, "", 3000));
  CHECK(!login(rebooted, admin, "admin-secret", 3060));
  CHECK(!login(rebooted, writer, "", 3060));
  CHECK(login(rebooted, admin, "admin-secret", 3061));
  CHECK(login(rebooted, writer, "", 3061));
  puts("guest-password churn leaves room for new admin and assigned logins passed");
}

static void privilegedTombstoneStillBindsGuestPassword() {
  FakeFilesystem fs;
  MyMesh room(&fs);
  const auto former = member(3, 0xF0);
  CHECK(login(room, former, "admin-secret", 5000));
  CHECK(storedIdentities(fs) == 1);
  // Removing the role keeps its replay boundary for a guest-password login.
  CHECK(room.acl.applyPermissions(SELF, former.pub_key, PUB_KEY_SIZE, PERM_ACL_GUEST));
  CHECK(!room.acl.getClient(former.pub_key, PUB_KEY_SIZE));
  CHECK(!login(room, former, "published", 5000));
  CHECK(!login(room, former, "published", 5060));
  CHECK(login(room, former, "published", 5061));
  CHECK(storedIdentities(fs) == 1);
  MyMesh rebooted(&fs);
  CHECK(!login(rebooted, former, "published", 5121));
  CHECK(login(rebooted, former, "published", 5122));
  CHECK(storedIdentities(fs) == 1);
  puts("historical privileged boundary still binds guest-password logins passed");
}

static void fullPrivilegedStoreStillFailsClosed() {
  FakeFilesystem fs;
  std::vector<uint8_t> image;
  for (unsigned i = 0; i < STORE_LIMIT; ++i) {
    const auto stored = member(i, 0xB0);
    const uint32_t ceiling = 100;
    image.insert(image.end(), stored.pub_key, stored.pub_key + PUB_KEY_SIZE);
    const auto* value = reinterpret_cast<const uint8_t*>(&ceiling);
    image.insert(image.end(), value, value + sizeof(ceiling));
  }
  const uint32_t crc = mesh::updateClientLoginReplayCRC(
      0xffffffffu, image.data(), image.size()) ^ 0xffffffffu;
  image.insert(image.end(), mesh::CLIENT_LOGIN_REPLAY_MAGIC,
               mesh::CLIENT_LOGIN_REPLAY_MAGIC + 4);
  const auto* value = reinterpret_cast<const uint8_t*>(&crc);
  image.insert(image.end(), value, value + sizeof(crc));
  fs.files[PRIMARY] = image;
  MyMesh room(&fs);
  CHECK(storedIdentities(fs) == STORE_LIMIT);
  // Privileged identities keep the fail-closed policy; guests need no slot.
  CHECK(!login(room, member(999, 0xAD), "admin-secret", 7000));
  CHECK(login(room, member(999, 0x6E), "published", 7000));
  CHECK(login(room, member(0, 0xB0), "admin-secret", 7000));
  CHECK(storedIdentities(fs) == STORE_LIMIT);
  puts("full privileged store fails closed for new admins only passed");
}

int main(int argc, char** argv) {
  CHECK(argc == 2);
  if (!strcmp(argv[1], "churn")) guestChurnCannotLockOutNewIdentities();
  else if (!strcmp(argv[1], "tombstone")) privilegedTombstoneStillBindsGuestPassword();
  else if (!strcmp(argv[1], "full")) fullPrivilegedStoreStillFailsClosed();
  else CHECK(false);
}
'''


class RoomLoginReplayCapacityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        compiler = shutil.which("g++") or shutil.which("clang++")
        if compiler is None:
            raise AssertionError("a host C++ compiler is required")
        room = (ROOT / "examples/simple_room_server/MyMesh.cpp").read_text()
        room_header = (ROOT / "examples/simple_room_server/MyMesh.h").read_text()
        constants = []
        for name, source in (("RESP_SERVER_LOGIN_OK", room), ("FIRMWARE_VER_LEVEL", room),
                             ("PUSH_NOTIFY_DELAY_MILLIS", room), ("LAZY_CONTACTS_WRITE_DELAY", room),
                             ("SERVER_RESPONSE_DELAY", room_header)):
            match = re.search(r"^\s*#define\s+" + name + r"\s+.*$", source, re.MULTILINE)
            if match is None:
                raise AssertionError("production constant not found: " + name)
            constants.append(match.group().strip())
        generated = (HARNESS.replace("@CONSTANTS@", "\n".join(constants))
                     .replace("@SAVE_FILTER@", extract_braced(room, "bool MyMesh::saveFilter("))
                     .replace("@LOGIN_HANDLER@", extract_braced(room, "void MyMesh::onAnonDataRecv(")))
        cls.directory = tempfile.TemporaryDirectory(prefix="room-login-replay-capacity-")
        cls.addClassCleanup(cls.directory.cleanup)
        work = Path(cls.directory.name)
        (work / "test.cpp").write_text(generated)
        cls.binary = work / "room-login-replay-capacity"
        command = [compiler, "-std=c++17", "-O1", "-Wall", "-Wextra", "-Werror",
                   "-DESP32=1", "-DESP32_PLATFORM=1", "-DMESH_CLIENT_REPEATER_ONLY=0",
                   f"-I{FIXTURE / 'mocks'}", f"-I{FIXTURE}", f"-I{ROOT / 'src'}",
                   str(work / "test.cpp"), "-o", str(cls.binary)]
        if "g++" in Path(compiler).name:
            # The existing fixture identity has a test-only nontrivial constructor.
            command.insert(1, "-Wno-class-memaccess")
        if sys.platform.startswith("linux"):
            command[1:1] = ["-fsanitize=address,undefined", "-fno-sanitize-recover=all",
                            "-fno-pie", "-no-pie"]
        built = subprocess.run(command, capture_output=True, text=True, timeout=120)
        if built.returncode != 0:
            raise AssertionError(built.stdout + built.stderr)

    def run_case(self, case, expected):
        checked = subprocess.run([str(self.binary), case], capture_output=True, text=True, timeout=60)
        self.assertEqual(checked.returncode, 0, checked.stdout + checked.stderr)
        self.assertIn(expected, checked.stdout)

    def test_guest_password_churn_cannot_lock_out_new_admin_or_assigned_logins(self):
        self.run_case("churn", "guest-password churn leaves room for new admin and assigned logins passed")

    def test_historical_privileged_boundary_still_binds_guest_password_login(self):
        self.run_case("tombstone", "historical privileged boundary still binds guest-password logins passed")

    def test_full_privileged_store_still_fails_closed_for_new_admin(self):
        self.run_case("full", "full privileged store fails closed for new admins only passed")


if __name__ == "__main__":
    unittest.main()
