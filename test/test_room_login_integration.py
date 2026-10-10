#!/usr/bin/env python3
"""Execute the real room login/replay handlers against controlled storage faults."""

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
#include <algorithm>
#include "filesystem.h"
#include <helpers/RoomAccessPolicy.h>
#include <helpers/RemoteCliReplyCache.h>
#include <helpers/RemoteCliRequest.h>
#include <helpers/ClientACLResponse.h>
#include <helpers/ClientPathPersistence.h>
#include <helpers/ClientPathObservation.h>
#include <helpers/RoomClientPathCommand.h>
#include <helpers/RoomCatchUp.h>
#include <helpers/TxtDataHelpers.h>
#include <Packet.h>
#include <helpers/RoomLoginAuthorization.h>
#include <helpers/ClientLoginPersistence.h>
#include <helpers/LazyPersistence.h>
#include <helpers/LogicalMessageCache.h>
@CONSTANTS@
@STR_HELPER@
#define MESH_CLIENT_REPEATER_ONLY 0
namespace mesh {
struct Identity {
  uint8_t pub_key[PUB_KEY_SIZE];
  bool matches(const Identity& other) const {
    return memcmp(pub_key, other.pub_key, PUB_KEY_SIZE) == 0;
  }
};
struct Utils {
  static void sha256(uint8_t* out, size_t n, const uint8_t* data, size_t length) {
    sha256(out, n, data, length, nullptr, 0);
  }
  static void sha256(uint8_t* out, size_t n, const uint8_t* key, size_t key_n,
                     const uint8_t* text, size_t text_n) {
    // Posting authorization and replay are tested here, not the hash primitive.
    memset(out, 0, n);
    for (size_t i = 0; i < key_n; ++i) out[i % n] ^= key[i];
    for (size_t i = 0; i < text_n; ++i) out[i % n] ^= text[i];
  }
};
@PACKET_CONSTRUCTOR@
@PACKET_PATH_CHECK@
}
@CLIENT_INFO@;
@POST_INFO@;

struct ReplayStorage {
  uint8_t key[PUB_KEY_SIZE] = {};
  bool found = false, read_ok = true, write_ok = true;
  uint32_t ceiling = 0;
  unsigned reads = 0, writes = 0;
};
static bool readClientLoginReplayCeiling(
    ReplayStorage* fs, const uint8_t* key, uint32_t* ceiling, bool* found) {
  ++fs->reads;
  if (!fs->read_ok) return false;
  *found = fs->found && memcmp(fs->key, key, PUB_KEY_SIZE) == 0;
  *ceiling = *found ? fs->ceiling : 0;
  return true;
}
static bool writeClientLoginReplayCeiling(
    ReplayStorage* fs, const uint8_t* key, uint32_t ceiling,
    mesh::ClientLoginReplayReservationAction action) {
  ++fs->writes;
  assert(action != mesh::ClientLoginReplayReservationAction::None);
  if (!fs->write_ok) return false;
  memcpy(fs->key, key, PUB_KEY_SIZE);
  fs->ceiling = ceiling;
  fs->found = true;
  return true;
}
struct ClientACL {
  ReplayStorage storage;
  ReplayStorage* _fs = &storage;
  bool login_replay_store_available = true, acl_load_complete = true;
  bool authorizeLoginTimestamp(const uint8_t*, uint32_t, uint32_t, uint8_t);
};
@REPLAY_HANDLER@

struct FakeACL : ClientACL {
  ClientInfo client{};
  bool known = false, allocate_ok = true;
  unsigned lookups = 0, authorizations = 0, allocations = 0;
  uint8_t authorized_role = 255;
  bool save(MemoryFS* fs, bool (*filter)(ClientInfo*)) {
    assert(fs != nullptr && filter != nullptr);
    return true;
  }
  int getNumClients() const { return known ? 1 : 0; }
  ClientInfo* getClientByIdx(int i) { assert(i == 0 && known); return &client; }
  ClientInfo* getClient(const uint8_t* key, size_t length) {
    ++lookups;
    assert(length == PUB_KEY_SIZE);
    return known && memcmp(client.id.pub_key, key, length) == 0 ? &client : nullptr;
  }
  bool authorizeLoginTimestamp(const uint8_t* key, uint32_t timestamp,
                               uint32_t floor, uint8_t role) {
    ++authorizations;
    authorized_role = role;
    return ClientACL::authorizeLoginTimestamp(key, timestamp, floor, role);
  }
  ClientInfo* putClient(const mesh::Identity& sender, uint8_t permissions) {
    ++allocations;
    assert(permissions == 0 && !known);
    if (!allocate_ok) return nullptr;
    client = ClientInfo{};
    client.id = sender;
    client.permissions = permissions;
    client.out_path_len = OUT_PATH_UNKNOWN;
    client.alt_path_len = OUT_PATH_UNKNOWN;
    client.out_path_is_persistable = true;
    known = true;
    return &client;
  }
};
struct FakeClock {
  uint32_t now = 1000;
  unsigned reads = 0, unique_reads = 0;
  uint32_t getCurrentTime() { ++reads; return now; }
  uint32_t getCurrentTimeUnique() { ++unique_reads; return now + unique_reads; }
};
struct FakeRng {
  unsigned calls = 0;
  void random(uint8_t* out, size_t length) {
    ++calls;
    memset(out, 0xB5, length);
  }
};
static uint32_t ticks = 0;
static uint32_t millis() { return ticks; }
struct MyMesh {
  FakeACL acl;
  mesh::Identity self_id{};
  PostInfo retained_posts[MAX_UNSYNCED_POSTS]{};
  MemoryFS policy_fs;
  MemoryFS* _fs = &policy_fs;
  mesh::RoomAccessPolicy room_access;
  MyMesh() { metadata_filesystem = &policy_fs; assert(room_access.load(&policy_fs)); }
  int matching_peer_indexes[1] = {0};
  mesh::RemoteCliReplyCache remote_cli_reply_cache;
  mesh::LogicalMessageCache<ROOM_MESSAGE_CACHE_SIZE> recent_room_polls;
  void serviceRoomQuotas();
  static bool saveFilter(ClientInfo*);
  bool handleClientPathCommand(ClientInfo*, char*, char*);
  bool executeClientPathCommand(ClientInfo*, mesh::RoomClientPathCommand, const char*, char*);
  bool handleRoomCatchUpCommand(ClientInfo*, char*, char*);
  bool applyRoomCatchUpCommand(ClientInfo*, const char*, uint32_t, char*);
  bool cancelActiveMessageRetries(const uint8_t*, uint32_t) { return true; }
  bool sendClientReply(ClientInfo*, mesh::Packet*, unsigned long, uint8_t);
  unsigned path_acks = 0;
  bool processAck(const uint8_t*) { ++path_acks; return true; }
  uint8_t getUnsyncedCount(ClientInfo*);
  int getExtraAckTransmitCount() { return 0; }
  unsigned requests = 0;
  uint8_t last_request_type = 0;
  int handleRequest(ClientInfo*, uint32_t, uint8_t* payload, size_t, size_t) { ++requests; last_request_type = payload[0]; return 0; }
  void handleCommand(uint32_t, char*, char* reply, int, uint8_t) { strcpy(reply, "OK"); }
  mesh::Packet* createAck(uint32_t ack) { ++post_acks; response.payload_len = 4; memcpy(response.payload, &ack, 4); return &response; }
  mesh::Packet* createMultiAck(uint32_t ack, uint8_t) { return createAck(ack); }
  struct Prefs {
    char password[32] = "admin";
    char guest_password[32] = "guest";
    bool allow_read_only = false;
    uint8_t direct_retry_enabled = 1;
  } _prefs;
  FakeClock clock;
  FakeRng rng;
  unsigned long millis_now = 20000, dirty_contacts_expiry = 0, next_push = 0;
  uint8_t contacts_save_failures = 0, reply_data[32] = {};
  unsigned creations = 0, direct_sends = 0, flood_sends = 0, path_returns = 0;
  bool create_ok = true, queue_ok = true;
  mesh::Packet response;
  uint8_t response_secret[PUB_KEY_SIZE] = {}, response_key[PUB_KEY_SIZE] = {};
  uint8_t returned_path[MAX_PATH_SIZE] = {}, direct_path[MAX_PATH_SIZE] = {};
  uint8_t returned_path_len = 0, direct_path_len = 0, reply_hash_size = 0;
  uint8_t captured_reply[32] = {};
  size_t captured_reply_length = 0;
  mesh::LogicalMessageCache<ROOM_MESSAGE_CACHE_SIZE> recent_room_posts;
  unsigned posts = 0, post_acks = 0;

