#pragma once
#include <stdint.h>

namespace mesh { namespace w12rxps {
struct Timing { uint32_t rx_ticks; uint32_t cycle_ticks; };

// LR2021 uses 32.768 kHz ticks and a start-to-start cycle period. Calculate
// in 64 bits: the pinned RadioLib conversion overflows above ~131 ms.
inline bool encode(uint32_t rx_us, uint32_t sleep_us, uint32_t transition_us,
                   Timing& out) {
  out = {};
  if (sleep_us <= transition_us) return false;
  const uint64_t rx = uint64_t(rx_us) * 32768u / 1000000u;
  const uint64_t sleep = uint64_t(sleep_us - transition_us) * 32768u / 1000000u;
  if (!rx || !sleep || rx + sleep > 0xFFFFFFu) return false;
  out.rx_ticks = uint32_t(rx);
  out.cycle_ticks = uint32_t(rx + sleep);
  return true;
}
} }
