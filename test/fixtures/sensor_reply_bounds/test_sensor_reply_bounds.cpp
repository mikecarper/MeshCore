#include <algorithm>
#include <array>
#include <cassert>
#include <cmath>
#include <cstring>
#include <iostream>
#include <vector>
#include <helpers/ClientACLResponse.h>
#include <helpers/sensors/LPPDataHelpers.h>
#include "../room_history_store/filesystem.h"
#include <helpers/RoomBoardProtocol.h>
#include <helpers/RoomMailProtocol.h>
#include <helpers/RoomAccessPolicy.h>

#include "telemetry_capacity.inc"
#include "telemetry_access.inc"
#define REQ_TYPE_GET_TELEMETRY_DATA SENSOR_REQ_TYPE_GET_TELEMETRY_DATA
#define PERM_ACL_GUEST 0
#define REQ_TYPE_GET_AVG_MIN_MAX 0x04
#define PERM_ACL_ROLE_MASK 7
#define PERM_ACL_READ_ONLY 1
#define PERM_ACL_READ_WRITE 2
#define PERM_ACL_ADMIN 3
#define TELEM_PERM_BASE 1
#define TELEM_PERM_LOCATION 2
#define TELEM_PERM_ENVIRONMENT 4
#define TELEM_CHANNEL_SELF 1
#define GPS_READ_INTERVAL_SECS 60

namespace mesh {
struct Identity {
  uint8_t pub_key[PUB_KEY_SIZE] = {};
  size_t copyHashTo(uint8_t* destination) const { *destination = 0x42; return PATH_HASH_SIZE; }
};
struct Utils {
  // The production packet builders retain their exact admission and layout.
  // Cryptography alone is replaced with deterministic padded plaintext.
  static int encryptThenMAC(const uint8_t*, uint8_t* destination,
                            const uint8_t* source, int length) {
    const int encrypted = (length + CIPHER_BLOCK_SIZE - 1) / CIPHER_BLOCK_SIZE * CIPHER_BLOCK_SIZE;
    memset(destination, 0, encrypted + CIPHER_MAC_SIZE);
    memcpy(destination + CIPHER_MAC_SIZE, source, length);
    return encrypted + CIPHER_MAC_SIZE;
  }
};
struct Random { void random(uint8_t* data, size_t length) { memset(data, 0x99, length); } };
class Mesh {
  std::vector<Packet*> allocations;
public:
  Identity self_id;
  Random rng;
  ~Mesh() { for (auto* packet : allocations) delete packet; }
  Packet* obtainNewPacket() { auto* packet = new Packet; allocations.push_back(packet); return packet; }
  uint8_t getContactTxRadio(const Identity&) { return RADIO_TX_AUTO; }
  Random* getRNG() { return &rng; }
  Packet* createPathReturn(const Identity&, const uint8_t*, const uint8_t*, uint8_t,
                          uint8_t, const uint8_t*, size_t);
  Packet* createPathReturn(const uint8_t*, const uint8_t*, const uint8_t*, uint8_t,
                          uint8_t, const uint8_t*, size_t);
  Packet* createDatagram(uint8_t, const Identity&, const uint8_t*, const uint8_t*, size_t);
};
}
#include "history_types.inc"
struct ClientInfo {
  mesh::Identity id;
  uint8_t permissions = 3;
  uint32_t last_activity = 0;
  bool isAdmin() const { return (permissions & PERM_ACL_ROLE_MASK) == 3; }
};
struct Clock : mesh::RTCClock {
  uint32_t getCurrentTime() override { return 1000; }
  void setCurrentTime(uint32_t) override {}
};
// Sensor and telemetry collection boundaries are modeled; handler and LPP
// sizing/conversion, real history aggregation, and packet assembly are actual.
struct Telemetry {
  std::vector<uint8_t> data;
  size_t max_len = SENSOR_TELEMETRY_CAPACITY;
  Telemetry() { data.reserve(max_len); }
  void reset() { data.clear(); }
  uint8_t getSize() const { return data.size(); }
  uint8_t* getBuffer() { return data.data(); }
  void add(uint8_t channel, uint8_t type, size_t size) {
    if (data.size() + 2 + size > max_len) return;
    data.push_back(channel); data.push_back(type);
    for (size_t i = 0; i < size; ++i) data.push_back(0x22);
  }
  void addVoltage(uint8_t channel, float) { add(channel, LPP_VOLTAGE, 2); }
  void addTemperature(uint8_t channel, float) { add(channel, LPP_TEMPERATURE, 2); }
};
struct Sensors {
  std::vector<uint8_t> body;
  uint8_t last_permissions = 0;
  void requestGpsTelemetryTimeSync(uint32_t) {}
  void querySensors(uint8_t permissions, Telemetry& telemetry) {
    last_permissions = permissions;
    if (telemetry.data.size() + body.size() <= telemetry.max_len)
      telemetry.data.insert(telemetry.data.end(), body.begin(), body.end());
  }
};
struct Board {
  uint16_t getBattMilliVolts() { return 3500; }
  float getMCUTemperature() { return NAN; }
};
class SensorMesh : public mesh::Mesh {
public:
  uint8_t reply_data[MAX_PACKET_PAYLOAD] = {};
  struct { uint8_t telemetry_access = TELEMETRY_ACCESS_ALL; } _prefs;
  Telemetry telemetry;
  Sensors sensors;
  Board board;
  Clock clock;
  std::array<MinMaxAvg, 8> history;
  int history_count = 8;
  bool empty_real_series = false;
  Clock* getRTCClock() { return &clock; }
  SensorMesh() {
    for (size_t i = 0; i < history.size(); ++i)
      history[i] = {1.0f, 2.0f, 1.5f, LPP_GENERIC_SENSOR, uint8_t(i + 1)};
  }
  int querySeriesData(uint32_t start, uint32_t end, MinMaxAvg* destination, int max_num) {
    assert(max_num == 8);
    if (empty_real_series) {
      float values[4];
      TimeSeriesData series(values, 4, 60);
      series.calcMinMaxAvg(&clock, start, end, destination, TELEM_CHANNEL_SELF, LPP_VOLTAGE);
      return 1;
    }
    for (int i = 0; i < std::min(max_num, std::max(0, history_count)); ++i) destination[i] = history[i];
    return history_count;
  }
  uint8_t handleRequest(ClientInfo*, uint32_t, uint8_t, uint8_t*, size_t,
                       size_t = mesh::CLIENT_ACL_DIRECT_REPLY_CAPACITY);
};
class MyMesh : public SensorMesh {
public:
  MemoryFS room_fs;
  MemoryFS* _fs = &room_fs;
  mesh::RoomAccessPolicy room_access;
  MyMesh() {
    telemetry.max_len = ROOM_TELEMETRY_CAPACITY; telemetry.data.reserve(telemetry.max_len);
    metadata_filesystem = _fs;
    assert(room_access.load(_fs));
  }
  int handleRequest(ClientInfo*, uint32_t, uint8_t*, size_t,
                    size_t = mesh::CLIENT_ACL_DIRECT_REPLY_CAPACITY);
};
#include "production.inc"

