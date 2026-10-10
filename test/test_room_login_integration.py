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
#include <Packet.h>
#include <helpers/RoomLoginAuthorization.h>
#include <helpers/ClientLoginPersistence.h>
#include <helpers/LazyPersistence.h>
#include <helpers/LogicalMessageCache.h>
@CONSTANTS@
#define MESH_CLIENT_REPEATER_ONLY 0
namespace mesh {
struct Identity { uint8_t pub_key[PUB_KEY_SIZE]; };
struct Utils {
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
struct MyMesh {
  FakeACL acl;
  struct Prefs {
    char password[32] = "admin";
    char guest_password[32] = "guest";
    bool allow_read_only = false;
  } _prefs;
  FakeClock clock;
  FakeRng rng;
  unsigned long millis_now = 20000, dirty_contacts_expiry = 0, next_push = 0;
  uint8_t contacts_save_failures = 0, reply_data[32] = {};
  unsigned creations = 0, direct_sends = 0, flood_sends = 0, path_returns = 0;
  bool create_ok = true;
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
  unsigned long futureMillis(unsigned long delay) { return millis_now + delay; }
  mesh::Packet* createDatagram(uint8_t type, const mesh::Identity& sender,
                               const uint8_t* secret, const uint8_t* data, size_t len) {
    ++creations;
    assert(type == PAYLOAD_TYPE_RESPONSE && len == 13);
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
  void sendDirect(mesh::Packet* packet, const uint8_t* path, uint8_t path_len,
                  unsigned long delay) {
    assert(packet == &response && delay == SERVER_RESPONSE_DELAY);
    assert(mesh::Packet::isValidPathLen(path_len));
    ++direct_sends;
    direct_path_len = path_len;
    memcpy(direct_path, path, (path_len & 63) * ((path_len >> 6) + 1));
  }
  void sendFloodReply(mesh::Packet* packet, unsigned long delay, uint8_t hash_size) {
    assert(packet == &response && delay == SERVER_RESPONSE_DELAY);
    ++flood_sends;
    reply_hash_size = hash_size;
  }
  void addPost(ClientInfo*, const char*) { ++posts; }
  void post(ClientInfo* client, const char* text, uint32_t sender_timestamp) {
    const uint8_t flags = TXT_TYPE_PLAIN;
    const size_t text_len = strlen(text);
    bool send_ack = false;
    @POST_GATE@
    if (send_ack) ++post_acks;
  }
  void onAnonDataRecv(mesh::Packet*, const uint8_t*, const mesh::Identity&,
                       uint8_t*, size_t);
};
@LOGIN_HANDLER@

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
  assert(data.back() == 0);
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
        login(mesh, password);
        assert(mesh.acl.client.permissions == permissions);
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
    login(mesh, "admin");
    const uint8_t expected = (permissions & ~PERM_ACL_ROLE_MASK) | PERM_ACL_ADMIN;
    assert(mesh.acl.client.permissions == expected);
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
      assert(client.permissions == (PERM_ACL_REGION_MGR | 0x80));
      assert(client.last_timestamp == 100 && client.last_activity == mesh.clock.now);
      assert(client.extra.room.sync_since == 81 && client.extra.room.last_post_timestamp == 800);
      assert(client.extra.room.pending_ack == 0 && client.extra.room.push_failures == 0);
      assert(client.extra.room.topic_seen_revision == 0 && client.extra.room.pending_topic_revision == 0);
      assert(client.extra.room.topic_failures == 0);
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

int main(int argc, char** argv) {
  assert(argc == 2);
  if (!strcmp(argv[1], "authorization")) authorizationCases();
  else if (!strcmp(argv[1], "roles")) preservedRoles();
  else if (!strcmp(argv[1], "storage")) storageFailures();
  else if (!strcmp(argv[1], "refresh")) sessionRefresh();
  else if (!strcmp(argv[1], "post")) readOnlyPosting();
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
                     "LAZY_CONTACTS_WRITE_DELAY", "ROOM_MESSAGE_CACHE_SIZE", "TXT_TYPE_PLAIN"):
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
        delay = re.search(r"^\s*#define\s+SERVER_RESPONSE_DELAY\s+.*$", room_header, re.MULTILINE)
        if delay is None:
            raise AssertionError("production SERVER_RESPONSE_DELAY not found")
        constants += "\n" + delay.group()
        replacements = {
            "@CONSTANTS@": constants,
            "@PACKET_CONSTRUCTOR@": extract_braced(packet, "Packet::Packet()"),
            "@PACKET_PATH_CHECK@": extract_braced(packet, "bool Packet::isValidPathLen("),
            "@CLIENT_INFO@": extract_braced(acl_header, "struct ClientInfo {"),
            "@REPLAY_HANDLER@": extract_braced(acl_source, "bool ClientACL::authorizeLoginTimestamp("),
            "@POST_GATE@": extract_braced(extract_braced(room, "void MyMesh::onPeerDataRecv("),
                                         "if (flags == TXT_TYPE_PLAIN)"),
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
                   "-Wno-unused-parameter", "-I" + str(ROOT / "src"),
                   str(source), "-o", str(cls.binary)]
        if sys.platform.startswith("linux"):
            command[1:1] = ["-fsanitize=address,undefined", "-fno-sanitize-recover=all",
                            "-fno-omit-frame-pointer", "-fno-pie", "-no-pie"]
        compiled = subprocess.run(command, capture_output=True, text=True, timeout=60)
        if compiled.returncode != 0:
            raise AssertionError(compiled.stdout + compiled.stderr)

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

    def test_read_only_login_never_grants_post_permission(self):
        self.run_case("post", "actual room post gate blocks logged-in readers/managers and accepts RW/Admin once passed")


if __name__ == "__main__":
    unittest.main()
