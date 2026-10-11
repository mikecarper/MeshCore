#include <cassert>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <string>
#include <vector>
#include "../room_history_store/filesystem.h"
#include <helpers/RoomAccessPolicy.h>
#include <helpers/ClientPathPersistence.h>
#include <helpers/RoomClientPathCommand.h>
#include <helpers/RoomMailStore.h>

#define PUB_KEY_SIZE 32
#define MAX_PATH_SIZE 64
#define MAX_HASH_SIZE 8
#define MAX_PACKET_PAYLOAD 184
#define MAX_POST_TEXT_LEN 151
#define MAX_UNSYNCED_POSTS 4
#define TXT_TYPE_SIGNED_PLAIN 2
#define PAYLOAD_TYPE_TXT_MSG 2
#define POST_SYNC_DELAY_SECS 6
#define PUSH_NOTIFY_DELAY_MILLIS 2000
#define PUSH_ACK_TIMEOUT_FLOOD 12000
#define PUSH_TIMEOUT_BASE 4000
#define PUSH_ACK_TIMEOUT_FACTOR 2000
#define SYNC_PUSH_INTERVAL 1200
#define MESH_DEBUG_PRINTLN(...) ((void)0)

static uint32_t ticks = 100000;
static unsigned long futureMillis(unsigned long delay) { return uint32_t(ticks + delay); }
static bool millisHasNowPassed(unsigned long deadline) {
  return int32_t(ticks - uint32_t(deadline)) > 0;
}
namespace mesh {
struct Identity {
  uint8_t pub_key[PUB_KEY_SIZE] = {};
  bool matches(const Identity& other) const {
    return memcmp(pub_key, other.pub_key, PUB_KEY_SIZE) == 0;
  }
};
struct Packet {
  bool radio_reply = false;
  uint8_t payload[MAX_PACKET_PAYLOAD] = {};
  size_t payload_len = 0;
  static bool isValidPathLen(uint8_t path);
};
struct Utils {
  static void sha256(uint8_t* out, size_t n, const uint8_t* a, size_t na) {
    sha256(out, n, a, na, nullptr, 0);
  }
  static void sha256(uint8_t* out, size_t n, const uint8_t* a, size_t na,
                     const uint8_t* b, size_t nb) {
    // Deterministic fixture fingerprint; no cryptography is replaced in firmware.
    uint32_t crc = 2166136261U;
    for (size_t i = 0; i < na; ++i) crc = (crc ^ a[i]) * 16777619U;
    for (size_t i = 0; i < nb; ++i) crc = (crc ^ b[i]) * 16777619U;
    for (size_t i = 0; i < n; ++i) out[i] = uint8_t(crc >> ((i % 4) * 8));
  }
};
struct Store { bool fail = false; std::string text; };
static bool saveRoomTopic(Store* store, const char* text) {
  if (store->fail) return false;
  store->text = text;
  return true;
}
}
#include "state.inc"
static_assert(sizeof(void*) != 4 || sizeof(((ClientInfo*)0)->extra.room) <= sizeof(((ClientInfo*)0)->extra.sensor),
              "topic state must fit the existing shared union");
