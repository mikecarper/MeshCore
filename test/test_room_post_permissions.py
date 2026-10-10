#!/usr/bin/env python3
"""Execute the production room post gate with every ACL role and replay case."""
from pathlib import Path
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
#include <cstring>
#include <cstdio>
#include "filesystem.h"
#include <helpers/RoomAccessPolicy.h>
#include <helpers/LogicalMessageCache.h>
#define TXT_TYPE_PLAIN 0
#define ROOM_MESSAGE_CACHE_SIZE 8
@ROLES@
namespace mesh {
struct Utils {
  static void sha256(uint8_t* out, size_t n, const uint8_t* key, size_t key_n,
                     const uint8_t* text, size_t text_n) {
    // Distinct fixture messages have distinct fingerprints; crypto is not the
    // behavior under test. Production LogicalMessageCache is used unchanged.
    memset(out, 0, n);
    for (size_t i = 0; i < key_n; ++i) out[i % n] ^= key[i];
    for (size_t i = 0; i < text_n; ++i) out[i % n] ^= text[i];
  }
};
}
struct ClientInfo {
  struct { uint8_t pub_key[PUB_KEY_SIZE] = {1}; } id;
  uint8_t permissions = 0;
  struct { struct { uint32_t last_post_timestamp = 0; uint16_t post_quota_used = 0, poll_quota_used = 0; } room; } extra;
};
struct MyMesh {
  MemoryFS fs;
  mesh::RoomAccessPolicy room_access;
  uint32_t ticks = 0;
  ClientInfo* active = nullptr;
  MyMesh() { metadata_filesystem = &fs; assert(room_access.load(&fs)); }
  void serviceRoomQuotas() { room_access.serviceQuotaWindow(ticks, [this] { active->extra.room.post_quota_used = active->extra.room.poll_quota_used = 0; }); }
  mesh::LogicalMessageCache<ROOM_MESSAGE_CACHE_SIZE> recent_room_posts;
  unsigned posts = 0, acks = 0;
  bool store_ok = true;
  bool addPost(ClientInfo*, const char*) { if (!store_ok) return false; ++posts; return true; }
  void receive(ClientInfo* client, const char* text, uint32_t sender_timestamp) {
    active = client;
    @IDENTITY_GATE@
    const uint8_t flags = TXT_TYPE_PLAIN;
    const size_t text_len = strlen(text);
    bool send_ack = false;
    @POST_GATE@
    if (send_ack) ++acks;
  }
};
int main() {
  // Include unrelated permission bits and unassigned roles: only RW/Admin post.
  for (unsigned permissions = 0; permissions < 256; ++permissions) {
    MyMesh mesh;
    ClientInfo client;
    client.permissions = permissions;
    const unsigned role = permissions & PERM_ACL_ROLE_MASK;
    const bool writable = role == PERM_ACL_READ_WRITE || role == PERM_ACL_ADMIN;
    mesh.receive(&client, "first", 100);
    mesh.receive(&client, "first", 100); // Exact retry: ACK again, store once.
    assert(mesh.posts == (writable ? 1U : 0U));
    assert(mesh.acks == (writable ? 2U : 0U));
    assert(client.extra.room.last_post_timestamp == (writable ? 100U : 0U));
    mesh.receive(&client, "second", 101);
    mesh.receive(&client, "first", 100); // Older exact retry after a newer post.
    assert(mesh.posts == (writable ? 2U : 0U));
    assert(mesh.acks == (writable ? 4U : 0U));
    mesh.receive(&client, "mismatch", 101);
    mesh.receive(&client, "unseen old", 99);
    assert(mesh.posts == (writable ? 2U : 0U));
    assert(mesh.acks == (writable ? 4U : 0U));
    assert(client.extra.room.last_post_timestamp == (writable ? 101U : 0U));
    if (writable) {
      client.permissions = PERM_ACL_READ_ONLY; // Revocation also blocks cached retries.
      mesh.receive(&client, "second", 101);
      assert(mesh.posts == 2 && mesh.acks == 4);
    }
  }
  {
    MyMesh mesh; ClientInfo client; client.permissions = PERM_ACL_READ_WRITE;
    assert(mesh.room_access.setRates(&mesh.fs, 1, 2));
    mesh.receive(&client, "quota-one", 100);
    assert(mesh.posts == 1 && mesh.acks == 1 && client.extra.room.post_quota_used == 1);
    mesh.receive(&client, "quota-one", 100);
    assert(mesh.posts == 1 && mesh.acks == 2 && client.extra.room.post_quota_used == 1);
    mesh.receive(&client, "quota-two", 101);
    assert(mesh.posts == 1 && mesh.acks == 2 && client.extra.room.last_post_timestamp == 100);
    mesh.ticks = 60000; mesh.receive(&client, "quota-two", 101);
    assert(mesh.posts == 2 && mesh.acks == 3 && client.extra.room.last_post_timestamp == 101);
    assert(client.extra.room.post_quota_used == 1); // Rejected post was not remembered.
    mesh.ticks = 120000; mesh.store_ok = false; mesh.receive(&client, "failed-store", 102);
    assert(mesh.posts == 2 && mesh.acks == 3 && client.extra.room.post_quota_used == 0);
    assert(client.extra.room.last_post_timestamp == 101);
    mesh.store_ok = true; mesh.receive(&client, "failed-store", 102);
    assert(mesh.posts == 3 && mesh.acks == 4 && client.extra.room.post_quota_used == 1);
    assert(mesh.room_access.addBan(&mesh.fs, client.id.pub_key) == mesh::RoomAccessPolicy::BanResult::Saved);
    mesh.receive(&client, "failed-store", 102); // Ban blocks an already cached exact retry.
    mesh.receive(&client, "banned-new", 103);
    assert(mesh.posts == 3 && mesh.acks == 4 && client.extra.room.last_post_timestamp == 102);
    assert(mesh.room_access.removeBan(&mesh.fs, client.id.pub_key) == mesh::RoomAccessPolicy::BanResult::Saved);
    mesh.ticks = 180000; mesh.receive(&client, "banned-new", 103);
    assert(mesh.posts == 4 && mesh.acks == 5);
  }
  puts("256 room permission and replay scenarios passed");
}
'''


class RoomPostPermissionTests(unittest.TestCase):
    def test_production_post_gate_and_retries(self):
        compiler = shutil.which("g++") or shutil.which("clang++")
        self.assertIsNotNone(compiler)
        source = (ROOT / "examples/simple_room_server/MyMesh.cpp").read_text()
        receive = extract_braced(source, "void MyMesh::onPeerDataRecv(")
        gate = extract_braced(receive, "if (flags == TXT_TYPE_PLAIN)")
        header = (ROOT / "src/helpers/ClientACL.h").read_text()
        roles = "\n".join(line for line in header.splitlines()
                          if line.startswith("#define PERM_ACL_"))
        identity_gate = next(line.strip() for line in receive.splitlines() if "if (!room_access.allowsIdentity(" in line)
        generated = HARNESS.replace("@POST_GATE@", gate).replace("@ROLES@", roles).replace("@IDENTITY_GATE@", identity_gate)
        with tempfile.TemporaryDirectory(prefix="room-post-permissions-") as directory:
            work = Path(directory)
            (work / "test.cpp").write_text(generated)
            binary = work / "room-posts"
            cmd = [compiler, "-std=c++17", "-Wall", "-Wextra", "-Werror", "-Wno-unused-parameter",
                   "-I" + str(ROOT / "test/fixtures/room_history_store"), "-I" + str(ROOT / "test/mocks"), "-I" + str(ROOT / "src"),
                   str(work / "test.cpp"), "-o", str(binary)]
            if sys.platform.startswith("linux"):
                cmd[1:1] = ["-fsanitize=address,undefined", "-fno-sanitize-recover=all",
                            "-fno-pie", "-no-pie"]
            built = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
            self.assertEqual(built.returncode, 0, built.stdout + built.stderr)
            tested = subprocess.run([str(binary)], capture_output=True, text=True, timeout=10)
            self.assertEqual(tested.returncode, 0, tested.stdout + tested.stderr)
            self.assertIn("256 room permission and replay scenarios passed", tested.stdout)


if __name__ == "__main__":
    unittest.main()