  FakeClock* getRTCClock() { return &clock; }
  FakeRng* getRNG() { return &rng; }
  mesh::Packet* obtainNewPacket() { return nullptr; }
  void releasePacket(mesh::Packet*) { assert(false); }
  unsigned long futureMillis(unsigned long delay) { return millis_now + delay; }
  mesh::Packet* createDatagram(uint8_t type, const mesh::Identity& sender,
                               const uint8_t* secret, const uint8_t* data, size_t len) {
    ++creations;
    assert(type == PAYLOAD_TYPE_RESPONSE || type == PAYLOAD_TYPE_TXT_MSG);
    assert(len <= sizeof(captured_reply));
    memcpy(response_key, sender.pub_key, PUB_KEY_SIZE);
    memcpy(response_secret, secret, PUB_KEY_SIZE);
    memcpy(captured_reply, data, len);
    captured_reply_length = len;
    return create_ok ? &response : nullptr;
  }
  mesh::Packet* createPathReturn(const mesh::Identity& sender, const uint8_t* secret,
      const uint8_t* path, uint8_t path_len, uint8_t type, const uint8_t* data, size_t len) {
    ++path_returns;
    returned_path_len = path_len;
    memcpy(returned_path, path, (path_len & 63) * ((path_len >> 6) + 1));
    return createDatagram(type, sender, secret, data, len);
  }
  bool sendDirect(mesh::Packet* packet, const uint8_t* path, uint8_t path_len,
                  unsigned long delay) {
    assert(packet == &response && (delay == SERVER_RESPONSE_DELAY || delay == TXT_ACK_DELAY));
    assert(mesh::Packet::isValidPathLen(path_len));
    ++direct_sends;
    direct_path_len = path_len;
    memcpy(direct_path, path, (path_len & 63) * ((path_len >> 6) + 1));
    return queue_ok;
  }
  bool sendFloodReply(mesh::Packet* packet, unsigned long delay, uint8_t hash_size) {
    assert(packet == &response && (delay == SERVER_RESPONSE_DELAY || delay == TXT_ACK_DELAY));
    ++flood_sends;
    reply_hash_size = hash_size;
    return queue_ok;
  }
  bool store_ok = true;
  bool addPost(ClientInfo*, const char*) { if (!store_ok) return false; ++posts; return true; }
  void post(ClientInfo*, const char* text, uint32_t sender_timestamp) {
    std::vector<uint8_t> data(5 + strlen(text) + 1);
    memcpy(data.data(), &sender_timestamp, 4); data[4] = TXT_TYPE_PLAIN << 2;
    memcpy(data.data() + 5, text, strlen(text));
    mesh::Packet packet; packet.header = ROUTE_TYPE_DIRECT;
    onPeerDataRecv(&packet, PAYLOAD_TYPE_TXT_MSG, 0, acl.client.shared_secret, data.data(), data.size() - 1);
  }
  void poll(uint32_t timestamp, uint32_t since = 0, bool flood = false) {
    uint8_t data[10] = {}; memcpy(data, &timestamp, 4); data[4] = REQ_TYPE_KEEP_ALIVE;
    memcpy(data + 5, &since, 4);
    mesh::Packet packet; packet.header = flood ? ROUTE_TYPE_FLOOD : ROUTE_TYPE_DIRECT;
    onPeerDataRecv(&packet, PAYLOAD_TYPE_REQ, 0, acl.client.shared_secret, data, 9);
  }
  void request(uint32_t timestamp, uint8_t subtype, uint8_t argument = 0) {
    uint8_t data[8] = {}; memcpy(data, &timestamp, 4); data[4] = subtype;
    data[5] = argument;
    mesh::Packet packet; packet.header = ROUTE_TYPE_DIRECT;
    onPeerDataRecv(&packet, PAYLOAD_TYPE_REQ, 0, acl.client.shared_secret, data, sizeof(data));
  }
  void onPeerDataRecv(mesh::Packet*, uint8_t, int, const uint8_t*, uint8_t*, size_t);
  bool onPeerPathRecv(mesh::Packet*, int, const uint8_t*, uint8_t*, uint8_t, uint8_t, uint8_t*, uint8_t);
  void onAnonDataRecv(mesh::Packet*, const uint8_t*, const mesh::Identity&,
                       uint8_t*, size_t);
};
@LOGIN_HANDLER@
@PEER_HANDLER@
@PATH_HANDLER@
@QUOTA_SERVICE@
@SAVE_FILTER@
@CLIENT_PATH_HANDLER@
@CLIENT_PATH_EXECUTE@
@SEND_CLIENT_REPLY@
@ROOM_CATCHUP_HANDLER@
@ROOM_CATCHUP_APPLY@
@ROOM_UNSYNCED_COUNT@

