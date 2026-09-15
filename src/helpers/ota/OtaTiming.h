#pragma once

#include <stdint.h>
#include <math.h>

namespace mesh {
namespace ota {

constexpr float OTA_SPEED_DEFAULT = 1.0f;
constexpr float OTA_SPEED_MIN = 0.05f;
constexpr float OTA_SPEED_MAX = 3.0f;

inline bool validSpeed(float speed) {
  return isfinite(speed) && speed >= OTA_SPEED_MIN && speed <= OTA_SPEED_MAX;
}

// Relative deadlines must fit the signed half of millis(), including after
// rollover. Keep zero immediate and preserve every existing delay at 1x.
inline uint32_t scaleDelay(uint32_t delay_ms, float speed) {
  if (!validSpeed(speed)) speed = OTA_SPEED_DEFAULT;
  const double scaled = ceil((double)delay_ms / speed);
  return scaled >= INT32_MAX ? INT32_MAX : (uint32_t)scaled;
}

// For optional deadlines whose owners use zero to mean "not armed". A timer
// that lands exactly on millis rollover must still fire (one millisecond late).
inline uint32_t armedDeadline(uint32_t now, uint32_t delay_ms) {
  const uint32_t deadline = now + delay_ms;
  return deadline ? deadline : 1;
}

// Extra quiet time between OTA packets when slowing an otherwise immediately
// drainable queue. Actual RF airtime and the dispatcher's airtime budget remain
// unchanged. At 1x and above, the existing queue can drain at its normal rate.
inline uint32_t packetQuietTime(uint32_t airtime_ms, float speed) {
  if (!validSpeed(speed) || speed >= OTA_SPEED_DEFAULT) return 0;
  const uint32_t cycle = scaleDelay(airtime_ms, speed);
  return cycle > airtime_ms ? cycle - airtime_ms : 0;
}

inline uint32_t relayDelay(uint32_t delay_ms, float speed) {
  // DispatcherAction packs the priority above its 24-bit delay. An extreme
  // slow setting must not spill into those priority bits.
  const uint32_t scaled = scaleDelay(delay_ms, speed);
  return scaled > 0x00ffffffU ? 0x00ffffffU : scaled;
}

// Advert intervals can reach seven days; at 0.05x that is 140 days. Count
// down in real elapsed milliseconds instead of overflowing a millis deadline.
class LongTimer {
  uint64_t remaining_ = 0;
  uint32_t last_ = 0;
public:
  bool ready(uint32_t now) {
    const uint32_t elapsed = now - last_;
    last_ = now;
    remaining_ = remaining_ > elapsed ? remaining_ - elapsed : 0;
    return remaining_ == 0;
  }
  void arm(uint32_t now, uint32_t delay_ms, float speed) {
    last_ = now;
    remaining_ = (uint64_t)ceil((double)delay_ms / (validSpeed(speed) ? speed : 1.0f));
  }
  void rescale(uint32_t now, float old_speed, float new_speed) {
    ready(now);
    remaining_ = (uint64_t)ceil((double)remaining_ * old_speed / new_speed);
  }
};

} // namespace ota
} // namespace mesh
