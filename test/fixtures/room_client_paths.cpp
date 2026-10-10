#include <cassert>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <string>
#include <vector>
#include "filesystem.h"
#include <Packet.h>
#include <helpers/RoomAccessPolicy.h>
#include <helpers/RoomClientPathCommand.h>
#include <helpers/RoomCatchUp.h>
#include <helpers/ClientPathObservation.h>
#include <helpers/ClientPathPersistence.h>
#include <helpers/RemoteCliReplyCache.h>
#include <helpers/RemoteCliRequest.h>
#include <helpers/ClientACLResponse.h>
#include <helpers/LazyPersistence.h>
#include <helpers/LogicalMessageCache.h>
#include <helpers/TxtDataHelpers.h>

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
  static bool fromHex(uint8_t* out, size_t n, const char* text) {
    if (strlen(text) != n * 2) return false;
    for (size_t index = 0; index < n; ++index) {
      const int high = cli::recentRepeaterHexNibble(text[index * 2]);
      const int low = cli::recentRepeaterHexNibble(text[index * 2 + 1]);
      if (high < 0 || low < 0) return false;
      out[index] = (high << 4) | low;
    }
    return true;
  }
  static void sha256(uint8_t* out, size_t n, const uint8_t* data, size_t length) {
    sha256(out, n, data, length, nullptr, 0);
  }
  static void sha256(uint8_t* out, size_t n, const uint8_t* left, size_t ln,
                     const uint8_t* right, size_t rn) {
    memset(out, 0, n);
    for (size_t i = 0; i < ln; ++i) out[i % n] ^= left[i];
    for (size_t i = 0; i < rn; ++i) out[i % n] ^= right[i];
  }
};
@PACKET_METHODS@
}
@CLIENT_INFO@;
@POST_INFO@;

