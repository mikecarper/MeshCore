#!/usr/bin/env python3
"""Run the actual room ACL filter and ClientACL storage/eviction policy."""
from pathlib import Path
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
#include <helpers/RoomAccessPolicy.h>
struct MyMesh { static bool saveFilter(ClientInfo*); };
@SAVE_FILTER@
static mesh::Identity keyed(unsigned n) {
  uint8_t key[PUB_KEY_SIZE] = {};
  key[0] = 0x49; key[29] = uint8_t(n >> 16); key[30] = uint8_t(n >> 8); key[31] = uint8_t(n);
  return mesh::Identity(key);
}
static void roomAssignedAclSurvivesReboot() {
  FakeFilesystem fs;
  ClientACL acl; acl.load(&fs, SELF); acl.protectExplicitPermissions();
  for (unsigned role = 1; role <= 7; ++role) {
    const auto id = keyed(role);
    CHECK(acl.applyPermissions(SELF, id.pub_key, PUB_KEY_SIZE, uint8_t(role | 0x80)));
    auto* client = acl.getClient(id.pub_key, PUB_KEY_SIZE);
    CHECK(client && client->permissions_are_explicit && MyMesh::saveFilter(client));
    client->last_activity = 1000 + role;
    CHECK(acl.authorizeLoginTimestamp(id.pub_key, 100 + role, 0, uint8_t(role)));
    client->last_timestamp = 100 + role;
    client->extra.room.sync_since = 40 + role;
#if !MESH_CLIENT_REPEATER_ONLY
    client->extra.room.post_quota_used = 6; client->extra.room.poll_quota_used = 5;
#endif
  }
  const auto guest = keyed(20), writer = keyed(21), admin = keyed(22);
  CHECK(acl.putClient(guest, PERM_ACL_GUEST));
  auto* auto_rw = acl.putClient(writer, PERM_ACL_READ_WRITE);
  auto* auto_admin = acl.putClient(admin, PERM_ACL_ADMIN);
  CHECK(auto_rw && auto_admin);
  CHECK(!auto_rw->permissions_are_explicit && !MyMesh::saveFilter(auto_rw));
  CHECK(!auto_admin->permissions_are_explicit && MyMesh::saveFilter(auto_admin));
  SELF.calcSharedSecret(auto_admin->shared_secret, admin.pub_key);
  CHECK(acl.save(&fs, MyMesh::saveFilter));
  const auto contacts = fs.files["/s_contacts"];
  ClientACL restored; restored.load(&fs, SELF); restored.protectExplicitPermissions();
  CHECK(restored.getNumClients() == 8);
  CHECK(!restored.getClient(guest.pub_key, PUB_KEY_SIZE));
  CHECK(!restored.getClient(writer.pub_key, PUB_KEY_SIZE));
  for (unsigned role = 1; role <= 7; ++role) {
    const auto id = keyed(role);
    auto* client = restored.getClient(id.pub_key, PUB_KEY_SIZE);
    CHECK(client && client->permissions == uint8_t(role | 0x80));
    CHECK(client->permissions_are_explicit && client->extra.room.sync_since == 40 + role);
    if (role == PERM_ACL_READ_ONLY) {
      // Existing RO-only sessions deliberately do not allocate durable login ceilings.
      CHECK(client->last_timestamp == 0);
      CHECK(!restored.authorizeLoginTimestamp(id.pub_key, 100 + role, 100 + role, uint8_t(role)));
    } else {
      CHECK(client->last_timestamp >= 160 + role);
      CHECK(!restored.authorizeLoginTimestamp(id.pub_key, 100 + role, client->last_timestamp, uint8_t(role)));
    }
#if !MESH_CLIENT_REPEATER_ONLY
    CHECK(client->extra.room.post_quota_used == 0 && client->extra.room.poll_quota_used == 0);
#endif
  }
  CHECK(restored.getClient(admin.pub_key, PUB_KEY_SIZE)->permissions_are_explicit);
  // The marker is not a new wire/storage flag and an unchanged save is byte-identical.
  CHECK(restored.save(&fs, MyMesh::saveFilter));
  CHECK(fs.files["/s_contacts"] == contacts);
}
static void roomPinsOnlyExplicitAssignments() {
  FakeFilesystem fs; ClientACL acl; acl.load(&fs, SELF); acl.protectExplicitPermissions();
  for (unsigned i = 0; i < MAX_CLIENTS; ++i) {
    const auto id = keyed(i + 1);
    CHECK(acl.applyPermissions(SELF, id.pub_key, PUB_KEY_SIZE, i % 2 ? PERM_ACL_READ_ONLY : PERM_ACL_READ_WRITE));
    acl.getClient(id.pub_key, PUB_KEY_SIZE)->last_activity = i + 1;
  }
  CHECK(!acl.putClient(keyed(100), PERM_ACL_GUEST));
  CHECK(acl.getNumClients() == MAX_CLIENTS);
  // setperm0 stays removal, creating one ephemeral login-cache slot.
  const auto removed = keyed(1);
  CHECK(acl.applyPermissions(SELF, removed.pub_key, PUB_KEY_SIZE, 0));
  CHECK(!acl.getClient(removed.pub_key, PUB_KEY_SIZE));
  auto* ephemeral = acl.putClient(keyed(100), PERM_ACL_READ_WRITE);
  CHECK(ephemeral && !ephemeral->permissions_are_explicit);
#if !MESH_CLIENT_REPEATER_ONLY
  ephemeral->extra.room.post_quota_used = ephemeral->extra.room.poll_quota_used = 9;
#endif
  CHECK(acl.putClient(keyed(101), PERM_ACL_GUEST));
  CHECK(!acl.getClient(keyed(100).pub_key, PUB_KEY_SIZE));
  for (unsigned i = 2; i <= MAX_CLIENTS; ++i) CHECK(acl.getClient(keyed(i).pub_key, PUB_KEY_SIZE));
#if !MESH_CLIENT_REPEATER_ONLY
  CHECK(acl.getClient(keyed(101).pub_key, PUB_KEY_SIZE)->extra.room.post_quota_used == 0);
#endif
  // Other roles keep the existing default policy: explicit RO is still evictable.
  FakeFilesystem other_fs; ClientACL other; other.load(&other_fs, SELF);
  for (unsigned i = 0; i < MAX_CLIENTS; ++i) {
    const auto id = keyed(i + 1);
    CHECK(other.applyPermissions(SELF, id.pub_key, PUB_KEY_SIZE, PERM_ACL_READ_ONLY));
    other.getClient(id.pub_key, PUB_KEY_SIZE)->last_activity = i + 1;
  }
  CHECK(other.putClient(keyed(999), PERM_ACL_GUEST));
  CHECK(!other.getClient(keyed(1).pub_key, PUB_KEY_SIZE));
  // Opt-out is deliberate and reversible; no persistence-format migration.
  acl.protectExplicitPermissions(false);
  CHECK(acl.putClient(keyed(102), PERM_ACL_GUEST));
}
int main() {
  roomAssignedAclSurvivesReboot(); roomPinsOnlyExplicitAssignments();
  puts("actual room assigned ACL persistence, replay, eviction, and other-role policy passed");
}
'''


class RoomAclPersistenceTests(unittest.TestCase):
    def test_actual_acl_filter_storage_and_opt_in_pressure_protection(self):
        compiler = shutil.which("g++") or shutil.which("clang++")
        self.assertIsNotNone(compiler)
        room = (ROOT / "examples/simple_room_server/MyMesh.cpp").read_text()
        generated = HARNESS.replace("@SAVE_FILTER@", extract_braced(room, "bool MyMesh::saveFilter("))
        with tempfile.TemporaryDirectory(prefix="room-assigned-acl-") as directory:
            work = Path(directory)
            (work / "test.cpp").write_text(generated)
            for compact in (0, 1):
                with self.subTest(repeater_only=compact):
                    binary = work / ("acl-" + str(compact))
                    command = [compiler, "-std=c++17", "-O1", "-Wall", "-Wextra", "-Werror",
                               "-DESP32=1", "-DESP32_PLATFORM=1", f"-DMESH_CLIENT_REPEATER_ONLY={compact}",
                               f"-I{FIXTURE / 'mocks'}", f"-I{FIXTURE}", f"-I{ROOT / 'src'}",
                               str(work / "test.cpp"), "-o", str(binary)]
                    if "g++" in Path(compiler).name:
                        # The existing fixture identity has a test-only nontrivial constructor.
                        command.insert(1, "-Wno-class-memaccess")
                    if sys.platform.startswith("linux"):
                        command[1:1] = ["-fsanitize=address,undefined", "-fno-sanitize-recover=all", "-fno-pie", "-no-pie"]
                    built = subprocess.run(command, capture_output=True, text=True, timeout=60)
                    self.assertEqual(built.returncode, 0, built.stdout + built.stderr)
                    checked = subprocess.run([str(binary)], capture_output=True, text=True, timeout=20)
                    self.assertEqual(checked.returncode, 0, checked.stdout + checked.stderr)
                    self.assertIn("actual room assigned ACL persistence, replay, eviction, and other-role policy passed", checked.stdout)


if __name__ == "__main__":
    unittest.main()
