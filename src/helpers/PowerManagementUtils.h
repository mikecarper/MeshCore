#pragma once

#include <stdint.h>

namespace mesh {
namespace power {

inline uint32_t powerSaveSleepSeconds(uint32_t delay_ms) {
  const uint32_t seconds = delay_ms / 1000UL;
#if defined(NRF52_PLATFORM) || defined(RP2040_PLATFORM) || defined(STM32_PLATFORM)
  // These boards ignore seconds and idle until an interrupt. Keep sub-second
  // idle eligible; periodic clock interrupts service the remaining deadline.
  return seconds + (delay_ms % 1000UL != 0);
#else
  // Timed sleepers must wake before a deadline, not round past its last fraction.
  return seconds;
#endif
}

inline uint32_t limitSleepMilliseconds(uint32_t max_seconds, uint32_t deadline_ms) {
  const uint64_t maximum_ms = static_cast<uint64_t>(max_seconds) * 1000ULL;
  return deadline_ms < maximum_ms ? deadline_ms : static_cast<uint32_t>(maximum_ms);
}

inline uint16_t medianVoltage(uint16_t a, uint16_t b, uint16_t c) {
  if (a > b) { uint16_t t = a; a = b; b = t; }
  if (b > c) { uint16_t t = b; b = c; c = t; }
  if (a > b) { uint16_t t = a; a = b; b = t; }
  return b;
}

inline bool shouldBootLock(uint16_t voltage_mv, uint16_t threshold_mv,
                           bool externally_powered) {
  return !externally_powered && threshold_mv != 0
      && voltage_mv > 1000 && voltage_mv < threshold_mv;
}

} // namespace power
} // namespace mesh
