#pragma once
#include <cstddef>
#include <cstdint>
#include <vector>

#define MAX_TRANS_UNIT 255
#define PAYLOAD_TYPE_OTA 0x0c
#define BRIDGE_DEBUG_PRINTLN(...) ((void)0)
struct NodePrefs { uint32_t bridge_baud = 115200; };

namespace mesh {
struct Packet {
  std::vector<uint8_t> payload{1, 2, 3};
  bool allowed = true;
  uint8_t getPayloadType() const { return payload.empty() ? 0 : (payload[0] >> 2) & 0x0f; }
  int getRawLength() const { return static_cast<int>(payload.size()); }
  uint16_t writeTo(uint8_t* out) const {
    for (size_t i = 0; i < payload.size(); ++i) out[i] = payload[i];
    return static_cast<uint16_t>(payload.size());
  }
  bool readFrom(const uint8_t* bytes, size_t size) {
    if (!size || size > MAX_TRANS_UNIT + 1) return false;
    payload.assign(bytes, bytes + size); return true;
  }
};
struct RTCClock {};
struct PacketManager {
  bool starved = false;
  unsigned allocations = 0, frees = 0;
  std::vector<std::vector<uint8_t>> received;
  Packet* allocNew() {
    ++allocations;
    return starved ? nullptr : new Packet;
  }
  void free(Packet* packet) { ++frees; delete packet; }
};
}

struct SeenPackets {
  std::vector<std::vector<uint8_t>> seen;
  bool wasSeen(const mesh::Packet* packet) const {
    for (const auto& bytes : seen) if (bytes == packet->payload) return true;
    return false;
  }
  void markSeen(mesh::Packet* packet) { seen.push_back(packet->payload); }
};

class BridgeBase {
public:
  static constexpr uint16_t BRIDGE_PACKET_MAGIC = 0xC03E;
  static constexpr uint16_t BRIDGE_MAGIC_SIZE = 2, BRIDGE_LENGTH_SIZE = 2, BRIDGE_CHECKSUM_SIZE = 2;
  virtual ~BridgeBase() = default;
  virtual void begin() = 0;
  virtual void end() = 0;
  virtual void loop() = 0;
  virtual void sendPacket(mesh::Packet*) = 0;
  virtual void onPacketReceived(mesh::Packet*) = 0;
  bool isRunning() const { return _initialized; }
protected:
  bool _initialized = false;
  NodePrefs* _prefs;
  mesh::PacketManager* _mgr;
  mesh::RTCClock* _rtc;
  SeenPackets _seen_packets;
  BridgeBase(NodePrefs* prefs, mesh::PacketManager* mgr, mesh::RTCClock* rtc)
    : _prefs(prefs), _mgr(mgr), _rtc(rtc) {}
  bool allowsPacket(const mesh::Packet* packet) { return packet->allowed; }
  static uint16_t fletcher16(const uint8_t* bytes, size_t size) {
    uint16_t a = 0, b = 0;
    for (size_t i = 0; i < size; ++i) { a = (a + bytes[i]) % 255; b = (b + a) % 255; }
    return (b << 8) | a;
  }
  bool validateChecksum(const uint8_t* bytes, size_t size, uint16_t checksum) {
    return fletcher16(bytes, size) == checksum;
  }
  void handleReceivedPacket(mesh::Packet* packet) {
    _mgr->received.push_back(packet->payload); _mgr->free(packet);
  }
};
