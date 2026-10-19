#pragma once

#include <stdint.h>

// HIL-only ESP32-S3 SPI2/3 clock register experiment. Keep it independent of
// the peripheral so every N/pre combination can be checked on the host.
struct TDeckSpiClockTiming {
  static constexpr uint32_t ClockEqualsSource = uint32_t(1) << 31;
  static constexpr uint32_t PhaseMask = 0x0fff;

  static uint32_t corrected(uint32_t original) {
    const uint32_t n = (original >> 12) & 0x3f;
    // Source-clock mode has no divided phases. N=0 likewise cannot represent
    // both a high and a low phase; leave it untouched rather than underflow H.
    if ((original & ClockEqualsSource) || n == 0) return original;
    const uint32_t high = (n + 1) / 2 - 1;
    return (original & ~PhaseMask) | n | (high << 6);
  }
};