struct FakeACL {
  ClientInfo clients[2]{};
  ClientInfo durable[2]{};
  bool retained[2]{};
  bool save_ok = true;
  unsigned saves = 0;
  int getNumClients() const { return 2; }
  ClientInfo* getClientByIdx(int i) { assert(i >= 0 && i < 2); return &clients[i]; }
  ClientInfo* getClient(const uint8_t* key, size_t length) {
    assert(length == PUB_KEY_SIZE);
    for (auto& client : clients) {
      if (memcmp(client.id.pub_key, key, length) == 0) return &client;
    }
    return nullptr;
  }
  bool save(MemoryFS* fs, bool (*filter)(ClientInfo*)) {
    ++saves;
    assert(fs != nullptr && filter != nullptr);
    if (!save_ok) return false;
    for (unsigned i = 0; i < 2; ++i) {
      retained[i] = filter(&clients[i]);
      if (retained[i]) durable[i] = clients[i];
    }
    return true;
  }
};
struct FakeClock {
  uint32_t now = 1000;
  uint32_t serial = 0;
  uint32_t getCurrentTime() const { return now; }
  uint32_t getCurrentTimeUnique() { return now + ++serial; }
};
struct FakeRng {
  void random(uint8_t* data, size_t n) { memset(data, 0xB5, n); }
};
static uint32_t ticks = 100;
static uint32_t millis() { return ticks; }
struct Sent {
  mesh::Packet packet;
  uint8_t path[MAX_PATH_SIZE]{};
  uint8_t path_len = 0;
  unsigned long delay = 0;
  bool direct = true;
  bool retry_enabled = false;
};
struct MyMesh {
  FakeACL acl;
  mesh::Identity self_id{};
  MemoryFS policy_fs;
  MemoryFS* _fs = &policy_fs;
  mesh::RoomAccessPolicy room_access;
  mesh::RemoteCliReplyCache remote_cli_reply_cache;
  mesh::LogicalMessageCache<ROOM_MESSAGE_CACHE_SIZE> recent_room_posts;
  mesh::LogicalMessageCache<ROOM_MESSAGE_CACHE_SIZE> recent_room_polls;
  int matching_peer_indexes[2] = {0, 1};
  unsigned long dirty_contacts_expiry = 0;
  uint8_t contacts_save_failures = 0;
  FakeClock clock;
  FakeRng rng;
  uint8_t reply_data[MAX_PACKET_PAYLOAD]{};
  mesh::Packet pool[64];
  unsigned used = 0, allocations = 0, releases = 0, fail_allocation = UINT32_MAX;
  bool send_ok = true;
  unsigned general_commands = 0, post_count = 0, retry_replacements = 0, _num_post_pushes = 0;
  PostInfo posts[MAX_UNSYNCED_POSTS]{};
  unsigned message_cancellations = 0;
  uint32_t cancelled_timestamp = 0;
  uint8_t cancelled_key[MAX_HASH_SIZE]{};
  uint32_t retry_timestamp = 0;
  mesh::Packet retry_packet;
  std::vector<Sent> sent;
  uint16_t default_scope = 0;
  struct Prefs {
    uint8_t direct_retry_enabled = 1;
    uint8_t path_hash_mode = 0;
  } _prefs;
  MyMesh() {
    metadata_filesystem = &policy_fs;
    assert(room_access.load(&policy_fs));
    for (unsigned i = 0; i < 2; ++i) {
      ClientInfo& c = acl.clients[i];
      memset(&c, 0, sizeof(c));
      memset(c.id.pub_key, i + 1, PUB_KEY_SIZE);
      memset(c.shared_secret, i + 0x40, PUB_KEY_SIZE);
      c.permissions = PERM_ACL_READ_WRITE;
      c.out_path_len = 1;
      c.out_path[0] = i + 0xA1;
      c.alt_path_len = OUT_PATH_UNKNOWN;
      c.observed_path_len = OUT_PATH_UNKNOWN;
      c.last_timestamp = 99;
    }
  }
  FakeClock* getRTCClock() { return &clock; }
  FakeRng* getRNG() { return &rng; }
  unsigned long futureMillis(unsigned long delay) { return ticks + delay; }
  mesh::Packet* obtainNewPacket() {
    if (++allocations == fail_allocation) return nullptr;
    assert(used < 64);
    return &pool[used++];
  }
  void releasePacket(mesh::Packet* p) { assert(p != nullptr); ++releases; }
  mesh::Packet* createDatagram(uint8_t type, const mesh::Identity&, const uint8_t*,
                               const uint8_t* data, size_t n) {
    auto p = obtainNewPacket();
    if (p) {
      p->header = type << PH_TYPE_SHIFT;
      p->payload_len = n;
      memcpy(p->payload, data, n);
    }
    return p;
  }
  mesh::Packet* createPathReturn(const mesh::Identity& id, const uint8_t* secret,
      const uint8_t*, uint8_t, uint8_t type, const uint8_t* data, size_t n) {
    return createDatagram(type, id, secret, data, n);
  }
  mesh::Packet* createAck(uint32_t ack) {
    mesh::Identity empty{}; uint8_t secret[PUB_KEY_SIZE]{};
    return createDatagram(PAYLOAD_TYPE_ACK, empty, secret, (const uint8_t*)&ack, 4);
  }
  mesh::Packet* createMultiAck(uint32_t ack, uint8_t) { return createAck(ack); }
  bool sendDirect(mesh::Packet* p, const uint8_t* path, uint8_t n, unsigned long delay = 0) {
    assert(p && mesh::Packet::isValidPathLen(n));
    Sent value; value.packet = *p; value.path_len = n; value.delay = delay;
    value.retry_enabled = _prefs.direct_retry_enabled != 0;
    memcpy(value.path, path, mesh::encodedClientPathByteLength(n));
    sent.push_back(value);
    return send_ok;
  }
  bool sendFloodReply(mesh::Packet* p, unsigned long delay, uint8_t hash_size) {
    Sent value; value.packet = *p; value.delay = delay; value.direct = false;
    value.path_len = (hash_size - 1) << 6;
    sent.push_back(value);
    return send_ok;
  }
  bool sendFloodScoped(uint16_t, mesh::Packet* p, unsigned long delay, uint8_t hash_size) {
    return sendFloodReply(p, delay, hash_size);
  }
  void replaceActiveMessageRetries(mesh::Packet* p, const uint8_t*, uint32_t timestamp) {
    ++retry_replacements; retry_packet = *p; retry_timestamp = timestamp;
  }
  void handleCommand(uint32_t, char* command, char* reply, int, uint8_t) {
    ++general_commands;
    if (handleRoomManagementCommand(command, reply)) return;
    strcpy(reply, "OK admin");
  }
  bool addPost(ClientInfo*, const char*) { ++post_count; return true; }
  void serviceRoomQuotas();
  uint8_t getUnsyncedCount(ClientInfo*);
  bool cancelActiveMessageRetries(const uint8_t* key, uint32_t timestamp) {
    ++message_cancellations; cancelled_timestamp = timestamp;
    memcpy(cancelled_key, key, MAX_HASH_SIZE); return true;
  }
  int getExtraAckTransmitCount() { return 0; }
  int handleRequest(ClientInfo*, uint32_t, uint8_t*, size_t, size_t) { return 0; }
  static bool saveFilter(ClientInfo*);
  bool handleClientPathCommand(ClientInfo*, char*, char*);
  bool executeClientPathCommand(ClientInfo*, mesh::RoomClientPathCommand, const char*, char*);
  bool handleRoomCatchUpCommand(ClientInfo*, char*, char*);
  bool applyRoomCatchUpCommand(ClientInfo*, const char*, uint32_t, char*);
  bool setRoomClientPath(ClientInfo*, const char*, const char*, char*);
  bool handleRoomManagementCommand(char*, char*);
  bool sendClientReply(ClientInfo*, mesh::Packet*, unsigned long, uint8_t);
  bool pushRoomTextToClient(ClientInfo*, uint32_t, const mesh::Identity&, const char*, uint32_t);
  void onPeerDataRecv(mesh::Packet*, uint8_t, int, const uint8_t*, uint8_t*, size_t);
  void command(const char* text, uint32_t timestamp = 100, unsigned sender = 0,
               uint32_t logical = 0, uint8_t flags = TXT_TYPE_CLI_COMMAND) {
    std::vector<uint8_t> data(5 + strlen(text) + mesh::RemoteCliRequest::EXTENSION_SIZE + 1);
    memcpy(data.data(), &timestamp, 4); data[4] = flags << 2;
    memcpy(data.data() + 5, text, strlen(text));
    size_t length = 5 + strlen(text);
    if (logical) length = mesh::RemoteCliRequest::append(data.data(), data.size(), 5, strlen(text), logical);
    mesh::Packet packet; packet.header = ROUTE_TYPE_DIRECT;
    onPeerDataRecv(&packet, PAYLOAD_TYPE_TXT_MSG, sender, acl.clients[sender].shared_secret,
                   data.data(), length);
  }
  std::string reply() const {
    if (sent.empty()) return "";
    const auto& p = sent.back().packet;
    return std::string((const char*)p.payload + 5, p.payload_len - 5);
  }
  void poll(uint32_t timestamp, uint32_t since, unsigned sender = 0) {
    uint8_t data[10]{};
    memcpy(data, &timestamp, 4); data[4] = REQ_TYPE_KEEP_ALIVE;
    memcpy(data + 5, &since, 4);
    mesh::Packet packet; packet.header = ROUTE_TYPE_DIRECT;
    onPeerDataRecv(&packet, PAYLOAD_TYPE_REQ, sender, acl.clients[sender].shared_secret, data, 9);
  }
};
@PRODUCTION_METHODS@
@TEST_CASES@