static std::vector<uint8_t> entry(uint8_t type, unsigned size, uint8_t channel = 2) {
  std::vector<uint8_t> body{channel, type};
  body.insert(body.end(), size, 0x33);
  return body;
}
static void verify_packet(SensorMesh& target, ClientInfo& client, unsigned length,
                          bool flood, uint8_t encoded_path) {
  uint8_t secret[PUB_KEY_SIZE] = {}, path[MAX_PATH_SIZE] = {};
  auto* packet = flood ? target.createPathReturn(client.id, secret, path, encoded_path,
                             PAYLOAD_TYPE_RESPONSE, target.reply_data, length)
                      : target.createDatagram(PAYLOAD_TYPE_RESPONSE, client.id, secret,
                             target.reply_data, length);
  assert(packet && packet->payload_len <= MAX_PACKET_PAYLOAD);
  const unsigned path_bytes = flood ? (encoded_path & 63) * ((encoded_path >> 6) + 1) : 0;
  const unsigned decoded_size = packet->payload_len - 2 * PATH_HASH_SIZE - CIPHER_MAC_SIZE
      - (flood ? path_bytes + 2 : 0);
  assert(decoded_size + 2 <= MAX_FRAME_SIZE);
}
static void telemetry_checks() {
  ClientInfo client;
  uint8_t mask = 0;
  for (bool flood : {false, true}) {
    for (unsigned path = 0; path < 256; ++path) {
      if (!mesh::Packet::isValidPathLen(path)) continue;
      const size_t capacity = std::min(mesh::clientACLReplyCapacity(flood, path),
                                       mesh::CLIENT_ACL_DIRECT_REPLY_CAPACITY);
      SensorMesh target;
      for (unsigned i = 0; i < SENSOR_TELEMETRY_CAPACITY / 3; ++i) {
        auto item = entry(LPP_RELATIVE_HUMIDITY, 1, i + 2);
        target.sensors.body.insert(target.sensors.body.end(), item.begin(), item.end());
      }
      // Suppress self voltage to fill the actual capacity with whole humidity records.
      mask = TELEM_PERM_BASE;
      const unsigned length = target.handleRequest(&client, 51, REQ_TYPE_GET_TELEMETRY_DATA,
                                                     &mask, 1, capacity);
      assert(length <= capacity && length >= 4 && (length - 4) % 3 == 0);
      assert(length == 4 + std::min(size_t(SENSOR_TELEMETRY_CAPACITY / 3), (capacity - 4) / 3) * 3);
      assert(!memcmp(target.reply_data + 4, target.sensors.body.data(), length - 4));
      verify_packet(target, client, length, flood, path);
    }
  }
  for (uint8_t type : {uint8_t(LPP_GPS), uint8_t(LPP_ACCELEROMETER), uint8_t(LPP_GYROMETER), uint8_t(LPP_COLOUR)}) {
    SensorMesh target;
    target.sensors.body = entry(type, LPPData::getDataSize(type));
    mask = TELEM_PERM_BASE;
    const unsigned full = 4 + target.sensors.body.size();
    assert(target.handleRequest(&client, 51, REQ_TYPE_GET_TELEMETRY_DATA, &mask, 1, full) == full);
    assert(!memcmp(target.reply_data + 4, target.sensors.body.data(), full - 4));
    assert(target.handleRequest(&client, 51, REQ_TYPE_GET_TELEMETRY_DATA, &mask, 1, full - 1) == 4);
  }
  {
    SensorMesh target;
    mask = 0xFF;
    assert(target.handleRequest(&client, 51, REQ_TYPE_GET_TELEMETRY_DATA, &mask, 1) == 4);
    mask = TELEM_PERM_BASE;
  }
  for (unsigned capacity = 0; capacity < 4; ++capacity) {
    SensorMesh target;
    assert(target.handleRequest(&client, 51, REQ_TYPE_GET_TELEMETRY_DATA, &mask, 1, capacity) == 0);
  }
  for (auto invalid : {std::vector<uint8_t>{2}, std::vector<uint8_t>{2, LPP_GPS, 3},
                      entry(0xFF, 1), entry(LPP_POLYLINE, 8), entry(LPP_VOLTAGE, 2, 0)}) {
    SensorMesh target;
    target.sensors.body = invalid;
    assert(target.handleRequest(&client, 51, REQ_TYPE_GET_TELEMETRY_DATA, &mask, 1) == 4);
  }
}
static void room_telemetry_checks() {
  ClientInfo client;
  uint8_t query[] = {ROOM_REQ_TYPE_GET_TELEMETRY_DATA, 0};
  for (bool flood : {false, true}) {
    for (unsigned path = 0; path < 256; ++path) {
      if (!mesh::Packet::isValidPathLen(path)) continue;
      const size_t capacity = std::min(mesh::clientACLReplyCapacity(flood, path),
                                       mesh::CLIENT_ACL_DIRECT_REPLY_CAPACITY);
      MyMesh target;
      for (unsigned i = 0; i < ROOM_TELEMETRY_CAPACITY / 4 - 1; ++i) {
        auto item = entry(LPP_VOLTAGE, 2, i + 2);
        target.sensors.body.insert(target.sensors.body.end(), item.begin(), item.end());
      }
      const unsigned length = target.handleRequest(&client, 51, query, sizeof(query), capacity);
      assert(length == 4 + std::min(size_t(ROOM_TELEMETRY_CAPACITY / 4), (capacity - 4) / 4) * 4);
      assert(length <= capacity && (length - 4) % 4 == 0);
      assert(!memcmp(target.reply_data + 4, target.telemetry.getBuffer(), length - 4));
      verify_packet(target, client, length, flood, path);
    }
  }
  for (uint8_t type : {uint8_t(LPP_GPS), uint8_t(LPP_ACCELEROMETER), uint8_t(LPP_GYROMETER), uint8_t(LPP_COLOUR)}) {
    MyMesh target;
    target.sensors.body = entry(type, LPPData::getDataSize(type));
    const unsigned full = 8 + target.sensors.body.size();
    assert(target.handleRequest(&client, 51, query, sizeof(query), full) == int(full));
    assert(!memcmp(target.reply_data + 8, target.sensors.body.data(), full - 8));
    assert(target.handleRequest(&client, 51, query, sizeof(query), full - 1) == 8);
  }
  for (unsigned capacity = 0; capacity < 4; ++capacity) {
    MyMesh target;
    assert(target.handleRequest(&client, 51, query, sizeof(query), capacity) == 0);
  }
  for (auto invalid : {std::vector<uint8_t>{2}, std::vector<uint8_t>{2, LPP_GPS, 3},
                      entry(0xFF, 1), entry(LPP_POLYLINE, 8), entry(LPP_VOLTAGE, 2, 0)}) {
    MyMesh target;
    target.sensors.body = invalid;
    assert(target.handleRequest(&client, 51, query, sizeof(query)) == 8);
  }
  MyMesh target;
  assert(target.handleRequest(&client, 51, query, 1) == 0);
  assert(target.handleRequest(&client, 51, query, 0) == 0);
  assert(target.handleRequest(&client, 51, nullptr, 2) == 0);
  assert(target.handleRequest(nullptr, 51, query, sizeof(query)) == 0);
}