static mesh::Identity identity(uint8_t n = 1) {
  mesh::Identity sender{};
  memset(sender.pub_key, n, sizeof(sender.pub_key));
  return sender;
}
static void seedKnown(MyMesh& mesh, uint8_t permissions = PERM_ACL_ADMIN) {
  auto& client = mesh.acl.client;
  // Initialize padding too, so byte-for-byte rejected-session checks are stable.
  memset(&client, 0, sizeof(client));
  client.id = identity();
  client.permissions = permissions;
  client.last_timestamp = 99;
  client.last_activity = 777;
  client.out_path_len = 2;
  client.alt_path_len = OUT_PATH_UNKNOWN;
  client.out_path[0] = 0xA1;
  client.out_path[1] = 0xB2;
  client.out_path_is_persistable = false;
  memset(client.shared_secret, 0x44, sizeof(client.shared_secret));
  client.extra.room.sync_since = 70;
  client.extra.room.last_post_timestamp = 800;
  client.extra.room.pending_ack = 0x12345678;
  client.extra.room.push_failures = 2;
  client.extra.room.topic_seen_revision = 11;
  client.extra.room.pending_topic_revision = 12;
  client.extra.room.topic_failures = 2;
  client.extra.room.post_quota_used = 9;
  client.extra.room.poll_quota_used = 8;
  mesh.acl.known = true;
}
static mesh::Packet incoming(bool flood = false) {
  mesh::Packet packet;
  packet.header = (PAYLOAD_TYPE_ANON_REQ << PH_TYPE_SHIFT)
      | (flood ? ROUTE_TYPE_FLOOD : ROUTE_TYPE_DIRECT);
  packet.path_len = 0x42; // Two 2-byte hashes, including non-default hash size.
  packet.path[0] = 0x10;
  packet.path[1] = 0x20;
  packet.path[2] = 0x30;
  packet.path[3] = 0x40;
  return packet;
}
static void login(MyMesh& mesh, const char* password = "guest", uint32_t timestamp = 100,
                   uint32_t sync_since = 80, bool flood = false) {
  // The real decrypt dispatcher provides one writable terminator byte.
  const size_t password_len = strlen(password);
  std::vector<uint8_t> data(8 + password_len + 1, 0xEE);
  memcpy(data.data(), &timestamp, 4);
  memcpy(data.data() + 4, &sync_since, 4);
  memcpy(data.data() + 8, password, password_len);
  uint8_t secret[PUB_KEY_SIZE];
  memset(secret, 0x77, sizeof(secret));
  auto packet = incoming(flood);
  mesh.onAnonDataRecv(&packet, secret, identity(), data.data(), data.size() - 1);
  if (mesh.room_access.allowsIdentity(identity().pub_key)) assert(data.back() == 0);
}
static void checkReply(const MyMesh& mesh, uint8_t permissions) {
  assert(mesh.creations == 1 && mesh.captured_reply_length == 13);
  uint32_t timestamp;
  memcpy(&timestamp, mesh.captured_reply, sizeof(timestamp));
  assert(timestamp == mesh.clock.now + 1);
  assert(mesh.captured_reply[4] == RESP_SERVER_LOGIN_OK);
  assert(mesh.captured_reply[5] == 0);
  const uint8_t role = permissions & PERM_ACL_ROLE_MASK;
  assert(mesh.captured_reply[6] == (role == PERM_ACL_ADMIN ? 1 : role == PERM_ACL_GUEST ? 2 : 0));
  assert(mesh.captured_reply[7] == permissions);
  assert(mesh.captured_reply[12] == FIRMWARE_VER_LEVEL);
  for (unsigned i = 0; i < 4; ++i) assert(mesh.captured_reply[8 + i] == 0xB5);
  for (unsigned i = 0; i < PUB_KEY_SIZE; ++i) {
    assert(mesh.response_key[i] == 1 && mesh.response_secret[i] == 0x77);
  }
}
static void checkRejectedSession(const MyMesh& mesh, const ClientInfo& before) {
  assert(memcmp(&mesh.acl.client, &before, sizeof(before)) == 0);
  assert(mesh.creations == 0 && mesh.direct_sends == 0 && mesh.flood_sends == 0);
  assert(mesh.clock.reads == 0 && mesh.clock.unique_reads == 0 && mesh.rng.calls == 0);
  assert(mesh.next_push == 0 && mesh.dirty_contacts_expiry == 0);
}

static void authorizationCases() {
  struct Case { const char* password; bool reader; bool accepted; uint8_t role; };
  const Case cases[] = {
    {"", false, false, PERM_ACL_GUEST}, {"", true, true, PERM_ACL_GUEST},
    {"wrong", false, false, PERM_ACL_GUEST}, {"wrong", true, true, PERM_ACL_GUEST},
    {"guest", false, true, PERM_ACL_READ_WRITE}, {"guest", true, true, PERM_ACL_READ_WRITE},
    {"admin", false, true, PERM_ACL_ADMIN}, {"admin", true, true, PERM_ACL_ADMIN},
  };
  for (const auto& c : cases) {
    MyMesh mesh;
    mesh._prefs.allow_read_only = c.reader;
    login(mesh, c.password);
    assert(mesh.acl.known == c.accepted);
    assert(mesh.acl.authorizations == (c.accepted ? 1U : 0U));
    assert(mesh.acl.allocations == (c.accepted ? 1U : 0U));
    assert(mesh.acl.storage.reads == (c.accepted ? 1U : 0U));
    assert(mesh.acl.storage.writes == (c.accepted && c.role != PERM_ACL_GUEST ? 1U : 0U));
    if (c.accepted) {
      assert(mesh.acl.client.permissions == c.role);
      checkReply(mesh, c.role);
    } else {
      assert(!mesh.acl.storage.found && mesh.acl.storage.ceiling == 0);
      assert(mesh.creations == 0 && mesh.dirty_contacts_expiry == 0);
    }
  }
  for (bool reader : {false, true}) {
    MyMesh mesh;
    mesh._prefs.password[0] = mesh._prefs.guest_password[0] = 0;
    mesh._prefs.allow_read_only = reader;
    login(mesh, "");
    assert(mesh.acl.known == reader);
    if (reader) {
      assert(mesh.acl.client.permissions == PERM_ACL_GUEST);
      assert(mesh.acl.storage.writes == 0);
      checkReply(mesh, PERM_ACL_GUEST);
    } else assert(mesh.acl.authorizations == 0);
  }
  // Parser validation occurs before lookup, replay reservation, or allocation.
  for (size_t length = 0; length < 8; ++length) {
    MyMesh mesh;
    auto packet = incoming();
    std::vector<uint8_t> data(length + 1, 0xEE);
    uint8_t secret[PUB_KEY_SIZE] = {};
    mesh.onAnonDataRecv(&packet, secret, identity(), data.data(), length);
    assert(data.back() == 0xEE && mesh.acl.lookups == 0);
    assert(mesh.acl.authorizations == 0 && mesh.acl.allocations == 0);
  }
  MyMesh other_payload;
  auto packet = incoming();
  packet.header = (PAYLOAD_TYPE_RESPONSE << PH_TYPE_SHIFT) | ROUTE_TYPE_DIRECT;
  uint8_t data[9] = {}, secret[PUB_KEY_SIZE] = {};
  other_payload.onAnonDataRecv(&packet, secret, identity(), data, 8);
  assert(other_payload.acl.lookups == 0 && other_payload.acl.authorizations == 0);
  puts("real-handler credentials, disabled passwords, and minimum payload cases passed");
}

