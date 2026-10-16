// Execute production cache reads, packet restoration, pool reuse and sharing.
// Only storage peripherals and the final radio queue boundary are isolated.
#include "FakeFilesystem.h"
#include <Packet.h>
#include <helpers/StaticPoolPacketManager.h>
#include <cstdlib>
#include <string>

#define FILESYSTEM FakeFilesystem
using mesh::FLOOD_RETRY_POLICY_DEFAULT;
using mesh::RADIO_TX_AUTO;

class DataStore {
public:
  FakeFilesystem* _fs;
#include "advert_state.h"
  explicit DataStore(FakeFilesystem& fs) : _fs(&fs) {}
  File openRead(FakeFilesystem* fs, const char* path) { return fs->open(path); }
  uint8_t getBlobByKey(const uint8_t[], int, uint8_t[]);
  bool putBlobByKey(const uint8_t[], int, const uint8_t[], uint8_t);
  bool queueAdvertByKey(const uint8_t[], int, const uint8_t[], uint8_t);
};
#include "share_store.h"

struct ContactInfo {
  struct { uint8_t pub_key[PUB_KEY_SIZE] = {}; } id;
};

class BaseChatMesh {
public:
  DataStore& store;
  StaticPoolPacketManager* _mgr;
  unsigned _err_flags = 0;
  bool receive_context_active = false;
  uint8_t receive_context_profile = 0;
  uint32_t receive_context_generation = 0;
  uint8_t temp_buf[MAX_TRANS_UNIT] = {};
  unsigned released = 0, sent = 0;
  mesh::Packet last;
  struct Tables {
    unsigned marked = 0;
    void markSent(const mesh::Packet*) { ++marked; }
  } table, *_tables = &table;
  BaseChatMesh(DataStore& s, StaticPoolPacketManager& p) : store(s), _mgr(&p) {}
  int getBlobByKey(const uint8_t* key, int length, uint8_t* bytes) {
    return store.getBlobByKey(key, length, bytes);
  }
  mesh::Packet* obtainNewPacket();
  void releasePacket(mesh::Packet* packet) { ++released; _mgr->free(packet); }
  bool shareContactZeroHop(const ContactInfo&);
  void sendZeroHop(mesh::Packet*, uint16_t*, uint32_t = 0);
  bool sendPacket(mesh::Packet* packet, uint8_t, uint32_t) {
    // The actual Dispatcher shape guards still apply at this isolated final
    // boundary; no radio, crypto verification or RF delivery is simulated.
    const bool accepted = !packet->violatesRoutePolicy()
        && mesh::Packet::isValidPathLen(packet->path_len)
        && packet->payload_len <= MAX_PACKET_PAYLOAD;
    if (accepted) { ++sent; last = *packet; }
    releasePacket(packet);
    return accepted;
  }
};
#include "share_methods.h"

static void require(bool condition, const char* message) {
  if (condition) return;
  fprintf(stderr, "CHECK: %s\n", message);
  abort();
}