static void room_board_dispatch_checks() {
  ClientInfo client;
  uint8_t malformed[] = {mesh::ROOM_BOARD_REQUEST_SUBTYPE, 0};
  for (size_t capacity = 0; capacity <= MAX_PACKET_PAYLOAD; ++capacity) {
    MyMesh target;
    memset(target.reply_data, 0xee, sizeof(target.reply_data));
    const int length = target.handleRequest(&client, 51, malformed, sizeof(malformed), capacity);
    assert(length == (capacity < 7 ? 0 : 7));
    assert(size_t(length) <= capacity);
    for (size_t i = capacity; i < sizeof(target.reply_data); ++i) assert(target.reply_data[i] == 0xee);
    if (length) {
      uint32_t tag = 0; memcpy(&tag, target.reply_data, sizeof(tag));
      assert(tag == 51 && target.reply_data[4] == mesh::ROOM_BOARD_REQUEST_SUBTYPE);
      assert(target.reply_data[5] == 0 && target.reply_data[6] == 1);
    }
  }
  uint8_t index[] = {mesh::ROOM_BOARD_REQUEST_SUBTYPE, 0, 0, 0, 0, 0, 0};
  MyMesh target;
  assert(target.handleRequest(&client, 51, index, sizeof(index), 13) == 7);
  assert(target.reply_data[6] == 1);
  assert(target.handleRequest(&client, 51, index, sizeof(index), 14) == 14);
  assert(target.reply_data[6] == 0);
}

