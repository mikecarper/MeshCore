#pragma once
#include <cstddef>
#include <cstdint>
#include <vector>
#include <helpers/BridgeIngressProfile.h>
#if defined(ARDUINO)
#include <Stream.h>
#include <cstdarg>
#include <cstdio>
#endif

#define MAX_TRANS_UNIT 255
#define PAYLOAD_TYPE_OTA 0x0c
#define BRIDGE_DEBUG_PRINTLN(...) ((void)0)
struct NodePrefs { uint8_t bridge_pkt_src = 1; uint32_t bridge_delay = 0; char bridge_secret[16] = "unit-test-key"; };

namespace mesh {
#if defined(ARDUINO)
extern bool logging_enabled, debug_enabled;
class DebugStream : public Stream {
public:
  int capacity = 4096;
  unsigned writes = 0;
  std::vector<uint8_t> bytes;
  int available() override { return 0; }
  int read() override { return -1; }
  int availableForWrite() override { return capacity; }
  size_t write(const uint8_t* data, size_t size) override {
    ++writes;
    if (size > static_cast<size_t>(capacity)) return 0;
    bytes.insert(bytes.end(), data, data + size); return size;
  }
};
extern DebugStream debug_stream;
inline bool isUsbDebugLoggingEnabled() { return logging_enabled && debug_enabled; }
inline Stream& usbLoggingPort() { return debug_stream; }
inline size_t nrf52DebugPrintf(const char* format, ...) {
  if (!isUsbDebugLoggingEnabled()) return 0;
  char record[256];
  va_list args; va_start(args, format);
  int size = vsnprintf(record, sizeof(record), format, args); va_end(args);
  if (size <= 0) return 0;
  if (size >= static_cast<int>(sizeof(record))) size = sizeof(record) - 1;
  return debug_stream.write(reinterpret_cast<const uint8_t*>(record), size);
}
#endif
struct Packet {
  std::vector<uint8_t> payload{1, 2, 3};
  bool allowed = true; bool udp_bridge_ingress = false;
  uint8_t getPayloadType() const { return payload.empty() ? 0 : (payload[0] >> 2) & 0x0f; }
  int getRawLength() const { return static_cast<int>(payload.size()); }
  uint16_t writeTo(uint8_t* out) const {
    for (size_t i = 0; i < payload.size(); ++i) out[i] = payload[i];
    return static_cast<uint16_t>(payload.size());
  }
  bool readFrom(const uint8_t* bytes, size_t size) {
    if (size < 2 || size > MAX_TRANS_UNIT || bytes[0] == 0xff) return false;
    udp_bridge_ingress = false;
    payload.assign(bytes, bytes + size); return true;
  }
};
struct RTCClock {};
struct PacketManager {
  bool starved = false;
  unsigned allocations = 0, frees = 0;
  std::vector<std::vector<uint8_t>> received;
  std::vector<mesh::bridge::IngressSource> received_sources;
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
  size_t seenCount() const { return _seen_packets.seen.size(); }
protected:
  const mesh::bridge::IngressSource* ingress_source = nullptr;
  struct IngressSourceScope {
    BridgeBase& owner;
    const mesh::bridge::IngressSource* previous;
    IngressSourceScope(BridgeBase& bridge, const mesh::bridge::IngressSource& source)
      : owner(bridge), previous(bridge.ingress_source) { owner.ingress_source = &source; }
    ~IngressSourceScope() { owner.ingress_source = previous; }
  };
  enum class IngressResult : uint8_t { Stopped, Filtered, Duplicate, Queued };
  virtual void onIngressResult(IngressResult) {}
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
    if (!allowsPacket(packet)) {
      onIngressResult(IngressResult::Filtered); _mgr->free(packet); return;
    }
    if (packet->getPayloadType() != PAYLOAD_TYPE_OTA && _seen_packets.wasSeen(packet)) {
      onIngressResult(IngressResult::Duplicate); _mgr->free(packet); return;
    }
    if (packet->getPayloadType() != PAYLOAD_TYPE_OTA) _seen_packets.markSeen(packet);
    onIngressResult(IngressResult::Queued);
    _mgr->received_sources.push_back(ingress_source ? *ingress_source : mesh::bridge::IngressSource());
    _mgr->received.push_back(packet->payload); _mgr->free(packet);
  }
};
