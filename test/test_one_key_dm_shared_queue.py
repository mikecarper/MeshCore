#!/usr/bin/env python3
"""Exercise the real shared Companion queue methods on the host."""

from pathlib import Path
import os
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "examples/companion_radio/MyMesh.cpp"


def method(source: str, signature: str) -> str:
    start = source.index(signature)
    end = source.index("{", start) + 1
    depth = 1
    while depth:
        depth += (source[end] == "{") - (source[end] == "}")
        end += 1
    return source[start:end]


class OneKeyDMSharedQueueTest(unittest.TestCase):
    def compile_and_run(self, source: str):
        with tempfile.TemporaryDirectory(prefix="meshcore-shared-dm-") as temp:
            path = Path(temp) / "shared_dm.cpp"
            binary = Path(temp) / "shared_dm.exe"
            path.write_text(source)
            sanitizer_flags = ["-fsanitize=address,undefined"] if os.name != "nt" else []
            built = subprocess.run(
                ["c++", "-std=c++17", "-O0", *sanitizer_flags,
                 str(path), "-o", str(binary)],
                text=True, capture_output=True,
            )
            self.assertEqual(built.returncode, 0, built.stderr)
            result = subprocess.run([str(binary)], text=True, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_shared_pool_keeps_ordinary_fifo_and_held_tail_separate(self):
        source = SOURCE.read_text()
        methods = "\n\n".join(method(source, signature) for signature in (
            "bool MyMesh::Frame::isChannelMsg() const",
            "int MyMesh::getOfflineQueueCapacity() const",
            "MyMesh::Frame& MyMesh::offlineQueueFrameAt(int logical_index)",
            "MyMesh::Frame& MyMesh::heldDMFrameAt(uint8_t index)",
            "void MyMesh::removeHeldOneKeyDM(uint8_t index)",
            "bool MyMesh::addToOfflineQueue(const uint8_t frame[], int len)",
            "int MyMesh::peekOfflineQueue(uint8_t frame[])",
            "void MyMesh::popOfflineQueue()",
            "int MyMesh::getFromOfflineQueue(uint8_t frame[])",
        ))
        harness = r'''
#include <cassert>
#include <cstdint>
#include <cstring>
#define ONE_KEY_DM_SHARED_OFFLINE_QUEUE 1
#define OFFLINE_QUEUE_SIZE 256
#define MAX_FRAME_SIZE 176
#define RESP_CODE_CHANNEL_MSG_RECV 0x8c
#define RESP_CODE_CHANNEL_MSG_RECV_V3 0x90
#define RESP_CODE_CHANNEL_DATA_RECV 0x8d
#define MESH_DEBUG_PRINTLN(...) ((void)0)
class MyMesh {
public:
  struct Frame {
    uint8_t len = 0;
    uint8_t buf[MAX_FRAME_SIZE] = {};
    bool isChannelMsg() const;
  };
  struct Held { uint8_t marker = 0; };
  Frame offline_queue[OFFLINE_QUEUE_SIZE] = {};
  Held held_dms[15] = {};
  uint8_t held_dm_count = 0;
  int offline_queue_len = 0;
  int offline_queue_head = 0;
  int getOfflineQueueCapacity() const;
  Frame& offlineQueueFrameAt(int index);
  Frame& heldDMFrameAt(uint8_t index);
  void removeHeldOneKeyDM(uint8_t index);
  bool addToOfflineQueue(const uint8_t frame[], int len);
  int peekOfflineQueue(uint8_t frame[]);
  void popOfflineQueue();
  int getFromOfflineQueue(uint8_t frame[]);
};
static_assert(sizeof(MyMesh::Frame) == 177);
'''
        exercise = r'''
int main() {
  MyMesh mesh;
  uint8_t out[176] = {};
  // All 256 slots are ordinary messages when there is no held DM.
  for (int i = 0; i < 256; ++i) {
    uint8_t frame[2] = {42, static_cast<uint8_t>(i)};
    assert(mesh.addToOfflineQueue(frame, sizeof(frame)));
  }
  const uint8_t overflow = 42;
  assert(!mesh.addToOfflineQueue(&overflow, 1));
  for (int i = 0; i < 256; ++i) {
    assert(mesh.getFromOfflineQueue(out) == 2);
    assert(out[1] == static_cast<uint8_t>(i));
  }

  MyMesh channel_mesh;
  const uint8_t channel = RESP_CODE_CHANNEL_MSG_RECV;
  assert(channel_mesh.addToOfflineQueue(&channel, 1));
  for (int i = 0; i < 255; ++i) {
    uint8_t frame[2] = {42, static_cast<uint8_t>(i)};
    assert(channel_mesh.addToOfflineQueue(frame, sizeof(frame)));
  }
  assert(channel_mesh.addToOfflineQueue(&overflow, 1));
  assert(channel_mesh.offline_queue_len == 256);
  assert(channel_mesh.getFromOfflineQueue(out) == 2 && out[1] == 0);
  for (int i = 1; i < 255; ++i) {
    assert(channel_mesh.getFromOfflineQueue(out) == 2);
    assert(out[1] == static_cast<uint8_t>(i));
  }
  assert(channel_mesh.getFromOfflineQueue(out) == 1 && out[0] == overflow);

  // Held entries occupy the opposite end; sync must never expose them.
  for (int i = 0; i < 15; ++i) {
    mesh.held_dms[i].marker = static_cast<uint8_t>(i + 1);
    mesh.heldDMFrameAt(mesh.held_dm_count).buf[0] = static_cast<uint8_t>(i + 1);
    ++mesh.held_dm_count;
  }
  for (int i = 0; i < 241; ++i) {
    uint8_t frame[2] = {42, static_cast<uint8_t>(i)};
    assert(mesh.addToOfflineQueue(frame, sizeof(frame)));
  }
  assert(!mesh.addToOfflineQueue(&overflow, 1));
  for (int i = 0; i < 241; ++i) {
    assert(mesh.getFromOfflineQueue(out) == 2);
    assert(out[1] == static_cast<uint8_t>(i));
  }
  assert(mesh.getFromOfflineQueue(out) == 0);
  for (int i = 0; i < 15; ++i) {
    assert(mesh.held_dms[i].marker == i + 1);
    assert(mesh.heldDMFrameAt(i).buf[0] == i + 1);
  }

  // Removing any held DM shifts only the held tail and frees one normal slot.
  mesh.removeHeldOneKeyDM(0);
  assert(mesh.held_dm_count == 14);
  assert(mesh.held_dms[0].marker == 2);
  assert(mesh.heldDMFrameAt(0).buf[0] == 2);
  assert(mesh.addToOfflineQueue(&overflow, 1));
  assert(mesh.getFromOfflineQueue(out) == 1 && out[0] == overflow);
  for (int i = 0; i < 14; ++i) mesh.removeHeldOneKeyDM(0);
  assert(mesh.held_dm_count == 0);
  for (int i = 0; i < 256; ++i) {
    uint8_t frame[2] = {42, static_cast<uint8_t>(i)};
    assert(mesh.addToOfflineQueue(frame, sizeof(frame)));
  }
  return 0;
}
'''
        self.compile_and_run(harness + methods + exercise)

    def test_sixteenth_dm_rolls_off_and_acceptance_releases_only_last_fifteen(self):
        source = SOURCE.read_text()
        methods = "\n\n".join(method(source, signature) for signature in (
            "bool MyMesh::Frame::isChannelMsg() const",
            "int MyMesh::getOfflineQueueCapacity() const",
            "MyMesh::Frame& MyMesh::offlineQueueFrameAt(int logical_index)",
            "MyMesh::Frame& MyMesh::heldDMFrameAt(uint8_t index)",
            "void MyMesh::removeHeldOneKeyDM(uint8_t index)",
            "bool MyMesh::addToOfflineQueue(const uint8_t frame[], int len)",
            "int MyMesh::peekOfflineQueue(uint8_t frame[])",
            "void MyMesh::popOfflineQueue()",
            "int MyMesh::getFromOfflineQueue(uint8_t frame[])",
            "bool MyMesh::onAddressedTextPacket(mesh::Packet* packet, uint8_t src_hash,",
            "void MyMesh::releaseHeldOneKeyDMs()",
        ))
        harness = r'''
#include <cassert>
#include <cstdint>
#include <cstdio>
#include <cstring>
#define ONE_KEY_DM_SHARED_OFFLINE_QUEUE 1
#define OFFLINE_QUEUE_SIZE 256
#define MAX_FRAME_SIZE 176
#define MAX_PACKET_PAYLOAD 184
#define MAX_TEXT_LEN 160
#define PUB_KEY_SIZE 32
#define TXT_TYPE_PLAIN 0
#define ROUTE_TYPE_DIRECT 2
#define ROUTE_TYPE_FLOOD 1
#define RESP_CODE_CHANNEL_MSG_RECV 0x8c
#define RESP_CODE_CHANNEL_MSG_RECV_V3 0x90
#define RESP_CODE_CHANNEL_DATA_RECV 0x8d
#define MESH_DEBUG_PRINTLN(...) ((void)0)
namespace mesh {
struct Packet {
  uint8_t header = ROUTE_TYPE_FLOOD;
  uint8_t path_len = 0;
  int8_t _snr = 0;
  bool isRouteFlood() const { return header == ROUTE_TYPE_FLOOD; }
};
struct Utils {
  static int MACThenDecrypt(const uint8_t*, uint8_t* out,
                            const uint8_t* in, size_t len) {
    memcpy(out, in, len);
    return static_cast<int>(len);
  }
  static void sha256(uint8_t* out, size_t out_len, const uint8_t* in,
                     size_t in_len, const uint8_t*, size_t) {
    for (size_t i = 0; i < out_len; ++i) out[i] = in[i % in_len];
  }
};
}
struct ContactInfo { struct { uint8_t pub_key[PUB_KEY_SIZE] = {}; } id; };
struct SelfIdentity {
  void calcSharedSecret(uint8_t* secret, const uint8_t*) { memset(secret, 0, PUB_KEY_SIZE); }
};
class MyMesh {
public:
  static constexpr uint8_t MAX_HELD_ONE_KEY_DMS = 15;
  static constexpr uint8_t ONE_KEY_DM_ID_SIZE = 8;
  struct Frame {
    uint8_t len = 0;
    uint8_t buf[MAX_FRAME_SIZE] = {};
    bool isChannelMsg() const;
  };
  struct HeldOneKeyDM {
    uint8_t sender_key[PUB_KEY_SIZE];
    uint8_t id[ONE_KEY_DM_ID_SIZE];
  };
  Frame offline_queue[OFFLINE_QUEUE_SIZE] = {};
  int offline_queue_len = 0;
  int offline_queue_head = 0;
  HeldOneKeyDM held_dms[MAX_HELD_ONE_KEY_DMS] = {};
  uint8_t held_dm_count = 0;
  uint8_t verified_pending_keys[MAX_HELD_ONE_KEY_DMS][PUB_KEY_SIZE] = {};
  uint8_t verified_pending_count = 0;
  uint8_t delivered_ids[MAX_HELD_ONE_KEY_DMS][ONE_KEY_DM_ID_SIZE] = {};
  uint8_t delivered_count = 0;
  int ack_count = 0;
  bool accepted = false;
  ContactInfo contact;
  SelfIdentity self_id;
  int getOfflineQueueCapacity() const;
  Frame& offlineQueueFrameAt(int index);
  Frame& heldDMFrameAt(uint8_t index);
  void removeHeldOneKeyDM(uint8_t index);
  bool addToOfflineQueue(const uint8_t frame[], int len);
  int peekOfflineQueue(uint8_t frame[]);
  void popOfflineQueue();
  int getFromOfflineQueue(uint8_t frame[]);
  bool onAddressedTextPacket(mesh::Packet*, uint8_t, const uint8_t*, size_t);
  void releaseHeldOneKeyDMs();
  ContactInfo* lookupPersistentContactByPubKey(const uint8_t* key, size_t) {
    return accepted && memcmp(contact.id.pub_key, key, PUB_KEY_SIZE) == 0
        ? &contact : nullptr;
  }
  void forgetVerifiedPendingSender(const uint8_t*) { verified_pending_count = 0; }
  static void makeOneKeyDMId(uint8_t id[ONE_KEY_DM_ID_SIZE], uint32_t timestamp,
                             const char*) {
    memset(id, 0, ONE_KEY_DM_ID_SIZE);
    memcpy(id, &timestamp, sizeof(timestamp));
  }
  bool wasDeliveredOneKeyDM(const uint8_t*, const uint8_t id[ONE_KEY_DM_ID_SIZE]) {
    for (int i = 0; i < delivered_count; ++i)
      if (memcmp(delivered_ids[i], id, ONE_KEY_DM_ID_SIZE) == 0) return true;
    return false;
  }
  void rememberDeliveredOneKeyDM(const uint8_t*, const uint8_t id[ONE_KEY_DM_ID_SIZE]) {
    memcpy(delivered_ids[delivered_count++], id, ONE_KEY_DM_ID_SIZE);
  }
  void onMessageRecv(const ContactInfo&, mesh::Packet*, uint32_t timestamp,
                     const char* text) {
    uint8_t frame[MAX_FRAME_SIZE] = {};
    memcpy(frame, &timestamp, 4);
    memcpy(frame + 4, text, strlen(text) + 1);
    assert(addToOfflineQueue(frame, 5 + strlen(text)));
  }
  void sendAckTo(const ContactInfo&, const uint8_t*, uint8_t) { ++ack_count; }
};
'''
        exercise = r'''
int main() {
  MyMesh receiver;
  receiver.contact.id.pub_key[0] = 0x42;
  receiver.verified_pending_count = 1;
  receiver.verified_pending_keys[0][0] = 0x42;
  mesh::Packet packet;
  packet._snr = 12;
  for (uint32_t timestamp = 1; timestamp <= 16; ++timestamp) {
    uint8_t encrypted[32] = {};
    memcpy(encrypted, &timestamp, 4);
    encrypted[4] = TXT_TYPE_PLAIN << 2;
    snprintf(reinterpret_cast<char*>(encrypted + 5), 20, "msg%02lu",
             static_cast<unsigned long>(timestamp));
    assert(receiver.onAddressedTextPacket(&packet, 0x42, encrypted,
                                           5 + strlen(reinterpret_cast<char*>(encrypted + 5))));
  }
  assert(receiver.held_dm_count == 15);
  uint8_t out[MAX_FRAME_SIZE] = {};
  assert(receiver.getFromOfflineQueue(out) == 0);
  receiver.accepted = true;
  receiver.releaseHeldOneKeyDMs();
  assert(receiver.held_dm_count == 0);
  assert(receiver.verified_pending_count == 0);
  assert(receiver.offline_queue_len == 15);
  assert(receiver.ack_count == 15);
  for (uint32_t timestamp = 2; timestamp <= 16; ++timestamp) {
    int len = receiver.getFromOfflineQueue(out);
    assert(len > 5);
    uint32_t delivered_timestamp;
    memcpy(&delivered_timestamp, out, 4);
    assert(delivered_timestamp == timestamp);
    char expected[20];
    snprintf(expected, sizeof(expected), "msg%02lu", static_cast<unsigned long>(timestamp));
    assert(strcmp(reinterpret_cast<char*>(out + 4), expected) == 0);
  }
  assert(receiver.getFromOfflineQueue(out) == 0);
  return 0;
}
'''
        self.compile_and_run(harness + methods + exercise)


if __name__ == "__main__":
    unittest.main()