static void room_mail_dispatch_checks() {
  uint8_t settings[] = {mesh::ROOM_MAIL_REQUEST_SUBTYPE, 0, 0, 0, 0, 0, 0};
  for (uint8_t permissions : {uint8_t(0), uint8_t(1), uint8_t(2), uint8_t(3), uint8_t(4), uint8_t(131)}) {
    for (uint32_t activity : {uint32_t(0), uint32_t(1000)}) {
      const uint8_t role = permissions & PERM_ACL_ROLE_MASK;
      const bool allowed = role == PERM_ACL_READ_ONLY || role == PERM_ACL_READ_WRITE
          || role == PERM_ACL_ADMIN || (role == PERM_ACL_GUEST && activity != 0);
      for (size_t capacity = 0; capacity <= MAX_PACKET_PAYLOAD; ++capacity) {
        MyMesh target;
        ClientInfo client; client.id.pub_key[0] = 1;
        client.permissions = permissions; client.last_activity = activity;
        memset(target.reply_data, 0xee, sizeof(target.reply_data));
        const unsigned writes = target.room_fs.write_opens;
        const int length = target.handleRequest(&client, 51, settings, sizeof(settings), capacity);
        const int expected = !allowed || capacity < 7 ? 0 : capacity < 17 ? 7 : 17;
        assert(length == expected && size_t(length) <= capacity);
        for (size_t i = capacity; i < sizeof(target.reply_data); ++i) assert(target.reply_data[i] == 0xee);
        assert(target.room_fs.write_opens == writes); // Checking must not provision an inbox.
        if (length) {
          uint32_t tag = 0; memcpy(&tag, target.reply_data, 4);
          assert(tag == 51 && target.reply_data[4] == mesh::ROOM_MAIL_REQUEST_SUBTYPE);
          assert(target.reply_data[5] == 0 && target.reply_data[6] == (capacity < 17 ? 1 : 0));
        }
      }
    }
  }
  for (size_t capacity : {size_t(10), size_t(11)}) {
    MyMesh target;
    ClientInfo owner; owner.permissions = PERM_ACL_READ_ONLY; owner.id.pub_key[0] = 1;
    uint8_t policy[] = {mesh::ROOM_MAIL_REQUEST_SUBTYPE, 8, 0, 0, 0, 0, 1};
    const int length = target.handleRequest(&owner, 51, policy, sizeof(policy), capacity);
    assert(length == (capacity < 11 ? 7 : 11));
    mesh::RoomMailStatus status;
    const auto result = mesh::getRoomMailStatus(&target.room_fs, owner.id.pub_key, status);
    assert(result == (capacity < 11 ? mesh::RoomMailResult::NotFound : mesh::RoomMailResult::Success));
    if (capacity >= 11) assert(status.settings.mode == mesh::RoomMailMode::Public);
  }
  for (uint8_t role : {uint8_t(PERM_ACL_READ_ONLY), uint8_t(PERM_ACL_READ_WRITE), uint8_t(PERM_ACL_ADMIN)}) {
    MyMesh target;
    ClientInfo sender; sender.permissions = role; sender.id.pub_key[0] = 1;
    uint8_t recipient[32] = {}; recipient[0] = 2;
    mesh::RoomMailSettings public_box; public_box.mode = mesh::RoomMailMode::Public;
    assert(mesh::saveRoomMailSettings(&target.room_fs, recipient, public_box, 0) == mesh::RoomMailResult::Success);
    uint8_t send[37] = {mesh::ROOM_MAIL_REQUEST_SUBTYPE, 3};
    memcpy(send + 2, recipient, 32); send[34] = 1; send[36] = 'x';
    assert(target.handleRequest(&sender, 51, send, sizeof(send), 11) == 11);
    assert(target.reply_data[6] == (role == PERM_ACL_READ_ONLY ? 6 : 0));
    mesh::RoomMailStatus status;
    assert(mesh::getRoomMailStatus(&target.room_fs, recipient, status) == mesh::RoomMailResult::Success);
    assert(status.count == (role == PERM_ACL_READ_ONLY ? 0 : 1));
    assert(target.room_access.addBan(&target.room_fs, recipient) == mesh::RoomAccessPolicy::BanResult::Saved);
    for (size_t capacity = 0; capacity <= 11; ++capacity) {
      assert(target.handleRequest(&sender, 52, send, sizeof(send), capacity) == (capacity < 7 ? 0 : 7));
      if (capacity >= 7) assert(target.reply_data[6] == 6);
    }
  }
}

