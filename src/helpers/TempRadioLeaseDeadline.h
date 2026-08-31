#pragma once

#include <stdint.h>

namespace mesh {

// Scheduled TempRadio uses wall-clock epochs so independently managed nodes
// can enter one common window.  The wall clock may legitimately be corrected
// backward by mesh/GPS/NTP consensus, however, and that must never extend a
// temporary modulation lease.  Anchor the accepted end to 64-bit uptime too;
// wall time decides when to start, while either clock is allowed to end it.
class TempRadioLeaseDeadline {
public:
  static uint64_t fromEpochEnd(uint64_t now_uptime_millis,
                               uint32_t now_epoch,
                               uint32_t end_epoch) {
    if (end_epoch <= now_epoch) return now_uptime_millis;
    return now_uptime_millis
        + (uint64_t)(end_epoch - now_epoch) * 1000ULL;
  }

  static bool expired(uint64_t now_uptime_millis,
                      uint64_t hard_end_uptime_millis) {
    return hard_end_uptime_millis != 0
        && now_uptime_millis >= hard_end_uptime_millis;
  }

  static uint32_t secondsUntil(uint64_t now_uptime_millis,
                               uint64_t hard_end_uptime_millis) {
    if (hard_end_uptime_millis == 0) return UINT32_MAX;
    if (now_uptime_millis >= hard_end_uptime_millis) return 0;
    uint64_t remaining_millis = hard_end_uptime_millis - now_uptime_millis;
    uint64_t seconds = (remaining_millis + 999ULL) / 1000ULL;
    return seconds > UINT32_MAX ? UINT32_MAX : (uint32_t)seconds;
  }
};

}  // namespace mesh
