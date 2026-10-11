#include <cassert>
#include <cmath>
#include <cstdint>
#include <cstring>
#include <iostream>
#include <limits>
#include <vector>
#include <helpers/ClientACLResponse.h>
#include "LPPDataHelpers.h"
#include "cayenne_lpp_1_6_1_metadata.inc"

#define PERM_ACL_ROLE_MASK 7
#define PERM_ACL_READ_ONLY 1
#define REQ_TYPE_SUBSCRIBE 8

struct ClientInfo {
  uint8_t permissions = 3;
  int id = 1;
  uint8_t shared_secret[32] = {}, out_path[MAX_PATH_SIZE] = {}, out_path_len = 0;
  struct { struct {
    uint32_t push_tag = 0, expiry_timestamp = 0;
    uint16_t scope_region_id = 0;
    uint8_t min_deltas_len = 0, min_deltas[14] = {}, prev_telem[14] = {};
  } sensor; } extra;
  bool isAdmin() const { return (permissions & 7) == 3; }
};
struct TransportKey {};
struct RegionEntry {
  uint16_t id = 1;
  char name[31] = "test";
  bool isWildcard() const { return false; }
};
struct RegionMap {
  RegionEntry region;
  RegionEntry* getDefaultRegion() { return &region; }
  RegionEntry* findById(uint16_t id) { return id == region.id ? &region : nullptr; }
  int getTransportKeysFor(const RegionEntry&, TransportKey*, int) { return 1; }
};
struct Telemetry {
  std::vector<uint8_t> bytes;
  const size_t capacity;
  explicit Telemetry(size_t max_size) : capacity(max_size) {}
  bool add(uint8_t channel, uint8_t type) {
    const size_t size = 2 + LPPData::getDataSize(type);
    if (bytes.size() + size > capacity) return false;
    bytes.push_back(channel);
    bytes.push_back(type);
    bytes.insert(bytes.end(), size - 2, 0);
    return true;
  }
  uint8_t* getBuffer() { return bytes.data(); }
  uint8_t getSize() { return bytes.size(); }
};
struct Clock {
  uint32_t getCurrentTime() { return 100; }
  uint32_t getCurrentTimeUnique() { return 100; }
};
struct ACL {
  std::vector<ClientInfo> clients;
  int getNumClients() { return clients.size(); }
  ClientInfo* getClientByIdx(int index) { return &clients.at(index); }
};
class SensorMesh {
public:
  SensorMesh();
  uint8_t reply_data[MAX_PACKET_PAYLOAD] = {};
  Telemetry telemetry;
  Clock clock;
  RegionMap region_map;
  RegionEntry* recv_pkt_region = nullptr;
  ACL acl;
  struct { uint8_t path_hash_mode = 0; } _prefs;
  bool refuse_allocation = false, refuse_queue = false;
  bool tracker_mode = false;
  unsigned allocations = 0, attempts = 0, queued = 0;
  mesh::Packet owned_packet;
  Clock* getRTCClock() { return &clock; }
  bool isTrackerModeEnabled() const { return tracker_mode; }
  bool telemHasChanged(ClientInfo*);
  void snapshotTelemetry(ClientInfo*);
  uint8_t handleRequest(ClientInfo*, uint32_t, uint8_t, uint8_t*, size_t,
                        size_t = mesh::CLIENT_ACL_DIRECT_REPLY_CAPACITY);
  void pushSubscriptions();
  // Replace only allocator/queue boundaries. The production change detector,
  // subscription parser, and complete subscription loop body execute below.
  mesh::Packet* createDatagram(uint8_t, int, const uint8_t*, const uint8_t*, size_t length) {
    ++allocations;
    if (length > mesh::CLIENT_ACL_DIRECT_REPLY_CAPACITY) return nullptr;
    return refuse_allocation ? nullptr : &owned_packet;
  }
  bool sendDirect(mesh::Packet*, const uint8_t*, uint8_t, uint32_t) {
    ++attempts;
    if (refuse_queue) return false;
    ++queued;
    return true;
  }
  bool sendFloodScoped(const TransportKey&, mesh::Packet* p, uint32_t delay, uint8_t) {
    return sendDirect(p, nullptr, 0, delay);
  }
};
#include "production.inc"