int main(int argc, char** argv) {
  require(argc == 2, "one share scenario is required");
  const std::string scenario = argv[1];
  FakeFilesystem fs;
  DataStore store(fs);
  StaticPoolPacketManager pool(1);
  BaseChatMesh chat(store, pool);
  ContactInfo contact;
  contact.id.pub_key[0] = 0x42;
  mesh::Packet advert;
  advert.header = ROUTE_TYPE_FLOOD | (PAYLOAD_TYPE_ADVERT << PH_TYPE_SHIFT);
  advert.setPathHashSizeAndCount(1, 2);
  advert.path[0] = 0x12;
  advert.path[1] = 0x34;
  if (scenario == "short3") advert.path_len = 0;
  const size_t fixed = PUB_KEY_SIZE + 4 + SIGNATURE_SIZE;
  const char name[] = "Probe node";
  advert.payload_len = fixed + sizeof(name);
  memset(advert.payload, 0xA5, advert.payload_len);
  memcpy(advert.payload, contact.id.pub_key, PUB_KEY_SIZE);
  advert.payload[fixed] = 0x81;  // ADV_TYPE_CHAT | ADV_NAME_MASK.
  memcpy(advert.payload + fixed + 1, name, sizeof(name) - 1);
  std::vector<uint8_t> expected_payload(advert.payload, advert.payload + advert.payload_len);
  uint8_t bytes[MAX_TRANS_UNIT];
  const auto length = advert.writeTo(bytes);
  require(store.putBlobByKey(contact.id.pub_key, PUB_KEY_SIZE, bytes, length),
          "valid cache preparation failed");
  char path[64];
  makeBlobPath(contact.id.pub_key, PUB_KEY_SIZE, path, sizeof(path));
  bool expected = scenario == "valid" || scenario == "valid-reused"
      || scenario == "valid-min" || scenario == "valid-max" || scenario == "ram-valid";
  bool allocation_failed = false;
  mesh::Packet* held = nullptr;

  if (scenario == "short1") fs.read_limit = 1;
  else if (scenario == "short3") fs.read_limit = 3;
  else if (scenario == "truncated-path") fs.files.at(path).resize(3);
  else if (scenario == "reserved-path") fs.files.at(path)[1] = 0xC0;
  else if (scenario == "bad-version") fs.files.at(path)[0] |= 0x40;
  else if (scenario == "truncated-transport") {
    fs.files.at(path)[0] = ROUTE_TYPE_TRANSPORT_FLOOD | (PAYLOAD_TYPE_ADVERT << PH_TYPE_SHIFT);
    fs.files.at(path).resize(3);
  } else if (scenario == "wrong-type") {
    fs.files.at(path)[0] = ROUTE_TYPE_FLOOD | (PAYLOAD_TYPE_GRP_TXT << PH_TYPE_SHIFT);
  } else if (scenario == "short-advert") fs.files.at(path).resize(4 + fixed - 1);
  else if (scenario == "oversize-advert") fs.files.at(path).resize(4 + fixed + MAX_ADVERT_DATA_SIZE + 1);
  else if (scenario == "wrong-key") fs.files.at(path)[4 + PUB_KEY_SIZE - 1] ^= 0x80;
  else if (scenario == "empty-cache") fs.files.at(path).clear();
  else if (scenario == "missing-cache") fs.files.erase(path);
  else if (scenario == "no-allocation") { held = pool.allocNew(); allocation_failed = true; }
  else if (scenario == "valid-min") {
    fs.files.at(path).resize(4 + fixed);
    expected_payload.resize(fixed);
  } else if (scenario == "valid-max") {
    fs.files.at(path).resize(4 + fixed + MAX_ADVERT_DATA_SIZE);
    expected_payload.resize(fixed + MAX_ADVERT_DATA_SIZE);
  }
  else if (scenario == "ram-valid" || scenario == "ram-wrong-key") {
    ContactInfo cached = contact;
    if (scenario == "ram-wrong-key") bytes[4 + PUB_KEY_SIZE - 1] ^= 0x80;
    require(store.queueAdvertByKey(cached.id.pub_key, PUB_KEY_SIZE, bytes, length),
            "RAM advert preparation failed");
    fs.files.erase(path);
  } else require(scenario == "valid" || scenario == "valid-reused", "unknown share scenario");

  if (scenario == "valid-reused" || scenario == "short1") {
    // Actual pool reuse retains the prior header. obtainNewPacket resets
    // lengths, so ignoring readFrom's early failure can send an empty ACK.
    auto* previous = pool.allocNew();
    previous->header = ROUTE_TYPE_DIRECT | (PAYLOAD_TYPE_ACK << PH_TYPE_SHIFT);
    pool.free(previous);
  }

  const bool result = chat.shareContactZeroHop(contact);
  if (expected) {
    require(result && chat.sent == 1, "valid cached advert was not shared");
    require(chat.last.getPayloadType() == PAYLOAD_TYPE_ADVERT,
            "shared packet type changed");
    require(chat.last.getRouteType() == ROUTE_TYPE_TRANSPORT_DIRECT && chat.last.path_len == 0,
            "valid share must remain zero-hop transport-direct");
    require(chat.last.transport_codes[0] == 0 && chat.last.transport_codes[1] == 0,
            "valid share must retain zero transport codes");
    require(!memcmp(chat.last.payload, contact.id.pub_key, PUB_KEY_SIZE),
            "valid share identity changed");
    require(chat.last.payload_len == expected_payload.size()
                && !memcmp(chat.last.payload, expected_payload.data(), expected_payload.size()),
            "valid share must preserve every cached payload byte");
    require(chat.table.marked == 1 && chat.released == 1,
            "valid share must mark sent once and retire once");
  } else {
    require(!result && chat.sent == 0, "invalid cached advert must fail without transmission");
    require(chat.table.marked == 0, "rejected advert must not be marked sent");
    const unsigned releases = scenario == "empty-cache" || scenario == "missing-cache"
        || allocation_failed ? 0 : 1;
    require(chat.released == releases, "failed share must release exactly its owned packet");
  }
  if (held) pool.free(held);
  require(pool.getFreeCount() == 1, "share leaked or double-freed its packet");
  printf("PASS: cached advert share %s\n", scenario.c_str());
}