struct Clock : mesh::RTCClock {
  uint32_t now = 1000;
  uint32_t getCurrentTime() override { return now; }
  void setCurrentTime(uint32_t epoch) override { now = epoch; }
};
struct Random {
  uint8_t attempt = 0;
  void random(uint8_t* out, size_t size) { assert(size == 1); *out = attempt++ & 3; }
};
struct ACL {
  ClientInfo clients[2] = {};
  int count = 1;
  int getNumClients() const { return count; }
  ClientInfo* getClientByIdx(int at) { assert(at >= 0 && at < count); return &clients[at]; }
};
struct Sent {
  uint32_t timestamp;
  std::string text;
  uint8_t author[4];
  uint8_t path[MAX_PATH_SIZE] = {};
  uint8_t path_len = 0;
  bool direct = false, retry_enabled = false;
};
struct MyMesh {
  ACL acl;
  MemoryFS policy_fs;
  mesh::RoomAccessPolicy room_access;
  Clock clock;
  Random rng;
  mesh::Identity self_id;
  mesh::Store store;
  mesh::Store* _fs = &store;
  mesh::Packet packet;
  mesh::Packet alternate_packet;
  std::vector<Sent> sent;
  bool pool_available = true, queue_available = true, alternate_available = true;
  unsigned releases = 0;
  unsigned cancellations = 0;
  uint8_t retry_key[MAX_HASH_SIZE] = {};
  uint32_t retry_timestamp = 0;
  uint8_t reply_data[MAX_PACKET_PAYLOAD] = {};
  unsigned long next_push = 0, room_topic_ready_at = 0;
  bool room_topic_ready = true;
  uint32_t room_topic_revision = 0, room_topic_timestamp = 0;
  int next_client_idx = 0, next_post_idx = 0;
  uint16_t _num_post_pushes = 0;
  char room_topic[MAX_POST_TEXT_LEN + 1] = {};
  PostInfo posts[MAX_UNSYNCED_POSTS] = {};
  uint32_t post_ready_at[MAX_UNSYNCED_POSTS] = {};
  uint32_t post_ready_mask = UINT32_MAX;
  struct { uint8_t path_hash_mode = 0, direct_retry_enabled = 1; } _prefs;
  int default_scope = 0;
  MyMesh() {
    metadata_filesystem = &policy_fs;
    assert(room_access.load(&policy_fs));
    self_id.pub_key[0] = 77;
    acl.clients[0].id.pub_key[0] = 1;
    acl.clients[0].last_activity = 1000;
    for (auto& client : acl.clients) client.alt_path_len = OUT_PATH_UNKNOWN;
  }
  Clock* getRTCClock() { return &clock; }
  Random* getRNG() { return &rng; }
  mesh::Packet* createDatagram(int type, const mesh::Identity&, const uint8_t*,
                                const uint8_t* data, int len) {
    assert(type == PAYLOAD_TYPE_TXT_MSG && len >= 9 && len <= 160);
    if (!pool_available) return nullptr;
    assert((data[4] >> 2) == TXT_TYPE_SIGNED_PLAIN);
    memcpy(packet.payload, data, len);
    packet.payload_len = len;
    return &packet;
  }
  mesh::Packet* obtainNewPacket() { return alternate_available ? &alternate_packet : nullptr; }
  void releasePacket(mesh::Packet* out) { assert(out == &alternate_packet); ++releases; }
  bool queue(mesh::Packet* out) {
    assert(out->radio_reply);
    if (!queue_available) return false;
    Sent entry;
    memcpy(&entry.timestamp, out->payload, 4);
    memcpy(entry.author, out->payload + 5, 4);
    entry.text = std::string(reinterpret_cast<char*>(out->payload + 9), out->payload_len - 9);
    sent.push_back(entry);
    return true;
  }
  bool sendFloodScoped(int, mesh::Packet* out, unsigned long, uint8_t) { return queue(out); }
  bool sendFloodReply(mesh::Packet* out, unsigned long delay, uint8_t width) {
    return sendFloodScoped(default_scope, out, delay, width);
  }
  bool sendDirect(mesh::Packet* out, const uint8_t* path, uint8_t length, unsigned long = 0) {
    if (!queue(out)) return false;
    auto& entry = sent.back();
    entry.direct = true; entry.path_len = length;
    entry.retry_enabled = _prefs.direct_retry_enabled != 0;
    memcpy(entry.path, path, mesh::encodedClientPathByteLength(length));
    return true;
  }
  void replaceActiveMessageRetries(mesh::Packet*, const uint8_t* key, uint32_t timestamp) {
    memcpy(retry_key, key, sizeof(retry_key)); retry_timestamp = timestamp;
  }
  bool pushPostToClient(ClientInfo*, PostInfo&);
  bool sendClientReply(ClientInfo*, mesh::Packet*, unsigned long, uint8_t);
  bool pushRoomTextToClient(ClientInfo*, uint32_t, const mesh::Identity&, const char*, uint32_t = 0);
  bool processAck(const uint8_t*);
  void activateRoomTopic();
  bool handleRoomTopicCommand(const char*, char*);
  void serviceRoomPush();
  uint8_t getUnsyncedCount(ClientInfo*);
  bool roomClientChatEnabled(ClientInfo*);
  bool cancelActiveMessageRetries(const uint8_t* key, uint32_t timestamp) {
    assert(memcmp(key, retry_key, sizeof(retry_key)) == 0 && timestamp == retry_timestamp);
    ++cancellations; return true;
  }
  void tick(uint32_t delta = 0) {
    ticks += delta + 1;
    next_push = uint32_t(ticks - 1);
    memset(reply_data, 0, sizeof(reply_data));
    serviceRoomPush();
  }
  void ack() {
    const uint32_t ack = acl.clients[0].extra.room.pending_ack;
    assert(ack && processAck(reinterpret_cast<const uint8_t*>(&ack)));
  }
  void topic(const char* text) {
    std::string command = std::string("set topic ") + text;
    char reply[160] = {};
    assert(handleRoomTopicCommand(command.c_str(), reply));
    assert(strncmp(reply, "OK", 2) == 0);
  }
  void post(int index, uint32_t timestamp, const char* text) {
    posts[index].post_timestamp = timestamp;
    posts[index].author.pub_key[0] = 99;
    strcpy(posts[index].text, text);
  }
};
#include "production.inc"
int main() {
  {
    MyMesh m; m.topic("Welcome");
    auto& client = m.acl.clients[0];
    client.extra.room.sync_since = 50;
    m.post(0, 100, "old-one"); m.post(1, 200, "old-two");
    m.post(2, 1100, "newer"); m.clock.now = 1200;
    m.tick(6000); assert(m.sent.back().text == "old-one"); m.ack();
    m.tick(); assert(m.sent.back().text == "old-two"); m.ack();
    m.tick(); assert(m.sent.back().text == "Welcome");
    assert(m.sent.back().author[0] == 77);
    assert(client.extra.room.sync_since == 200); m.ack();
    assert(client.extra.room.sync_since == 200);
    assert(client.extra.room.topic_seen_revision == m.room_topic_revision);
    // An app echoing the latest topic timestamp cannot skip the newer post.
    client.extra.room.sync_since = m.room_topic_timestamp;
    m.tick(); assert(m.sent.back().text == "newer"); m.ack();
    assert(client.extra.room.sync_since == 1100);
    const auto count = m.sent.size(); m.tick(); assert(m.sent.size() == count);
  }
  {
    MyMesh m; m.topic("first"); m.tick(6000);
    const uint32_t old_revision = m.room_topic_revision;
    const uint32_t old_ack = m.acl.clients[0].extra.room.pending_ack;
    m.topic("second");
    assert(m.processAck(reinterpret_cast<const uint8_t*>(&old_ack)));
    assert(m.acl.clients[0].extra.room.topic_seen_revision == old_revision);
    assert(m.acl.clients[0].extra.room.sync_since == 0);
    m.tick(6000); assert(m.sent.back().text == "second"); m.ack();
  }
  {
    MyMesh m; m.topic("lost"); m.post(0, 1100, "chat"); m.clock.now = 1200;
    m.tick(6000);
    for (int attempt = 0; attempt < 3; ++attempt) {
      auto& c = m.acl.clients[0];
      ticks = uint32_t(c.extra.room.ack_timeout); m.tick();
    }
    assert(m.sent.size() == 4 && m.sent.back().text == "chat");
    assert(m.acl.clients[0].extra.room.push_failures == 0);
    m.ack(); assert(m.acl.clients[0].extra.room.sync_since == 1100);
    assert(m.acl.clients[0].extra.room.topic_failures == 3);
    m.topic("changed"); m.tick(6000); assert(m.sent.back().text == "changed");
  }
  {
    MyMesh m; m.topic("topic"); m.clock.now = 1003;
    m.post(0, 1000, "unaged"); m.post_ready_mask &= ~UINT32_C(1);
    m.post_ready_at[0] = futureMillis(10000); m.tick(6000);
    assert(m.sent.empty()); // Unaged older posts still protect app forceSince.
    m.tick(4000); assert(m.sent.back().text == "unaged");
    m.ack(); m.tick(); assert(m.sent.back().text == "topic");
  }
  for (bool flood : {false, true}) for (bool pool : {false, true}) {
    MyMesh m; m.topic("transport");
    m.acl.clients[0].out_path_len = flood ? OUT_PATH_UNKNOWN : 0;
    m.pool_available = pool; m.queue_available = !pool;
    m.tick(6000);
    assert(m.sent.empty() && m.acl.clients[0].extra.room.pending_ack == 0);
    assert(m.acl.clients[0].extra.room.pending_topic_revision == 0);
    assert(m.acl.clients[0].extra.room.topic_seen_revision == 0);
    m.pool_available = m.queue_available = true; m.tick(); m.ack();
    assert(m.acl.clients[0].extra.room.topic_seen_revision == m.room_topic_revision);
  }
  {
    MyMesh m; const std::string maximum(151, 'x'); m.topic(maximum.c_str());
    char guarded[164]; memset(guarded, 0xa5, sizeof(guarded));
    assert(m.handleRoomTopicCommand("get topic", guarded + 3));
    assert(std::string(guarded + 3) == "> " + maximum);
    assert(uint8_t(guarded[0]) == 0xa5 && uint8_t(guarded[160]) == 0xa5);
    m.tick(6000); assert(m.sent.back().text == maximum); m.ack();
    char reply[160] = {};
    std::string oversized = "set topic " + maximum + "x";
    assert(m.handleRoomTopicCommand(oversized.c_str(), reply));
    assert(strncmp(reply, "Err", 3) == 0 && m.room_topic == maximum);
    m.store.fail = true;
    assert(m.handleRoomTopicCommand("set topic replacement", reply));
    assert(strncmp(reply, "Err", 3) == 0 && m.room_topic == maximum);
    m.store.fail = false; m.topic("");
    const auto count = m.sent.size(); m.tick(6000); assert(m.sent.size() == count);
    assert(m.store.text.empty());
    assert(!m.handleRoomTopicCommand("set topic-other text", reply));
    assert(!m.handleRoomTopicCommand("get topic-other", reply));
  }
  {
    MyMesh m; m.acl.count = 0; m.next_client_idx = 4; m.tick();
    m.acl.count = 1; m.acl.clients[0].last_activity = 0;
    m.topic("inactive"); m.tick(6000); assert(m.sent.empty());
    m.acl.clients[0].last_activity = 1000; m.tick(); m.ack();
    assert(m.next_client_idx == 0);
  }
  {
    MyMesh m; m.clock.now = 2000; m.topic("corrected clock");
    m.clock.setCurrentTime(1000); m.clock.resetUniqueTime(1000);
    m.post(0, 900, "older"); m.post(1, 1100, "newer");
    m.tick(6000); assert(m.room_topic_timestamp == 1000);
    assert(m.sent.back().text == "older"); m.ack();
    m.tick(); assert(m.sent.back().text == "corrected clock");
    assert(m.sent.back().timestamp == 1000); m.ack();
    m.acl.clients[0].extra.room.sync_since = m.room_topic_timestamp;
    m.clock.now = 1200; m.tick(); assert(m.sent.back().text == "newer"); m.ack();
  }
  {
    // Maturity is latched without subscribers, across millis wrap and half-range.
    ticks = UINT32_MAX - 3000;
    MyMesh m; m.topic("long uptime"); m.acl.count = 0;
    m.tick(6000); assert(m.room_topic_ready && m.sent.empty());
    m.tick(UINT32_C(0x80000000));
    m.acl.count = 1; m.tick();
    assert(m.sent.back().text == "long uptime"); m.ack();
    m.acl.clients[0].extra.room.topic_seen_revision = 0;
    m.tick(UINT32_C(0x80000000));
    assert(m.sent.size() == 2); m.ack();
  }
  {
    MyMesh m; m.topic("banned topic"); m.post(0, 900, "banned post");
    assert(m.room_access.addBan(&m.policy_fs, m.acl.clients[0].id.pub_key) == mesh::RoomAccessPolicy::BanResult::Saved);
    m.tick(6000); assert(m.sent.empty());
    assert(m.acl.clients[0].extra.room.pending_ack == 0);
    assert(m.room_access.removeBan(&m.policy_fs, m.acl.clients[0].id.pub_key) == mesh::RoomAccessPolicy::BanResult::Saved);
    m.tick(); assert(m.sent.back().text == "banned post"); m.ack();
    m.tick(); assert(m.sent.back().text == "banned topic"); m.ack();
  }
  {
    // A banned first slot cannot swallow an ACK for a later allowed identity.
    MyMesh m; m.acl.count = 2;
    auto& banned = m.acl.clients[0]; auto& allowed = m.acl.clients[1];
    allowed.id.pub_key[0] = 2;
    banned.extra.room.pending_ack = allowed.extra.room.pending_ack = 0x12345678;
    banned.extra.room.push_post_timestamp = 200;
    allowed.extra.room.push_post_timestamp = 300;
    assert(m.room_access.addBan(&m.policy_fs, banned.id.pub_key) == mesh::RoomAccessPolicy::BanResult::Saved);
    const auto ban_before = banned;
    const uint32_t ack = 0x12345678;
    assert(m.processAck(reinterpret_cast<const uint8_t*>(&ack)));
    assert(memcmp(&ban_before, &banned, sizeof(banned)) == 0);
    assert(allowed.extra.room.pending_ack == 0 && allowed.extra.room.sync_since == 300);
  }
  {
    MyMesh m; auto& client = m.acl.clients[0];
    client.out_path_len = client.alt_path_len = 1;
    client.out_path[0] = 0xA1; client.alt_path[0] = 0xB2;
    m.topic("two-route topic"); m.post(0, 900, "two-route post");
    m.tick(6000);
    assert(m.sent.size() == 2 && m.sent[0].text == "two-route post");
    assert(m.sent[1].text == m.sent[0].text && m.sent[0].timestamp == m.sent[1].timestamp);
    assert(m.sent[0].path[0] == 0xA1 && m.sent[1].path[0] == 0xB2);
    assert(m.sent[0].retry_enabled && !m.sent[1].retry_enabled);
    assert(m._prefs.direct_retry_enabled == 1 && m._num_post_pushes == 1);
    assert(client.extra.room.ack_timeout == futureMillis(PUSH_TIMEOUT_BASE + 2 * PUSH_ACK_TIMEOUT_FACTOR));
    const uint32_t post_ack = client.extra.room.pending_ack;
    m.ack(); assert(client.extra.room.sync_since == 900);
    assert(!m.processAck(reinterpret_cast<const uint8_t*>(&post_ack)));
    m.tick(); assert(m.sent.size() == 4 && m.sent[2].text == "two-route topic");
    assert(m.sent[3].text == m.sent[2].text && m.sent[2].timestamp == m.sent[3].timestamp);
    assert(m._num_post_pushes == 2 && client.extra.room.sync_since == 900);
    m.ack(); assert(client.extra.room.sync_since == 900);
    assert(client.extra.room.topic_seen_revision == m.room_topic_revision);
  }
  for (bool alternate_available : {false, true}) {
    MyMesh m; auto& client = m.acl.clients[0];
    client.out_path_len = client.alt_path_len = 1;
    client.out_path[0] = 0xA1; client.alt_path[0] = 0xB2;
    m.alternate_available = alternate_available;
    m.queue_available = !alternate_available;
    m.topic("admission"); m.tick(6000);
    if (alternate_available) {
      assert(m.sent.empty() && m.releases == 1 && m._num_post_pushes == 0);
      assert(client.extra.room.pending_ack == 0);
    } else {
      assert(m.sent.size() == 1 && m.sent[0].retry_enabled && m._num_post_pushes == 1);
      m.ack();
    }
    assert(m._prefs.direct_retry_enabled == 1);
  }
  for (bool pending_topic : {false, true}) {
    MyMesh m; auto& client = m.acl.clients[0];
    m.topic("Public topic");
    if (!pending_topic) m.post(0, 900, "Public chat");
    m.tick(6000); assert(m.sent.size() == 1 && client.extra.room.pending_ack);
    const uint32_t old_ack = client.extra.room.pending_ack;
    mesh::RoomMailSettings settings; settings.mailbox_only = true;
    assert(mesh::saveRoomMailSettings(&m.policy_fs, client.id.pub_key, settings) == mesh::RoomMailResult::Success);
    assert(!m.roomClientChatEnabled(&client) && m.getUnsyncedCount(&client) == 0);
    m.tick(); assert(!client.extra.room.pending_ack && !client.extra.room.pending_topic_revision);
    assert(m.cancellations == 1 && !m.processAck(reinterpret_cast<const uint8_t*>(&old_ack)));
    for (int poll = 0; poll < 10; ++poll) m.tick(12000);
    assert(m.sent.size() == 1 && client.extra.room.sync_since == 0);
    settings.mailbox_only = false;
    assert(mesh::saveRoomMailSettings(&m.policy_fs, client.id.pub_key, settings) == mesh::RoomMailResult::Success);
    m.tick(); assert(m.sent.size() == 2); m.ack();
  }
  {
    // An intermittently connected mailbox subscriber never downloads chat or
    // the topic; corrupt saved policy remains closed to automatic delivery.
    MyMesh m; auto& client = m.acl.clients[0];
    mesh::RoomMailSettings settings; settings.mailbox_only = true;
    assert(mesh::saveRoomMailSettings(&m.policy_fs, client.id.pub_key, settings) == mesh::RoomMailResult::Success);
    m.topic("Public topic"); m.post(0, 900, "Public chat");
    for (int poll = 0; poll < 10; ++poll) m.tick(6000);
    assert(m.sent.empty() && !m.getUnsyncedCount(&client));
    m.policy_fs.put(mesh::ROOM_MAIL_PRIMARY_PATH, {'c','o','r','r','u','p','t'});
    for (int poll = 0; poll < 10; ++poll) m.tick(6000);
    assert(m.sent.empty() && !m.roomClientChatEnabled(&client));
  }
  puts("room topic delivery regressions passed");
}
