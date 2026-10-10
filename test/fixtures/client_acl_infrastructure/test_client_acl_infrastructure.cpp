#include <algorithm>
#include <cassert>
#include <cstring>
#include <iostream>
#include <vector>
#include <helpers/ClientACLResponse.h>
#include <helpers/RoutingPolicy.h>

#define REQ_TYPE_GET_ACCESS_LIST 0x05
#define REQ_TYPE_GET_TELEMETRY_DATA 0x04
#define REQ_TYPE_KEEP_ALIVE 0x02
#define SERVER_RESPONSE_DELAY 300
#define OUT_PATH_UNKNOWN 0xFF
#define PERM_ACL_ROLE_MASK 7
#define PERM_ACL_ADMIN 3

namespace mesh {
struct Identity {
  uint8_t pub_key[PUB_KEY_SIZE] = {};
  size_t copyHashTo(uint8_t* destination) const {
    *destination = pub_key[0];
    return PATH_HASH_SIZE;
  }
};
struct Utils {
  // Exercise the real packet-size admission and layout; replace crypto only.
  static int encryptThenMAC(const uint8_t*, uint8_t* destination,
                            const uint8_t* source, int length) {
    const int encrypted = (length + CIPHER_BLOCK_SIZE - 1) / CIPHER_BLOCK_SIZE * CIPHER_BLOCK_SIZE;
    memset(destination, 0, encrypted + CIPHER_MAC_SIZE);
    memcpy(destination + CIPHER_MAC_SIZE, source, length);
    return encrypted + CIPHER_MAC_SIZE;
  }
  static void sha256(uint8_t* out, int size, const uint8_t*, int, const uint8_t*, int) {
    memset(out, 0, size);
  }
};
struct Random {
  void random(uint8_t* data, size_t length) { memset(data, 0x99, length); }
};
class Mesh {
  std::vector<Packet*> allocations;
public:
  Identity self_id;
  Random rng;
  struct Tables { void markSent(Packet*) {} } tables;
  Tables* _tables = &tables;
  unsigned released = 0;
  void releasePacket(Packet*) { ++released; }
  void maybeScheduleFloodRetry(Packet*, uint8_t) {}
  void maybeScheduleDirectRetry(Packet*, uint8_t) {}
  uint8_t getTraceDirectPriority(Packet*) { return 0; }
  void replaceQueuedSelfAdvertRetries(Packet*) {}
  virtual bool sendPacket(Packet*, uint8_t, uint32_t) = 0;
  bool sendFlood(Packet*, uint32_t, uint8_t);
  bool sendFlood(Packet*, uint16_t*, uint32_t, uint8_t);
  bool sendDirect(Packet*, const uint8_t*, uint8_t, uint32_t);
  bool refuse_allocation = false;
  ~Mesh() { for (auto* packet : allocations) delete packet; }
  Packet* obtainNewPacket() {
    if (refuse_allocation) return nullptr;
    auto* packet = new Packet;
    allocations.push_back(packet);
    return packet;
  }
  uint8_t getContactTxRadio(const Identity&) { return RADIO_TX_AUTO; }
  Random* getRNG() { return &rng; }
  Packet* createPathReturn(const Identity&, const uint8_t*, const uint8_t*, uint8_t,
                           uint8_t, const uint8_t*, size_t);
  Packet* createPathReturn(const uint8_t*, const uint8_t*, const uint8_t*, uint8_t,
                           uint8_t, const uint8_t*, size_t);
  Packet* createDatagram(uint8_t, const Identity&, const uint8_t*, const uint8_t*, size_t);
};
}
struct TransportKey {
  bool null = true;
  bool isNull() const { return null; }
  uint16_t calcTransportCode(mesh::Packet*) const { return 7; }
};
struct RegionEntry { bool wildcard = false; bool isWildcard() const { return wildcard; } };
struct RegionMap {
  bool known = false;
  int getTransportKeysFor(const RegionEntry&, TransportKey* key, int) {
    key->null = !known; return known ? 1 : 0;
  }
};
struct ClientInfo {
  mesh::Identity id;
  uint8_t permissions = 3;
  uint8_t out_path_len = 0, out_path[MAX_PATH_SIZE] = {};
  uint32_t last_timestamp = 0, last_activity = 0;
  struct { struct {
    uint32_t sync_since = 0, pending_ack = 0, pending_topic_revision = 0;
    uint8_t push_failures = 0;
  } room; } extra;
  bool isAdmin() const { return (permissions & PERM_ACL_ROLE_MASK) == PERM_ACL_ADMIN; }
};
struct ACL {
  std::vector<ClientInfo> clients;
  int getNumClients() const { return clients.size(); }
  ClientInfo* getClientByIdx(int index) { return &clients.at(index); }
};
struct Clock : mesh::RTCClock {
  uint32_t getCurrentTime() override { return 100; }
  void setCurrentTime(uint32_t) override {}
};
#ifdef TEST_SENSOR
#define TARGET_CLASS SensorMesh
#else
#define TARGET_CLASS MyMesh
#endif
class TARGET_CLASS : public mesh::Mesh {
public:
  uint8_t reply_data[MAX_PACKET_PAYLOAD] = {};
  ACL acl;
  ClientInfo sender;
  Clock clock;
  unsigned queued = 0, direct_sent = 0, flood_sent = 0;
  bool refuse_queue = false;
  TransportKey default_scope;
  RegionEntry region;
  RegionEntry* recv_pkt_region = nullptr;
  RegionMap region_map;
  mesh::Packet* last_reply = nullptr;
  Clock* getRTCClock() { return &clock; }
#ifdef TEST_SENSOR
  uint8_t handleRequest(ClientInfo*, uint32_t, uint8_t, uint8_t*, size_t,
                    size_t = mesh::CLIENT_ACL_DIRECT_REPLY_CAPACITY);
#else
  int handleRequest(ClientInfo*, uint32_t, uint8_t*, size_t,
                    size_t = mesh::CLIENT_ACL_DIRECT_REPLY_CAPACITY);
  int telemetryPrefix(uint8_t*, size_t);
#endif
  void receive(mesh::Packet*, uint8_t*, size_t);
  bool admit(mesh::Packet* packet) {
    if (!packet || refuse_queue) return false;
    assert(packet->payload_len <= MAX_PACKET_PAYLOAD);
    last_reply = packet;
    ++queued;
    if (packet->isRouteDirect()) ++direct_sent; else ++flood_sent;
    return true;
  }
  bool sendPacket(mesh::Packet* packet, uint8_t, uint32_t) override { return admit(packet); }
#ifdef TEST_SENSOR
  bool sendFloodScoped(const TransportKey&, mesh::Packet*, uint32_t, uint8_t);
  bool sendFloodReply(mesh::Packet*, unsigned long, uint8_t);
#else
  bool sendFloodReply(mesh::Packet* packet, unsigned long delay, uint8_t hash_size) {
    return sendFlood(packet, delay, hash_size);
  }
#endif
  mesh::Packet* createAck(uint32_t) { return nullptr; }
  uint8_t getUnsyncedCount(ClientInfo*) { return 0; }
};
#include "production.inc"