static void preservedRoles() {
  const char* passwords[] = {"guest", "wrong", "", "old-admin"};
  unsigned preserved = 0;
  for (unsigned permissions = 0; permissions < 256; ++permissions) {
    for (const auto* password : passwords) {
      for (bool reader : {false, true}) {
        MyMesh mesh;
        seedKnown(mesh, permissions);
        mesh._prefs.allow_read_only = reader;
        mesh.acl.client.permissions_are_explicit = reader;
        login(mesh, password);
        assert(mesh.acl.client.permissions == permissions);
        assert(mesh.acl.client.permissions_are_explicit == reader);
        assert(mesh.acl.authorized_role == (permissions & PERM_ACL_ROLE_MASK));
        assert(mesh.acl.allocations == 0 && mesh.acl.client.last_timestamp == 100);
        checkReply(mesh, permissions);
        // Repeat the exact accepted packet with a different public password.
        const ClientInfo before = mesh.acl.client;
        mesh.creations = mesh.direct_sends = mesh.flood_sends = 0;
        mesh.clock.reads = mesh.clock.unique_reads = mesh.rng.calls = 0;
        mesh.next_push = mesh.dirty_contacts_expiry = 0;
        const unsigned writes_before = mesh.acl.storage.writes;
        login(mesh, "guest", 100, 900, true);
        checkRejectedSession(mesh, before);
        assert(mesh.acl.storage.writes == writes_before);
        ++preserved;
      }
    }
    // A valid explicit Admin credential replaces delegated labels 4/5, too.
    MyMesh mesh;
    seedKnown(mesh, permissions);
    mesh.acl.client.permissions_are_explicit = true;
    login(mesh, "admin");
    const uint8_t expected = (permissions & ~PERM_ACL_ROLE_MASK) | PERM_ACL_ADMIN;
    assert(mesh.acl.client.permissions == expected);
    assert(mesh.acl.client.permissions_are_explicit);
    checkReply(mesh, expected);
  }
  assert(preserved == 2048);
  puts("2048 real-handler preserved roles and replay rejects plus 256 admin upgrades passed");
}

static void storageFailures() {
  for (bool known : {false, true}) {
    for (unsigned fault = 0; fault < 5; ++fault) {
      MyMesh mesh;
      if (known) seedKnown(mesh, PERM_ACL_FILTER_MGR | 0x80);
      const ClientInfo before = mesh.acl.client;
      switch (fault) {
        case 0: mesh.acl._fs = nullptr; break;
        case 1: mesh.acl.acl_load_complete = false; break;
        case 2: mesh.acl.login_replay_store_available = false; break;
        case 3: mesh.acl.storage.read_ok = false; break;
        case 4: mesh.acl.storage.write_ok = false; break;
      }
      login(mesh, known ? "guest" : "admin");
      checkRejectedSession(mesh, before);
      assert(mesh.acl.known == known && mesh.acl.allocations == 0);
      assert(mesh.acl.authorizations == 1);
      assert(!mesh.acl.storage.found && mesh.acl.storage.ceiling == 0);
      assert(mesh.acl.storage.writes == (fault == 4 ? 1U : 0U));
    }
  }
  // Denial from disabled/incorrect credentials never reaches replay storage.
  for (bool write_ok : {false, true}) {
    MyMesh mesh;
    mesh.acl.storage.write_ok = write_ok;
    login(mesh, "wrong");
    assert(mesh.acl.authorizations == 0 && mesh.acl.allocations == 0);
    assert(mesh.acl.storage.reads == 0 && mesh.acl.storage.writes == 0);
  }
  // A historical privileged tombstone is still enforced on a public reader.
  MyMesh tombstone;
  tombstone._prefs.allow_read_only = true;
  tombstone.acl.storage.found = true;
  memcpy(tombstone.acl.storage.key, identity().pub_key, PUB_KEY_SIZE);
  tombstone.acl.storage.ceiling = 160;
  login(tombstone, "", 160);
  assert(!tombstone.acl.known && tombstone.acl.allocations == 0);
  assert(tombstone.acl.storage.ceiling == 160 && tombstone.acl.storage.writes == 0);
  tombstone.acl.storage.write_ok = false;
  login(tombstone, "", 161);
  assert(!tombstone.acl.known && tombstone.acl.allocations == 0);
  assert(tombstone.acl.storage.ceiling == 160 && tombstone.acl.storage.writes == 1);
  // A live downgraded Guest cannot bypass its historical privileged record.
  MyMesh known_reader;
  seedKnown(known_reader, PERM_ACL_GUEST | 0x80);
  known_reader.acl.storage.found = true;
  memcpy(known_reader.acl.storage.key, identity().pub_key, PUB_KEY_SIZE);
  known_reader.acl.storage.ceiling = 159;
  known_reader.acl.storage.write_ok = false;
  const ClientInfo reader_before = known_reader.acl.client;
  login(known_reader, "guest", 160, 900, true);
  checkRejectedSession(known_reader, reader_before);
  assert(known_reader.acl.authorized_role == PERM_ACL_GUEST);
  assert(known_reader.acl.allocations == 0 && known_reader.acl.storage.ceiling == 159);
  assert(known_reader.acl.storage.writes == 1);
  // Durable reservation happens before allocation; a full ACL cannot roll it back.
  MyMesh full;
  full.acl.allocate_ok = false;
  login(full, "admin");
  assert(!full.acl.known && full.acl.allocations == 1 && full.creations == 0);
  assert(full.acl.storage.found && full.acl.storage.ceiling == 160);
  login(full, "admin", 100);
  assert(full.acl.allocations == 1 && full.acl.storage.writes == 1);
  // Zero timestamps are never accepted even with correct credentials.
  MyMesh zero;
  login(zero, "admin", 0);
  assert(!zero.acl.known && zero.acl.allocations == 0 && zero.acl.storage.writes == 0);
  puts("real replay admission rejects failed storage, historical replay, and failed allocation passed");
}