static void history_checks() {
  ClientInfo client;
  uint8_t query[10] = {};
  for (bool flood : {false, true}) {
    for (unsigned path = 0; path < 256; ++path) {
      if (!mesh::Packet::isValidPathLen(path)) continue;
      const size_t capacity = std::min(mesh::clientACLReplyCapacity(flood, path),
                                       mesh::CLIENT_ACL_DIRECT_REPLY_CAPACITY);
      SensorMesh target;
      const unsigned length = target.handleRequest(&client, 51, REQ_TYPE_GET_AVG_MIN_MAX,
                                                     query, sizeof(query), capacity);
      assert(length == 8 + std::min(size_t(8), (capacity - 8) / 14) * 14);
      uint32_t tag, now;
      memcpy(&tag, target.reply_data, 4); memcpy(&now, target.reply_data + 4, 4);
      assert(tag == 51 && now == 1000);
      for (unsigned i = 8, channel = 1; i < length; i += 14, ++channel) {
        assert(target.reply_data[i] == channel && target.reply_data[i + 1] == LPP_GENERIC_SENSOR);
        assert(LPPData::getFloat(target.reply_data + i + 2, 4, 1, false) == 1);
        assert(LPPData::getFloat(target.reply_data + i + 6, 4, 1, false) == 2);
        assert(LPPData::getFloat(target.reply_data + i + 10, 4, 1, false) == 1);
      }
      verify_packet(target, client, length, flood, path);
    }
  }
  for (unsigned type = 0; type < 256; ++type) {
    if (!LPPData::isScalarType(type)) continue;
    SensorMesh target;
    target.history_count = 1;
    target.history[0]._lpp_type = type;
    const unsigned length = target.handleRequest(&client, 51, REQ_TYPE_GET_AVG_MIN_MAX, query, sizeof(query));
    assert(length == 8u + 2u + 3u * LPPData::getDataSize(type));
    assert(target.reply_data[8] == 1 && target.reply_data[9] == type);
    assert(LPPData::getFloat(target.reply_data + 10, LPPData::getDataSize(type),
                              LPPData::getMultiplier(type), LPPData::isSigned(type)) == 1.0f);
    verify_packet(target, client, length, false, 0);
  }
  for (uint8_t type : {uint8_t(LPP_GPS), uint8_t(LPP_ACCELEROMETER), uint8_t(LPP_GYROMETER),
                       uint8_t(LPP_COLOUR), uint8_t(LPP_POLYLINE), uint8_t(0xFF)}) {
    SensorMesh target;
    target.history_count = 8;
    for (auto& record : target.history) record._lpp_type = type;
    assert(target.handleRequest(&client, 51, REQ_TYPE_GET_AVG_MIN_MAX, query, sizeof(query)) == 8);
  }
  for (int count : {-1, 9, 100}) {
    SensorMesh target;
    target.history_count = count;
    assert(target.handleRequest(&client, 51, REQ_TYPE_GET_AVG_MIN_MAX, query, sizeof(query)) == 0);
  }
  for (unsigned capacity = 0; capacity < 8; ++capacity) {
    SensorMesh target;
    assert(target.handleRequest(&client, 51, REQ_TYPE_GET_AVG_MIN_MAX, query, sizeof(query), capacity) == 0);
  }
  {
    SensorMesh target;
    target.empty_real_series = true;
    // The actual history aggregator returns NaN for an empty query range.
    assert(target.handleRequest(&client, 51, REQ_TYPE_GET_AVG_MIN_MAX, query, sizeof(query)) == 8);
  }
  for (float value : {NAN, INFINITY, -INFINITY}) {
    SensorMesh target;
    target.history_count = 1;
    target.history[0]._avg = value;
    assert(target.handleRequest(&client, 51, REQ_TYPE_GET_AVG_MIN_MAX, query, sizeof(query)) == 8);
  }
}
int main(int argc, char** argv) {
  assert(argc == 2);
  if (!strcmp(argv[1], "room")) room_telemetry_checks();
  else if (!strcmp(argv[1], "room.board")) room_board_dispatch_checks();
  else if (!strcmp(argv[1], "room.mail")) room_mail_dispatch_checks();
  else if (!strcmp(argv[1], "telemetry")) telemetry_checks();
  else if (!strcmp(argv[1], "history")) history_checks();
  else if (!strcmp(argv[1], "empty")) {
    SensorMesh target;
    ClientInfo client;
    uint8_t query[10] = {};
    target.empty_real_series = true;
    assert(target.handleRequest(&client, 51, REQ_TYPE_GET_AVG_MIN_MAX, query, sizeof(query)) == 8);
  }
  else if (!strcmp(argv[1], "permissions")) {
    for (uint8_t mode : {uint8_t(TELEMETRY_ACCESS_ALL), uint8_t(TELEMETRY_ACCESS_ACL)}) {
      for (uint8_t role : {uint8_t(PERM_ACL_GUEST), uint8_t(PERM_ACL_READ_ONLY), uint8_t(3)}) {
        SensorMesh target;
        ClientInfo client;
        client.permissions = role;
        target._prefs.telemetry_access = mode;
        const bool allowed = mode == TELEMETRY_ACCESS_ALL || role >= PERM_ACL_READ_ONLY;
        uint8_t mask = 0;
        const unsigned length = target.handleRequest(&client, 51, REQ_TYPE_GET_TELEMETRY_DATA, &mask, 1);
        assert(length == (allowed ? 8u : 4u));
        assert(target.sensors.last_permissions == (allowed ? 7 : 0));
        uint8_t query[10] = {};
        const unsigned history_length = target.handleRequest(&client, 51, REQ_TYPE_GET_AVG_MIN_MAX, query, sizeof(query));
        assert((history_length != 0) == (role >= PERM_ACL_READ_ONLY));
      }
    }
  }
  else if (!strcmp(argv[1], "packet")) {
    SensorMesh target;
    ClientInfo client;
    for (unsigned i = 0; i < SENSOR_TELEMETRY_CAPACITY / 4; ++i) {
      auto item = entry(LPP_VOLTAGE, 2, i + 2);
      target.sensors.body.insert(target.sensors.body.end(), item.begin(), item.end());
    }
    uint8_t mask = TELEM_PERM_BASE;
    const unsigned length = target.handleRequest(&client, 51, REQ_TYPE_GET_TELEMETRY_DATA, &mask, 1);
    verify_packet(target, client, length, false, 0);
  }
  else assert(false);
  std::cout << "Sensor response bounds and packet admission passed\n";
}