static int request(TARGET_CLASS& target, ClientInfo* sender, uint8_t* query, size_t length,
                   size_t capacity = mesh::CLIENT_ACL_DIRECT_REPLY_CAPACITY) {
#ifdef TEST_SENSOR
  const uint8_t type = length ? query[0] : REQ_TYPE_GET_ACCESS_LIST;
  return target.handleRequest(sender, 51, type, length ? query + 1 : nullptr,
                              length ? length - 1 : 0, capacity);
#else
  return target.handleRequest(sender, 51, query, length, capacity);
#endif
}
static bool included(const ClientInfo& client) {
#ifdef TEST_SENSOR
  return client.permissions != 0;
#else
  return client.isAdmin();
#endif
}
static void fill(ACL& acl, unsigned count, bool mixed = false) {
  acl.clients.assign(count, ClientInfo{});
  for (unsigned i = 0; i < count; ++i) {
    for (unsigned j = 0; j < PUB_KEY_SIZE; ++j) acl.clients[i].id.pub_key[j] = (i + j) % 255 + 1;
    if (mixed) acl.clients[i].permissions = (i % 4 == 0) ? 0 : (i % 4 == 1) ? 1 : (i % 4 == 2) ? 3 : 4;
  }
}
static unsigned verify(TARGET_CLASS& target, int length, size_t capacity) {
  assert(length >= 4 && size_t(length) <= capacity && (length - 4) % 7 == 0);
  uint32_t tag;
  memcpy(&tag, target.reply_data, 4);
  assert(tag == 51);
  unsigned offset = 4;
  for (const auto& client : target.acl.clients) {
    if (!included(client)) continue;
    if (offset + 7 > size_t(length)) break;
    assert(!memcmp(target.reply_data + offset, client.id.pub_key, 6));
    assert(target.reply_data[offset + 6] == client.permissions);
    offset += 7;
  }
  assert(offset == unsigned(length));
  return (length - 4) / 7;
}
static void print_legacy(const uint8_t* body, unsigned count, size_t length) {
  static const char hex[] = "0123456789abcdef";
  std::cout << "LEGACY:" << count << ':';
  for (size_t i = 0; i < length; ++i) std::cout << hex[body[i] >> 4] << hex[body[i] & 15];
  std::cout << '\n';
}
int main() {
  uint8_t query[] = {REQ_TYPE_GET_ACCESS_LIST, 0, 0};
  uint8_t secret[PUB_KEY_SIZE] = {};
  unsigned paths = 0;
  for (bool flood : {false, true}) {
    for (unsigned path_len = 0; path_len < 256; ++path_len) {
      if (!mesh::Packet::isValidPathLen(path_len)) {
        assert(mesh::clientACLReplyCapacity(flood, path_len) == 0);
        continue;
      }
      ++paths;
      for (unsigned clients : {0, 1, 22, 23, 24, 25, 32, 256}) {
        for (bool mixed : {false, true}) {
          TARGET_CLASS target;
          fill(target.acl, clients, mixed);
          const size_t capacity = mesh::clientACLReplyCapacity(flood, path_len);
          const int length = request(target, &target.sender, query, sizeof(query), capacity);
          const unsigned count = verify(target, length, capacity);
          const unsigned live = std::count_if(target.acl.clients.begin(), target.acl.clients.end(), included);
          assert(count == std::min(live, unsigned((capacity - 4) / 7)));
          uint8_t path[MAX_PATH_SIZE] = {};
          mesh::Packet* packet = flood
              ? target.createPathReturn(target.sender.id, secret, path, path_len,
                                      PAYLOAD_TYPE_RESPONSE, target.reply_data, length)
              : target.createDatagram(PAYLOAD_TYPE_RESPONSE, target.sender.id, secret, target.reply_data, length);
          assert(packet && packet->payload_len <= MAX_PACKET_PAYLOAD);
          const size_t cipher_bytes = packet->payload_len - 2 * PATH_HASH_SIZE - CIPHER_MAC_SIZE;
          const size_t path_prefix = flood ? 2 + (path_len & 63) * ((path_len >> 6) + 1) : 0;
          assert(cipher_bytes >= path_prefix);
          assert(cipher_bytes - path_prefix + 2 <= MAX_FRAME_SIZE);
          if (path_len == 0 && !mixed) {
            const size_t offset = 2 * PATH_HASH_SIZE + CIPHER_MAC_SIZE + 4 + (flood ? 2 : 0);
            print_legacy(packet->payload + offset, count, packet->payload_len - offset);
          }
        }
      }
    }
  }
  assert(paths > 200);
  TARGET_CLASS target;
  fill(target.acl, 32, true);
  for (uint8_t permissions : {0, 1, 2, 4, 5, 6, 7, 255}) {
    target.sender.permissions = permissions;
    assert(request(target, &target.sender, query, sizeof(query)) == 0);
  }
  for (uint8_t permissions : {3, 131, 195}) {
    target.sender.permissions = permissions;
    assert(request(target, &target.sender, query, sizeof(query)) >= 4);
  }
  target.sender.permissions = 3;
  assert(request(target, nullptr, query, sizeof(query)) == 0);
  for (size_t length : {0, 1, 2}) {
    // Exact-sized buffers make a reserved-byte overread visible to ASan.
    std::vector<uint8_t> truncated(query, query + length);
    assert(request(target, &target.sender, truncated.data(), length) == 0);
  }
  for (unsigned index : {1, 2}) {
    query[index] = 1;
    assert(request(target, &target.sender, query, sizeof(query)) == 0);
    query[index] = 0;
  }
  for (size_t capacity = 0; capacity <= MAX_PACKET_PAYLOAD + 10; ++capacity) {
    const int length = request(target, &target.sender, query, sizeof(query), capacity);
    if (capacity < 4) assert(length == 0);
    else verify(target, length, std::min(capacity, mesh::CLIENT_ACL_DIRECT_REPLY_CAPACITY));
  }
#ifndef TEST_SENSOR
  for (size_t length : {0, 1}) {
    std::vector<uint8_t> truncated(length, REQ_TYPE_GET_TELEMETRY_DATA);
    assert(target.telemetryPrefix(truncated.data(), length) == 0);
  }
#endif
  for (bool flood : {false, true}) {
    for (uint8_t stored_path : {0, 1, 0xFE, 0xFF, 0xC0, 0x41}) {
      TARGET_CLASS receiver;
      fill(receiver.acl, 256, true);
      receiver.sender.out_path_len = stored_path;
      mesh::Packet packet;
      packet.header = flood ? ROUTE_TYPE_FLOOD : ROUTE_TYPE_DIRECT;
      packet.path_len = 0x60; // Maximum 64 bytes in the flood-return route.
      const uint8_t data[] = {51, 0, 0, 0, REQ_TYPE_GET_ACCESS_LIST, 0, 0};
      for (size_t length = 0; length < sizeof(data); ++length) {
        std::vector<uint8_t> truncated(data, data + length);
        receiver.receive(&packet, truncated.data(), length);
        assert(receiver.queued == 0);
      }
      receiver.receive(&packet, const_cast<uint8_t*>(data), sizeof(data));
      assert(receiver.queued == 1 && receiver.last_reply != nullptr);
      assert(receiver.sender.last_timestamp == 51 && receiver.sender.last_activity == 100);
      assert(receiver.last_reply->payload_len <= MAX_PACKET_PAYLOAD);
      const bool direct = !flood && mesh::Packet::isValidPathLen(stored_path);
      assert(receiver.direct_sent == unsigned(direct));
      assert(receiver.flood_sent == unsigned(!direct));
      const size_t capacity = mesh::clientACLReplyCapacity(flood, packet.path_len);
      assert(verify(receiver, 4 + 7 * std::min(192U,
          unsigned((capacity - 4) / 7)), capacity) > 0);
    }
  }
  for (bool flood : {false, true}) {
    for (uint8_t stored_path : {0, 1, 0xFE, 0xFF, 0xC0}) {
      for (int scope = 0; scope < 4; ++scope) {
        TARGET_CLASS receiver;
        fill(receiver.acl, 32, true);
        receiver.sender.last_timestamp = 50;
        receiver.sender.last_activity = 12;
        receiver.sender.out_path_len = stored_path;
        if (scope == 1) receiver.default_scope.null = false;
        if (scope >= 2) receiver.recv_pkt_region = &receiver.region;
        receiver.region_map.known = scope == 2;
        receiver.region.wildcard = scope == 3;
        mesh::Packet packet;
        packet.header = flood ? ROUTE_TYPE_FLOOD : ROUTE_TYPE_DIRECT;
        packet.path_len = 0x60;
        uint8_t data[] = {51, 0, 0, 0, REQ_TYPE_GET_ACCESS_LIST, 0, 0};
        receiver.refuse_allocation = true;
        receiver.receive(&packet, data, sizeof(data));
        assert(receiver.queued == 0);
#ifdef TEST_SENSOR
        assert(receiver.sender.last_timestamp == 50 && receiver.sender.last_activity == 12);
#endif
        receiver.refuse_allocation = false;
        receiver.refuse_queue = true;
        receiver.receive(&packet, data, sizeof(data));
        assert(receiver.queued == 0);
#ifdef TEST_SENSOR
        assert(receiver.sender.last_timestamp == 50 && receiver.sender.last_activity == 12);
#endif
        receiver.refuse_queue = false;
        receiver.receive(&packet, data, sizeof(data));
        assert(receiver.queued == 1 && receiver.sender.last_timestamp == 51);
        assert(receiver.sender.last_activity == 100);
        receiver.receive(&packet, data, sizeof(data));
#ifdef TEST_SENSOR
        assert(receiver.queued == 1); // Admission commits the Sensor replay floor.
#else
        assert(receiver.queued == 2); // Preserve Room's equal-tag retry semantics.
#endif
      }
    }
  }
  std::cout << "Room/Sensor ACL route, input, and permission checks passed\n";
}
