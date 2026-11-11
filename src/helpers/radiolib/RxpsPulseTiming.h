#pragma once

#include <stdint.h>
#include <string.h>

namespace mesh {
namespace rxpspulse {

// All supported MCU float formats are IEEE binary32. Checking its exponent
// avoids several soft-float comparison calls on integrated Cortex-M4 radios.
inline bool finiteFloat(float value) {
  static_assert(sizeof(float)==sizeof(uint32_t),"32-bit radio float required");
  uint32_t bits;memcpy(&bits,&value,sizeof(bits));
  return (bits&0x7F800000UL)!=0x7F800000UL;
}

// Include the device's transition allowance, one nonzero timer tick, and at
// least 500 us more in encoded timer units. Rates and field sizes are supplied
// by the caller; this helper makes no assumption about the radio family.
// Zero means the requested period cannot be represented safely.
inline uint32_t bufferedSleepRequestUs(uint32_t transition_us,
                                       uint32_t timer_hz,
                                       uint32_t max_ticks) {
  if (timer_hz == 0 || max_ticks == 0) return 0;

  const uint64_t margin_product = uint64_t(500) * timer_hz;
  const uint64_t required_ticks = 1 + margin_product / 1000000 +
      (margin_product % 1000000 != 0);
  if (required_ticks > max_ticks) return 0;

  const uint64_t dwell_numerator = required_ticks * 1000000;
  const uint64_t dwell_us = dwell_numerator / timer_hz +
      (dwell_numerator % timer_hz != 0);
  const uint64_t request_us = uint64_t(transition_us) + dwell_us;
  if (request_us > UINT32_MAX) return 0;

  // Whole microseconds may add multiple ticks at high timer rates. Check the
  // actual floor conversion, including the field limit, before returning it.
  const uint64_t encoded_ticks = (dwell_us * timer_hz) / 1000000;
  if (encoded_ticks < required_ticks || encoded_ticks > max_ticks) return 0;
  return static_cast<uint32_t>(request_us);
}

}  // namespace rxpspulse
}  // namespace mesh
