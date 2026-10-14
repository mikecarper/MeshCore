#pragma once
#include <helpers/BeaconConfig.h>
#include <Arduino.h>
#include <cstddef>
#include <cstdint>
#include <cstring>
#include <vector>

extern uint64_t beacon_test_time;

namespace mesh {
class RTCClock {};
class RNG {
 public:
  void random(uint8_t* output, size_t size) {
    const uint32_t value = 0x12345678;
    std::memcpy(output, &value, size);
  }
};
class Mesh {
 public:
  struct Send { uint64_t time; BeaconSlot slot; uint32_t from, id; };
  std::vector<Send> sends;
  bool accept = true, wake_enabled = false, wire_supported = true, pending = false;
  uint32_t wake_delay = 0, wake_now = 0, attempts = 0, cancellations = 0;
  bool validateTextBeacon(const BeaconSlot& slot) const {
    const auto& p = slot.radio;
    return slot.enabled && RadioProfiles::valid(p) && p.freq >= 300 && p.freq <= 1000
        && (p.bw == 31.25f || p.bw == 62.5f || p.bw == 125 || p.bw == 250 || p.bw == 500)
        && slot.power >= -9 && slot.power <= 22 && slot.text[0]
        && (p.protocol != RadioProfileProtocol::Meshtext || wire_supported);
  }
  bool queueTextBeacon(const BeaconSlot& slot, const char*, uint32_t from, uint32_t id) {
    ++attempts;
    if (!accept) return false;
    pending = true;
    sends.push_back({beacon_test_time, slot, from, id});
    return true;
  }
  void setBeaconWakeDelay(uint32_t now, uint32_t delay, bool enabled) {
    wake_now = now; wake_delay = delay; wake_enabled = enabled;
  }
  uint32_t beaconNodeId() const { return 0x88776655; }
  RNG* getRNG() { return &rng_; }
  void cancelBeaconRadioFrame() { ++cancellations; pending = false; }
 private:
  RNG rng_;
};
} // namespace mesh