static void sessionRefresh() {
  for (bool flood : {false, true}) {
    for (uint8_t route : {uint8_t(2), uint8_t(OUT_PATH_UNKNOWN), uint8_t(OUT_PATH_FORCE_FLOOD)}) {
      MyMesh mesh;
      seedKnown(mesh, PERM_ACL_REGION_MGR | 0x80);
      mesh.acl.client.out_path_len = route;
      login(mesh, "guest", 100, 81, flood);
      const auto& client = mesh.acl.client;
      assert(client.observed_path_len == OUT_PATH_UNKNOWN);
      assert(client.observed_path_pending == flood);
      assert(client.observed_path_expiry == (flood ? mesh.millis_now + 60000 : 0));
      assert(client.permissions == (PERM_ACL_REGION_MGR | 0x80));
      assert(client.last_timestamp == 100 && client.last_activity == mesh.clock.now);
      assert(client.extra.room.sync_since == 81 && client.extra.room.last_post_timestamp == 800);
      assert(client.extra.room.pending_ack == 0 && client.extra.room.push_failures == 0);
      assert(client.extra.room.topic_seen_revision == 0 && client.extra.room.pending_topic_revision == 0);
      assert(client.extra.room.topic_failures == 0);
      assert(client.extra.room.post_quota_used == 9 && client.extra.room.poll_quota_used == 8);
      for (uint8_t value : client.shared_secret) assert(value == 0x77);
      assert(mesh.next_push == mesh.millis_now + PUSH_NOTIFY_DELAY_MILLIS);
      assert(mesh.dirty_contacts_expiry == mesh.millis_now + LAZY_CONTACTS_WRITE_DELAY);
      assert(mesh.acl.allocations == 0 && mesh.clock.reads == 1 && mesh.clock.unique_reads == 1);
      const uint8_t expected_route = flood && route != OUT_PATH_FORCE_FLOOD ? OUT_PATH_UNKNOWN : route;
      assert(client.out_path_len == expected_route);
      assert(client.out_path[0] == 0xA1 && client.out_path[1] == 0xB2);
      if (flood) {
        assert(mesh.path_returns == 1 && mesh.flood_sends == 1 && mesh.direct_sends == 0);
        assert(mesh.returned_path_len == 0x42 && mesh.reply_hash_size == 2);
        const uint8_t expected_path[] = {0x10, 0x20, 0x30, 0x40};
        assert(memcmp(mesh.returned_path, expected_path, sizeof(expected_path)) == 0);
        if (route != OUT_PATH_FORCE_FLOOD) assert(client.out_path_is_persistable);
      } else if (route == 2) {
        assert(mesh.path_returns == 0 && mesh.direct_sends == 1 && mesh.flood_sends == 0);
        assert(mesh.direct_path_len == 2 && mesh.direct_path[0] == 0xA1 && mesh.direct_path[1] == 0xB2);
      } else assert(mesh.path_returns == 0 && mesh.flood_sends == 1 && mesh.direct_sends == 0);
      checkReply(mesh, client.permissions);
    }
  }
  // A login refresh must preserve an existing persistence fault's backoff.
  MyMesh backoff;
  seedKnown(backoff);
  backoff.dirty_contacts_expiry = 400000;
  backoff.contacts_save_failures = 5;
  login(backoff, "guest");
  assert(backoff.dirty_contacts_expiry == 400000 && backoff.contacts_save_failures == 5);
  // Activity alone is transient; an unchanged persisted session needs no write.
  MyMesh unchanged;
  seedKnown(unchanged);
  memset(unchanged.acl.client.shared_secret, 0x77, PUB_KEY_SIZE);
  login(unchanged, "guest", 100, 70);
  assert(unchanged.acl.client.last_activity == unchanged.clock.now);
  assert(unchanged.dirty_contacts_expiry == 0);
  // Packet allocation failure cannot undo a successfully authenticated session.
  MyMesh response_failure;
  seedKnown(response_failure);
  response_failure.create_ok = false;
  login(response_failure, "guest");
  assert(response_failure.acl.client.last_timestamp == 100);
  assert(response_failure.creations == 1 && response_failure.direct_sends == 0 && response_failure.flood_sends == 0);
  puts("real-handler successful reconnect refreshes cursor, activity, secret, topic state, and route passed");
}

static void observedLoginPath() {
  MyMesh mesh;
  seedKnown(mesh);
  mesh.acl.client.out_path_len = OUT_PATH_FORCE_FLOOD;
  login(mesh, "guest", 100, 80, true);
  auto& client = mesh.acl.client;
  assert(client.observed_path_pending && client.out_path_len == OUT_PATH_FORCE_FLOOD);
  uint8_t route[] = {0x12, 0x34, 0x56, 0x78}, ack[4]{};
  auto packet = incoming();
  // ACK and flood packets are not the reciprocal empty-custom login PATH.
  mesh.onPeerPathRecv(&packet, 0, client.shared_secret, route, 0x42,
                      PAYLOAD_TYPE_ACK, ack, sizeof(ack));
  assert(client.observed_path_pending && mesh.path_acks == 1);
  auto flood_packet = incoming(true);
  mesh.onPeerPathRecv(&flood_packet, 0, client.shared_secret, route, 0x42, 0x0F, ack, 0);
  assert(client.observed_path_pending);
  assert(!mesh.onPeerPathRecv(&packet, 0, client.shared_secret, route, 0x42, 0x0F, ack, 0));
  assert(!client.observed_path_pending && client.observed_path_len == 0x42);
  assert(memcmp(client.observed_path, route, sizeof(route)) == 0);
  assert(client.out_path_len == OUT_PATH_FORCE_FLOOD); // An observed PATH is not an override.
  char get_command[] = "get outpath path", reply[180]{};
  assert(mesh.handleClientPathCommand(&client, get_command, reply));
  assert(strcmp(reply, "> 1234,5678") == 0);
  char set_command[] = "set outpath path";
  assert(mesh.handleClientPathCommand(&client, set_command, reply));
  assert(client.out_path_len == 0x42 && client.out_path_is_persistable);
  assert(memcmp(client.out_path, route, sizeof(route)) == 0);
  login(mesh, "guest", 101, 80, false);
  assert(!client.observed_path_pending && client.observed_path_len == OUT_PATH_UNKNOWN);
  login(mesh, "guest", 102, 80, true);
  mesh.onPeerPathRecv(&packet, 0, client.shared_secret, route, 0, 0x0F, ack, 0);
  assert(client.observed_path_len == 0 && !client.observed_path_pending);
  char zero_hop[] = "set outpath path";
  assert(mesh.handleClientPathCommand(&client, zero_hop, reply));
  assert(client.out_path_len == 0 && strcmp(reply, "> direct") == 0);
  // Timeout and an unrelated PATH cannot create an observed-route result.
  login(mesh, "guest", 103, 80, true);
  mesh.millis_now += 60000;
  mesh.onPeerPathRecv(&packet, 0, client.shared_secret, route, 0x42, 0x0F, ack, 0);
  assert(!client.observed_path_pending && client.observed_path_len == OUT_PATH_UNKNOWN);
  login(mesh, "guest", 104, 80, true);
  char explicitly_selected[] = "set outpath B1";
  assert(mesh.handleClientPathCommand(&client, explicitly_selected, reply));
  mesh.onPeerPathRecv(&packet, 0, client.shared_secret, route, 0x42, 0x0F, ack, 0);
  assert(client.observed_path_len == 0x42 && client.out_path_len == 1 && client.out_path[0] == 0xB1);
  // No reciprocal PATH is requested unless the login response was queued.
  for (bool allocation_fault : {false, true}) {
    MyMesh failed;
    failed.create_ok = !allocation_fault;
    failed.queue_ok = allocation_fault;
    login(failed, "guest", 100, 80, true);
    assert(failed.acl.known && failed.acl.client.last_timestamp == 100);
    assert(!failed.acl.client.observed_path_pending
           && failed.acl.client.observed_path_len == OUT_PATH_UNKNOWN);
  }
  puts("actual flood login opens observed window, PATH captures, direct login clears, and expiry rejects passed");
}

static void reconnectCursor() {
  for (uint32_t supplied : {uint32_t(0), uint32_t(300), uint32_t(500), uint32_t(700)}) {
    MyMesh mesh; seedKnown(mesh, PERM_ACL_READ_WRITE);
    mesh.acl.client.extra.room.sync_since = 500;
    login(mesh, "guest", 100, supplied);
    const uint32_t expected = supplied > 500 ? supplied : 500;
    assert(mesh.acl.client.extra.room.sync_since == expected && mesh.creations == 1);
  }
  MyMesh new_client;
  login(new_client, "guest", 100, 300);
  assert(new_client.acl.client.extra.room.sync_since == 300 && new_client.creations == 1);
  puts("existing reconnect retains catch-up floor and accepts newer cursors while new clients honor supplied history passed");
}

