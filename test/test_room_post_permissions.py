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
  struct { struct { uint32_t last_post_timestamp = 0; } room; } extra;
};
struct MyMesh {
  mesh::LogicalMessageCache<ROOM_MESSAGE_CACHE_SIZE> recent_room_posts;
  unsigned posts = 0, acks = 0;
  void addPost(ClientInfo*, const char*) { ++posts; }
  void receive(ClientInfo* client, const char* text, uint32_t sender_timestamp) {
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
        generated = HARNESS.replace("@POST_GATE@", gate).replace("@ROLES@", roles)
        with tempfile.TemporaryDirectory(prefix="room-post-permissions-") as directory:
            work = Path(directory)
            (work / "test.cpp").write_text(generated)
            binary = work / "room-posts"
            cmd = [compiler, "-std=c++17", "-Wall", "-Wextra", "-Werror", "-Wno-unused-parameter",
                   "-I" + str(ROOT / "test/mocks"), "-I" + str(ROOT / "src"),
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
