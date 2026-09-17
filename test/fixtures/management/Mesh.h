#pragma once
#include <stdint.h>
#include <string.h>
#include <vector>
#include <cmath>
inline uint32_t test_millis = 0;
inline uint32_t millis() { return test_millis; }
#define MAX_PATH_SIZE 64
#define OUT_PATH_UNKNOWN 0xff
struct TransportKey {
  uint8_t key[16] = {1};
  bool isNull() const { for (auto b : key) if (b) return false; return true; }
};
namespace mesh {
struct LocalIdentity { uint8_t pub_key[32] = {}; };
struct Packet {
  uint8_t payload[184] = {}, payload_len = 0;
  bool flood = false;
  static bool isValidPathLen(uint8_t n) { return (n >> 6) < 3 && ((n & 63) * ((n >> 6) + 1)) <= 64; }
  static uint8_t copyPath(uint8_t* out, const uint8_t* in, uint8_t n) {
    const size_t bytes = (n & 63) * ((n >> 6) + 1);
    if (bytes) memcpy(out, in, bytes);
    return n;
  }
};
struct RTCClock { uint32_t time = 1789513200; uint32_t getCurrentTime() { return time; } };
struct MainBoard {
  uint16_t voltage = 3740;
  float temperature = 24;
  uint16_t getBattMilliVolts() { return voltage; }
  float getMCUTemperature() { return temperature; }
};
struct Mesh {
  LocalIdentity self_id;
  RTCClock rtc;
  bool temp = false, outbound = false, fail_queue = false;
  unsigned budget = 60000;
  std::vector<Packet> packets;
  RTCClock* getRTCClock() { return &rtc; }
  bool isAnyTempRadioActive() { return temp; }
  bool hasOutbound() { return outbound; }
  unsigned getRemainingTxBudget() { return budget; }
  Packet* createRawData(const uint8_t* data, size_t n) { auto* p = new Packet; memcpy(p->payload, data, n); p->payload_len = n; return p; }
  bool sendManagementData(Packet* p, bool flood, const uint8_t*, uint8_t,
                          uint8_t, const uint8_t* = nullptr) {
    p->flood = flood; if (!fail_queue) packets.push_back(*p); delete p; return !fail_queue;
  }
};
}