static unsigned subscribe(SensorMesh& target, ClientInfo& client, uint8_t type,
                          unsigned delta_size, unsigned payload_size = 255, uint32_t tag = 123) {
  uint8_t request[8 + 14] = {};
  const uint16_t timeout = 600;
  memcpy(request, &tag, 4);
  memcpy(request + 4, &timeout, 2);
  request[7] = 2 + delta_size;
  request[8] = 1;
  request[9] = type;
  const size_t length = payload_size == 255 ? 8 + 2 + delta_size : payload_size;
  return target.handleRequest(&client, 51, REQ_TYPE_SUBSCRIBE, request, length);
}
static void input_checks() {
  for (unsigned type = 0; type < 256; ++type) {
    if (LPPData::isScalarType(type)) continue;
    SensorMesh target;
    ClientInfo client;
    const ClientInfo original = client;
    const unsigned size = LPPData::getDataSize(type);
    assert(subscribe(target, client, type, size) == 8);
    assert(client.extra.sensor.min_deltas_len == 0);
    assert(!memcmp(&client.extra.sensor, &original.extra.sensor, sizeof(client.extra.sensor)));
    // Defensive detector must also reject unsupported records already in RAM.
    client.extra.sensor.min_deltas_len = 2 + size;
    client.extra.sensor.min_deltas[0] = 1;
    client.extra.sensor.min_deltas[1] = type;
    assert(!target.telemHasChanged(&client));
  }
  SensorMesh target;
  ClientInfo client;
  assert(subscribe(target, client, LPP_VOLTAGE, 2) == 12);
  const ClientInfo original = client;
  for (unsigned length = 0; length < 12; ++length) {
    assert(subscribe(target, client, LPP_VOLTAGE, 2, length, 456) == (length < 8 ? 0 : 8));
    assert(!memcmp(&client.extra.sensor, &original.extra.sensor, sizeof(client.extra.sensor)));
  }
  assert(subscribe(target, client, LPP_GPS, 9, 255, 456) == 8);
  assert(!memcmp(&client.extra.sensor, &original.extra.sensor, sizeof(client.extra.sensor)));
}
static void scalar_checks() {
  unsigned supported = 0;
  for (unsigned type = 0; type < 256; ++type) {
    if (!LPPData::isScalarType(type)) continue;
    ++supported;
    const unsigned size = LPPData::getDataSize(type);
    assert(size >= 1 && size <= 4);
    assert(size == CayenneLPP::getTypeSize(type));
    assert(LPPData::getMultiplier(type) == CayenneLPP::getTypeMultiplier(type));
    assert(LPPData::isSigned(type) == CayenneLPP::getTypeSigned(type));
    SensorMesh target;
    ClientInfo client;
    assert(subscribe(target, client, type, size) == 12);
    assert(client.extra.sensor.min_deltas_len == 2 + size);
  }
  assert(supported == 22);
  assert(LPPData::getDataSize(LPP_RELATIVE_HUMIDITY) == 1);
  assert(LPPData::getMultiplier(LPP_RELATIVE_HUMIDITY) == 2);
  uint8_t humidity[] = {127};
  assert(LPPData::getFloat(humidity, 1, 2, false) == 63.5f);
  uint8_t encoded[4] = {};
  assert(LPPData::putFloat(encoded, 63.5f, 1, 2, false) == 1);
  assert(encoded[0] == 127);
  for (uint8_t type : {LPP_VOLTAGE, LPP_CURRENT}) {
    uint8_t buffer[4] = {};
    assert(LPPData::isSigned(type));
    const uint32_t multiplier = LPPData::getMultiplier(type);
    assert(LPPData::putFloat(buffer, -1.25f, 2, multiplier, true) == 2);
    assert(LPPData::getFloat(buffer, 2, multiplier, true) == -1.25f);
    LPPReader reader(buffer, 2);
    float value = 0;
    assert(type == LPP_VOLTAGE ? reader.readVoltage(value) : reader.readCurrent(value));
    assert(value == -1.25f);
    LPPWriter writer(buffer, sizeof(buffer));
    assert(type == LPP_VOLTAGE ? writer.writeVoltage(1, -1.25f) : writer.writeData(1, type, -1.25f));
    assert(writer.length() == 4);
    assert(buffer[0] == 1 && buffer[1] == type);
    assert(LPPData::getFloat(buffer + 2, 2, multiplier, true) == -1.25f);
  }
  SensorMesh target;
  ClientInfo client;
  assert(subscribe(target, client, LPP_RELATIVE_HUMIDITY, 1) == 12);
  target.telemetry.bytes = {1, LPP_RELATIVE_HUMIDITY, 127, 2, LPP_VOLTAGE, 1, 104};
  assert(target.telemHasChanged(&client));
  target.snapshotTelemetry(&client);
  assert(client.extra.sensor.prev_telem[2] == 127);
  assert(!target.telemHasChanged(&client));
}
static void admission_checks() {
  for (uint8_t stored_path : {0, 1, 0xFE, 0xFF}) {
    for (bool refuse_allocation : {false, true}) {
      SensorMesh target;
      target.acl.clients.resize(1);
      ClientInfo& client = target.acl.clients[0];
      client.out_path_len = stored_path;
      assert(subscribe(target, client, LPP_VOLTAGE, 2) == 12);
      target.telemetry.bytes = {1, LPP_VOLTAGE, 1, 104};
      target.refuse_allocation = refuse_allocation;
      target.refuse_queue = !refuse_allocation;
      target.pushSubscriptions();
      assert(target.queued == 0);
      assert(target.telemHasChanged(&client));
      assert(client.extra.sensor.prev_telem[2] == 0);
      assert(client.extra.sensor.prev_telem[3] == 0);
      target.refuse_allocation = target.refuse_queue = false;
      target.pushSubscriptions();
      assert(target.queued == 1);
      assert(client.extra.sensor.prev_telem[2] == 1);
      assert(client.extra.sensor.prev_telem[3] == 104);
      target.pushSubscriptions();
      assert(target.queued == 1);
    }
  }
}
static void tracker_mode_checks() {
  for (uint8_t stored_path : {0, 0xFF}) {
    SensorMesh target;
    target.acl.clients.resize(1);
    ClientInfo& client = target.acl.clients[0];
    client.out_path_len = stored_path;
    assert(subscribe(target, client, LPP_VOLTAGE, 2) == 12);
    target.telemetry.bytes = {1, LPP_VOLTAGE, 1, 104};
    const auto original = client.extra.sensor;
    target.tracker_mode = true;
    for (unsigned repeat = 0; repeat < 3; ++repeat) target.pushSubscriptions();
    assert(!target.allocations && !target.attempts && !target.queued);
    assert(!memcmp(&client.extra.sensor, &original, sizeof(original)));
    assert(target.telemHasChanged(&client));

    target.tracker_mode = false;
    target.refuse_queue = true;
    target.pushSubscriptions();
    assert(target.allocations == 1 && target.attempts == 1 && !target.queued);
    assert(target.telemHasChanged(&client)); // An admitted-later push remains pending.
    target.refuse_queue = false;
    target.tracker_mode = true;
    for (unsigned repeat = 0; repeat < 3; ++repeat) target.pushSubscriptions();
    assert(target.allocations == 1 && target.attempts == 1 && !target.queued);
    assert(!memcmp(&client.extra.sensor, &original, sizeof(original)));

    target.tracker_mode = false;
    target.pushSubscriptions();
    assert(target.allocations == 2 && target.attempts == 2 && target.queued == 1);
    assert(!target.telemHasChanged(&client));
    target.pushSubscriptions();
    assert(target.allocations == 2 && target.queued == 1);
  }
}
static void writer_checks() {
  uint8_t buffer[16];
  memset(buffer, 0xAA, sizeof(buffer));
  LPPWriter writer(buffer, sizeof(buffer));
  for (float value : {std::numeric_limits<float>::quiet_NaN(),
                      std::numeric_limits<float>::infinity(),
                      -std::numeric_limits<float>::infinity()}) {
    assert(!writer.writeData(1, LPP_VOLTAGE, value));
    assert(writer.length() == 0);
    for (uint8_t byte : buffer) assert(byte == 0xAA);
  }
  for (uint8_t type : {LPP_GPS, LPP_ACCELEROMETER, LPP_GYROMETER, LPP_COLOUR, LPP_POLYLINE, 0xFF}) {
    assert(!writer.writeData(1, type, 1.0f));
    assert(writer.length() == 0);
    for (uint8_t byte : buffer) assert(byte == 0xAA);
  }
  assert(writer.writeData(1, LPP_RELATIVE_HUMIDITY, 63.5f));
  assert(writer.length() == 3);
  assert(buffer[0] == 1 && buffer[1] == LPP_RELATIVE_HUMIDITY && buffer[2] == 127);
  for (unsigned i = 3; i < sizeof(buffer); ++i) assert(buffer[i] == 0xAA);
  uint8_t short_buffer[2] = {0xAA, 0xAA};
  LPPWriter short_writer(short_buffer, sizeof(short_buffer));
  assert(!short_writer.writeData(1, LPP_VOLTAGE, 3.6f));
  assert(short_writer.length() == 0 && short_buffer[0] == 0xAA && short_buffer[1] == 0xAA);
}
static void capacity_checks() {
  SensorMesh target;
  assert(target.telemetry.capacity + 8 <= mesh::CLIENT_ACL_DIRECT_REPLY_CAPACITY);
  while (target.telemetry.add(1, LPP_RELATIVE_HUMIDITY)) {}
  assert(size_t(target.telemetry.getSize()) + 8 <= mesh::CLIENT_ACL_DIRECT_REPLY_CAPACITY);
  target.acl.clients.resize(1);
  ClientInfo& client = target.acl.clients[0];
  assert(subscribe(target, client, LPP_VOLTAGE, 2) == 12);
  // Defense if an oversized collection nevertheless reaches the push loop.
  target.telemetry.bytes = {1, LPP_VOLTAGE, 1, 104};
  target.telemetry.bytes.resize(mesh::CLIENT_ACL_DIRECT_REPLY_CAPACITY - 7, 0);
  target.pushSubscriptions();
  assert(target.allocations == 0);
  assert(target.queued == 0);
  assert(target.telemHasChanged(&client));
  target.telemetry.bytes.resize(4);
  target.pushSubscriptions();
  assert(target.queued == 1);
}
static void conversion_checks() {
  uint8_t encoded[12] = {};
  for (uint8_t size = 1; size <= 4; ++size) {
    assert(LPPData::putFloat(encoded, -12.0f, size, 1, true) == size);
    assert(LPPData::getFloat(encoded, size, 1, true) == -12.0f);
  }
  for (uint8_t size = 1; size <= 4; ++size) {
    const uint32_t mask = size == 4 ? UINT32_MAX : (uint32_t(1) << (8 * size)) - 1;
    const uint32_t sign_bit = uint32_t(1) << (8 * size - 1);
    uint8_t expected[4] = {};
    uint32_t raw = sign_bit;
    for (unsigned i = size; i > 0; --i) { expected[i - 1] = raw & 255; raw >>= 8; }
    const float minimum = -(float)sign_bit;
    assert(LPPData::putFloat(encoded, minimum, size, 1, true) == size);
    assert(!memcmp(encoded, expected, size));
    assert(LPPData::getFloat(encoded, size, 1, true) == minimum);
    raw = sign_bit - 1;
    for (unsigned i = size; i > 0; --i) { expected[i - 1] = raw & 255; raw >>= 8; }
    const float maximum = (float)(sign_bit - 1);
    assert(LPPData::putFloat(encoded, maximum, size, 1, true) == size);
    assert(!memcmp(encoded, expected, size));
    assert(LPPData::putFloat(encoded, maximum * 2, size, 1, true) == size);
    assert(!memcmp(encoded, expected, size));
    assert(LPPData::putFloat(encoded, minimum * 2, size, 1, true) == size);
    assert(encoded[0] == 128);
    for (unsigned i = 1; i < size; ++i) assert(encoded[i] == 0);
    raw = mask;
    for (unsigned i = size; i > 0; --i) { expected[i - 1] = raw & 255; raw >>= 8; }
    assert(LPPData::putFloat(encoded, (float)mask, size, 1, false) == size);
    assert(!memcmp(encoded, expected, size));
    assert(LPPData::putFloat(encoded, (float)mask * 2, size, 1, false) == size);
    assert(!memcmp(encoded, expected, size));
    assert(LPPData::putFloat(encoded, -1.0f, size, 1, false) == size);
    for (unsigned i = 0; i < size; ++i) assert(encoded[i] == 0);
  }
  uint8_t all_ones[] = {255, 255, 255, 255};
  float rounded = LPPData::getFloat(all_ones, 4, 1, false);
  assert(LPPData::putFloat(encoded, rounded, 4, 1, false) == 4);
  assert(!memcmp(encoded, all_ones, 4));
  for (float value : {std::numeric_limits<float>::quiet_NaN(),
                      std::numeric_limits<float>::infinity(),
                      -std::numeric_limits<float>::infinity()}) {
    memset(encoded, 0xAA, sizeof(encoded));
    assert(LPPData::putFloat(encoded, value, 4, 1, false) == 0);
    for (uint8_t byte : encoded) assert(byte == 0xAA);
  }
  for (uint8_t size : {0, 5, 6, 9}) {
    memset(encoded, 0xAA, sizeof(encoded));
    assert(LPPData::putFloat(encoded, 12.0f, size, 1, true) == 0);
    assert(encoded[0] == 0xAA);
    assert(LPPData::getFloat(encoded, size, 1, true) == 0.0f);
  }
  assert(LPPData::putFloat(encoded, 12.0f, 1, 0, true) == 0);
  assert(LPPData::getFloat(encoded, 1, 0, true) == 0.0f);
}
int main(int argc, char** argv) {
  assert(argc == 2);
  if (!strcmp(argv[1], "scalar")) scalar_checks();
  else if (!strcmp(argv[1], "input")) input_checks();
  else if (!strcmp(argv[1], "admission")) admission_checks();
  else if (!strcmp(argv[1], "conversion")) conversion_checks();
  else if (!strcmp(argv[1], "capacity")) capacity_checks();
  else if (!strcmp(argv[1], "writer")) writer_checks();
  else if (!strcmp(argv[1], "tracker")) tracker_mode_checks();
  else assert(false);
  std::cout << "Sensor subscription checks passed\n";
}