static void readOnlyPosting() {
  for (const char* password : {"", "wrong"}) {
    MyMesh mesh;
    mesh._prefs.allow_read_only = true;
    login(mesh, password);
    assert(mesh.acl.known && mesh.acl.client.permissions == PERM_ACL_GUEST);
    mesh.post(&mesh.acl.client, "reader must not write", 1000);
    assert(mesh.posts == 0 && mesh.post_acks == 0);
    assert(mesh.acl.client.extra.room.last_post_timestamp == 0);
    // Existing Guest is an ACL role too; a public password cannot upgrade it.
    login(mesh, "guest", 101);
    assert(mesh.acl.client.permissions == PERM_ACL_GUEST && mesh.acl.storage.writes == 0);
    mesh.post(&mesh.acl.client, "still not writable", 1001);
    assert(mesh.posts == 0 && mesh.post_acks == 0);
  }
  for (uint8_t role : {uint8_t(PERM_ACL_READ_ONLY), uint8_t(PERM_ACL_REGION_MGR), uint8_t(PERM_ACL_FILTER_MGR)}) {
    MyMesh mesh;
    seedKnown(mesh, role);
    login(mesh, "guest");
    mesh.post(&mesh.acl.client, "not a posting role", 1000);
    assert(mesh.acl.client.permissions == role && mesh.posts == 0 && mesh.post_acks == 0);
  }
  for (const char* password : {"guest", "admin"}) {
    MyMesh mesh;
    login(mesh, password);
    mesh.post(&mesh.acl.client, "writable", 1000);
    mesh.post(&mesh.acl.client, "writable", 1000);
    assert(mesh.posts == 1 && mesh.post_acks == 2);
  }
  puts("actual room post gate blocks logged-in readers/managers and accepts RW/Admin once passed");
}


static void moderationCases() {
  for (bool known : {false, true}) for (const char* password : {"", "guest", "admin"}) {
    MyMesh mesh;
    if (known) seedKnown(mesh, PERM_ACL_ADMIN);
    mesh._prefs.allow_read_only = true;
    assert(mesh.room_access.addBan(&mesh.policy_fs, identity().pub_key) == mesh::RoomAccessPolicy::BanResult::Saved);
    const ClientInfo before = mesh.acl.client;
    login(mesh, password);
    checkRejectedSession(mesh, before);
    assert(mesh.acl.authorizations == 0 && mesh.acl.allocations == 0 && mesh.acl.lookups == 0);
    assert(mesh.acl.storage.reads == 0 && mesh.acl.storage.writes == 0);
    if (known) {
      mesh.post(&mesh.acl.client, "banned", 1000);
      mesh.poll(1000, 900);
      assert(mesh.posts == 0 && mesh.post_acks == 0);
      assert(memcmp(&mesh.acl.client, &before, sizeof(before)) == 0);
      auto packet = incoming(); uint8_t path[] = {0x11, 0x22}, ack[4] = {};
      assert(!mesh.onPeerPathRecv(&packet, 0, mesh.acl.client.shared_secret,
                                 path, 2, PAYLOAD_TYPE_ACK, ack, sizeof(ack)));
      assert(mesh.path_acks == 0 && memcmp(&mesh.acl.client, &before, sizeof(before)) == 0);
    }
    assert(mesh.room_access.removeBan(&mesh.policy_fs, identity().pub_key) == mesh::RoomAccessPolicy::BanResult::Saved);
    login(mesh, password);
    assert(mesh.acl.known && mesh.creations == 1);
  }
  // A 31-byte collision is a different identity; bans always use the full key.
  MyMesh collision; auto other = identity(); other.pub_key[31] ^= 1;
  assert(collision.room_access.addBan(&collision.policy_fs, other.pub_key) == mesh::RoomAccessPolicy::BanResult::Saved);
  login(collision, "admin"); assert(collision.acl.known && collision.creations == 1);
  // A bad policy image must not silently admit an Admin or public reader.
  MyMesh unavailable;
  unavailable.policy_fs.put(mesh::ROOM_ACCESS_PRIMARY_PATH, {1,2,3});
  assert(!unavailable.room_access.load(&unavailable.policy_fs));
  unavailable._prefs.allow_read_only = true;
  login(unavailable, "admin");
  assert(!unavailable.acl.known && unavailable.acl.lookups == 0 && unavailable.creations == 0);
  puts("actual room gates deny full-key bans on login, posts, polls, and paths passed");
}

static void quotaCases() {
  MyMesh mesh; ticks = 100; seedKnown(mesh, PERM_ACL_ADMIN);
  assert(mesh.room_access.setRates(&mesh.policy_fs, 1, 1));
  mesh.post(&mesh.acl.client, "one", 1000);
  assert(mesh.posts == 1 && mesh.post_acks == 1 && mesh.acl.client.extra.room.post_quota_used == 1);
  assert(mesh.acl.client.extra.room.poll_quota_used == 0);
  login(mesh, "guest", 100);
  assert(mesh.acl.client.extra.room.post_quota_used == 1); // Re-login never bypasses a posting quota.
  mesh.post(&mesh.acl.client, "two", 1001);
  assert(mesh.posts == 1 && mesh.post_acks == 1 && mesh.acl.client.extra.room.last_post_timestamp == 1000);
  mesh.post(&mesh.acl.client, "one", 1000);
  assert(mesh.posts == 1 && mesh.post_acks == 2 && mesh.acl.client.extra.room.post_quota_used == 1);
  mesh.poll(1001, 50);
  assert(mesh.post_acks == 3 && mesh.acl.client.extra.room.poll_quota_used == 1);
  assert(mesh.acl.client.last_timestamp == 1001 && mesh.acl.client.extra.room.sync_since == 80);
  mesh.acl.client.extra.room.pending_ack = 0x12345678;
  mesh.acl.client.extra.room.pending_topic_revision = 55;
  const auto poll_retry_before = mesh.acl.client;
  mesh.poll(1001, 50); // Even equal-time retries cannot cancel a newer outstanding delivery.
  assert(mesh.post_acks == 4 && mesh.acl.client.extra.room.poll_quota_used == 1);
  assert(memcmp(&poll_retry_before, &mesh.acl.client, sizeof(poll_retry_before)) == 0);
  mesh.poll(1002, 60);
  assert(mesh.post_acks == 4 && mesh.acl.client.last_timestamp == 1001 && mesh.acl.client.extra.room.sync_since == 80);
  login(mesh, "guest", 1002, 80);
  assert(mesh.acl.client.extra.room.post_quota_used == 1 && mesh.acl.client.extra.room.poll_quota_used == 1);
  mesh.poll(1001, 50); // Old exact retry ACK cannot rewind a refreshed/newer cursor.
  assert(mesh.post_acks == 5 && mesh.acl.client.extra.room.sync_since == 80);
  mesh.poll(1001, 99); // Changed content at an old timestamp isn't a cached retry.
  assert(mesh.post_acks == 5 && mesh.acl.client.extra.room.sync_since == 80);
  ticks = 60100;
  mesh.post(&mesh.acl.client, "two", 1001);
  assert(mesh.posts == 2 && mesh.post_acks == 6 && mesh.acl.client.extra.room.last_post_timestamp == 1001);
  assert(mesh.acl.client.extra.room.post_quota_used == 1 && mesh.acl.client.extra.room.poll_quota_used == 0);
  mesh.poll(1002, 60); // Earlier quota rejection must not have entered the retry cache.
  assert(mesh.post_acks == 7 && mesh.acl.client.extra.room.poll_quota_used == 1);
  assert(mesh.acl.client.extra.room.sync_since == 80);
  ticks = 120100; mesh.store_ok = false;
  const auto post_floor = mesh.acl.client.extra.room.last_post_timestamp;
  const auto last_activity = mesh.acl.client.last_activity;
  mesh.post(&mesh.acl.client, "storage-fails", 1003);
  assert(mesh.posts == 2 && mesh.post_acks == 7 && mesh.acl.client.extra.room.post_quota_used == 0);
  assert(mesh.acl.client.extra.room.last_post_timestamp == post_floor && mesh.acl.client.last_activity == last_activity);
  mesh.store_ok = true; mesh.post(&mesh.acl.client, "storage-fails", 1003);
  assert(mesh.posts == 3 && mesh.post_acks == 8 && mesh.acl.client.extra.room.post_quota_used == 1);
  const auto before = mesh.acl.client;
  mesh.poll(1004, 1000, true); // Flood polls do not consume allowance or refresh replay/session state.
  assert(mesh.post_acks == 8 && memcmp(&before, &mesh.acl.client, sizeof(before)) == 0);
  puts("actual room quotas survive relogin, exempt exact retries, and reject without replay or ACK passed");
}

