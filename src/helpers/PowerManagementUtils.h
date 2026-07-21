#pragma once

#include <stdint.h>

namespace mesh {
namespace power {

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