static void allRequestQuotaCases() {
  for (uint8_t subtype : {uint8_t(REQ_TYPE_GET_STATUS), uint8_t(REQ_TYPE_GET_TELEMETRY_DATA),
                          uint8_t(REQ_TYPE_GET_ACCESS_LIST), uint8_t(ROOM_BOARD_REQUEST_SUBTYPE)}) {
    MyMesh mesh; ticks = 100; seedKnown(mesh, PERM_ACL_ADMIN);
    assert(mesh.room_access.setRates(&mesh.policy_fs, 0, 1));
    mesh.request(1000, subtype, 8);
    assert(mesh.requests == 1 && mesh.last_request_type == subtype && mesh.acl.client.extra.room.poll_quota_used == 1);
    assert(mesh.acl.client.last_timestamp == 1000);
    mesh.request(1000, subtype, 8); // Exact accepted request is re-readable without a second charge.
    assert(mesh.requests == 2 && mesh.acl.client.extra.room.poll_quota_used == 1);
    const auto before_reject = mesh.acl.client;
    mesh.request(1001, subtype, 8); // New timestamp consumes a new allowance and is denied.
    mesh.request(1000, subtype, 9); // Changed payload cannot masquerade as an exact retry.
    assert(mesh.requests == 2 && memcmp(&before_reject, &mesh.acl.client, sizeof(before_reject)) == 0);
    ticks = 60100; mesh.request(1001, subtype, 8);
    assert(mesh.requests == 3 && mesh.acl.client.last_timestamp == 1001 && mesh.acl.client.extra.room.poll_quota_used == 1);
    mesh.request(1000, subtype, 8); // Older accepted read may repeat without lowering the session replay floor.
    assert(mesh.requests == 4 && mesh.acl.client.last_timestamp == 1001 && mesh.acl.client.extra.room.poll_quota_used == 1);
    mesh.request(1000, subtype, 9);
    assert(mesh.requests == 4 && mesh.acl.client.last_timestamp == 1001);
  }
  puts("actual request quota covers status, telemetry, ACL, board, and exact payload retries passed");
}
int main(int argc, char** argv) {
  assert(argc == 2);
  if (!strcmp(argv[1], "authorization")) authorizationCases();
  else if (!strcmp(argv[1], "roles")) preservedRoles();
  else if (!strcmp(argv[1], "storage")) storageFailures();
  else if (!strcmp(argv[1], "refresh")) sessionRefresh();
  else if (!strcmp(argv[1], "post")) readOnlyPosting();
  else if (!strcmp(argv[1], "moderation")) moderationCases();
  else if (!strcmp(argv[1], "quotas")) quotaCases();
  else if (!strcmp(argv[1], "requests")) allRequestQuotaCases();
  else if (!strcmp(argv[1], "observed")) observedLoginPath();
  else if (!strcmp(argv[1], "reconnect")) reconnectCursor();
  else assert(false);
}
'''


class RoomLoginIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        compiler = shutil.which("g++") or shutil.which("clang++")
        if compiler is None:
            raise AssertionError("a host C++ compiler is required")
        room = (ROOT / "examples/simple_room_server/MyMesh.cpp").read_text()
        room_header = (ROOT / "examples/simple_room_server/MyMesh.h").read_text()
        acl_header = (ROOT / "src/helpers/ClientACL.h").read_text()
        acl_source = (ROOT / "src/helpers/ClientACL.cpp").read_text()
        packet = (ROOT / "src/Packet.cpp").read_text()
        # Keep the executed bodies, ClientInfo layout, and protocol labels tied
        # to production. Only filesystem operations and radio allocation are fake.
        constants = "\n".join(line for line in acl_header.splitlines()
                              if line.startswith(("#define PERM_ACL_", "#define OUT_PATH_")))
        for name in ("PUSH_NOTIFY_DELAY_MILLIS", "FIRMWARE_VER_LEVEL", "RESP_SERVER_LOGIN_OK",
                     "LAZY_CONTACTS_WRITE_DELAY", "ROOM_MESSAGE_CACHE_SIZE", "MAX_POST_TEXT_LEN", "MAX_UNSYNCED_POSTS", "TXT_TYPE_PLAIN",
                     "TXT_TYPE_CLI_DATA", "TXT_TYPE_CLI_COMMAND", "TXT_ACK_DELAY", "REPLY_DELAY_MILLIS", "REQ_TYPE_KEEP_ALIVE", "REQ_TYPE_GET_STATUS", "REQ_TYPE_GET_TELEMETRY_DATA", "REQ_TYPE_GET_ACCESS_LIST"):
            match = re.search(r"^\s*#define\s+" + name + r"\s+.*$", room, re.MULTILINE)
            if match is None:
                for path in (ROOT / "examples/simple_room_server/MyMesh.h", ROOT / "src/Mesh.h",
                             ROOT / "src/helpers/TxtDataHelpers.h", ROOT / "src/Packet.h"):
                    if path.exists():
                        match = re.search(r"^\s*#define\s+" + name + r"\s+.*$", path.read_text(), re.MULTILINE)
                        if match is not None:
                            break
            if match is None:
                raise AssertionError("production constant not found: " + name)
            constants += "\n" + match.group()
        board = (ROOT / "src/helpers/RoomBoardProtocol.h").read_text()
        board_subtype = re.search(r"static constexpr uint8_t ROOM_BOARD_REQUEST_SUBTYPE = ([^;]+);", board)
        if board_subtype is None:
            raise AssertionError("production board request subtype not found")
        constants += "\n#define ROOM_BOARD_REQUEST_SUBTYPE " + board_subtype[1]
        delay = re.search(r"^\s*#define\s+SERVER_RESPONSE_DELAY\s+.*$", room_header, re.MULTILINE)
        if delay is None:
            raise AssertionError("production SERVER_RESPONSE_DELAY not found")
        constants += "\n" + delay.group()
        replacements = {
            "@CONSTANTS@": constants,
            "@STR_HELPER@": extract_braced((ROOT / "src/helpers/TxtDataHelpers.cpp").read_text(),
                                            "void StrHelper::strncpy("),
            "@PACKET_CONSTRUCTOR@": extract_braced(packet, "Packet::Packet()"),
            "@PACKET_PATH_CHECK@": extract_braced(packet, "bool Packet::isValidPathLen("),
            "@CLIENT_INFO@": extract_braced(acl_header, "struct ClientInfo {"),
            "@POST_INFO@": extract_braced(room_header, "struct PostInfo {"),
            "@REPLAY_HANDLER@": extract_braced(acl_source, "bool ClientACL::authorizeLoginTimestamp("),
            "@PEER_HANDLER@": extract_braced(room, "void MyMesh::onPeerDataRecv("),
            "@PATH_HANDLER@": extract_braced(room, "bool MyMesh::onPeerPathRecv("),
            "@QUOTA_SERVICE@": extract_braced(room, "void MyMesh::serviceRoomQuotas("),
            "@SAVE_FILTER@": extract_braced(room, "bool MyMesh::saveFilter("),
            "@CLIENT_PATH_HANDLER@": extract_braced(room, "bool MyMesh::handleClientPathCommand("),
            "@CLIENT_PATH_EXECUTE@": extract_braced(room, "bool MyMesh::executeClientPathCommand("),
            "@SEND_CLIENT_REPLY@": extract_braced(room, "bool MyMesh::sendClientReply("),
            "@ROOM_CATCHUP_HANDLER@": extract_braced(room, "bool MyMesh::handleRoomCatchUpCommand("),
            # Only the field name changes: retain the posting counter used by
            # this older login fixture while executing the real retention code.
            "@ROOM_CATCHUP_APPLY@": re.sub(r"\bposts\b", "retained_posts", extract_braced(
                room, "bool MyMesh::applyRoomCatchUpCommand(")),
            "@ROOM_UNSYNCED_COUNT@": re.sub(r"\bposts\b", "retained_posts", extract_braced(
                room, "uint8_t MyMesh::getUnsyncedCount(")),
            "@LOGIN_HANDLER@": extract_braced(room, "void MyMesh::onAnonDataRecv("),
        }
        generated = HARNESS
        for marker, text in replacements.items():
            generated = generated.replace(marker, text)
        cls.directory = tempfile.TemporaryDirectory(prefix="room-login-integration-")
        cls.addClassCleanup(cls.directory.cleanup)
        work = Path(cls.directory.name)
        source = work / "test.cpp"
        source.write_text(generated)
        cls.binary = work / "room-login"
        command = [compiler, "-std=c++17", "-O1", "-Wall", "-Wextra", "-Werror",
                   "-Wno-unused-parameter", "-I" + str(ROOT / "test/fixtures/room_history_store"), "-I" + str(ROOT / "src"),
                   str(source), "-o", str(cls.binary)]
        if sys.platform.startswith("linux"):
            command[1:1] = ["-fsanitize=address,undefined", "-fno-sanitize-recover=all",
                            "-fno-omit-frame-pointer", "-fno-pie", "-no-pie"]
        compiled = subprocess.run(command, capture_output=True, text=True, timeout=60)
        if compiled.returncode != 0:
            raise AssertionError(compiled.stdout + compiled.stderr)
        cls.generated = generated
        cls.compile_command = command
        cls.source = source

    def run_case(self, case, expected):
        checked = subprocess.run([str(self.binary), case], capture_output=True, text=True, timeout=15)
        self.assertEqual(checked.returncode, 0, checked.stdout + checked.stderr)
        self.assertIn(expected, checked.stdout)

    def test_unknown_blank_read_only_disabled_passwords_and_payload_bounds(self):
        self.run_case("authorization", "real-handler credentials, disabled passwords, and minimum payload cases passed")

    def test_non_demotion_all_roles_and_exact_replay_preserve_session(self):
        self.run_case("roles", "2048 real-handler preserved roles and replay rejects plus 256 admin upgrades passed")

    def test_auth_or_durable_replay_failure_precedes_acl_allocation_and_mutation(self):
        self.run_case("storage", "real replay admission rejects failed storage, historical replay, and failed allocation passed")

    def test_successful_relogin_refreshes_session_and_routes_without_resetting_post_floor(self):
        self.run_case("refresh", "real-handler successful reconnect refreshes cursor, activity, secret, topic state, and route passed")

    def test_observed_route_uses_actual_login_and_path_handlers_with_timeout(self):
        self.run_case("observed", "actual flood login opens observed window, PATH captures, direct login clears, and expiry rejects passed")

    def test_existing_reconnect_preserves_skip_floor_and_new_clients_choose_history_cursor(self):
        self.run_case("reconnect", "existing reconnect retains catch-up floor and accepts newer cursors while new clients honor supplied history passed")

    def test_negative_control_detects_old_reconnect_cursor_assignment(self):
        before = "client->extra.room.sync_since = std::max(previous_sync_since, sender_sync_since);"
        self.assertEqual(self.generated.count(before), 1)
        source = self.source.parent / "negative-reconnect.cpp"
        source.write_text(self.generated.replace(before, "client->extra.room.sync_since = sender_sync_since;"), encoding="ascii")
        binary = self.source.parent / "negative-reconnect"
        command = [str(source) if arg == str(self.source) else str(binary) if arg == str(self.binary)
                   else arg for arg in self.compile_command]
        compiled = subprocess.run(command, capture_output=True, text=True, timeout=60)
        self.assertEqual(compiled.returncode, 0, compiled.stdout + compiled.stderr)
        checked = subprocess.run([str(binary), "reconnect"], capture_output=True, text=True, timeout=15)
        self.assertNotEqual(checked.returncode, 0, "old reconnect assignment must undo a selected skip")
        self.assertIn("Assertion", checked.stderr, checked.stdout + checked.stderr)

    def test_all_room_request_types_consume_poll_budget_and_preserve_exact_retries(self):
        self.run_case("requests", "actual request quota covers status, telemetry, ACL, board, and exact payload retries passed")

    def test_full_identity_bans_apply_to_new_and_existing_sessions(self):
        self.run_case("moderation", "actual room gates deny full-key bans on login, posts, polls, and paths passed")

    def test_actual_post_and_poll_quotas_retries_and_relogin(self):
        self.run_case("quotas", "actual room quotas survive relogin, exempt exact retries, and reject without replay or ACK passed")

    def test_read_only_login_never_grants_post_permission(self):
        self.run_case("post", "actual room post gate blocks logged-in readers/managers and accepts RW/Admin once passed")


if __name__ == "__main__":
    unittest.main()
