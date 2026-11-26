#pragma once

#include <stdint.h>
#include <string.h>

namespace mesh {
namespace stm32 {

// The RX delay base is finite and positive here. Reduce its binary mantissa
// to [sqrt(1/2), sqrt(2)] so a short log series stays within float precision.
// Keep even subnormal bases supported by normalizing them before extracting
// the exponent. This lets the compact profile link only the exponential.
inline float rxDelayLog(float base) {
  int exponent = 0;
  if (base < 0x1p-126f) {
    base *= 0x1p24f;
    exponent = -24;
  }
  uint32_t bits;
  memcpy(&bits, &base, sizeof(bits));
  exponent += static_cast<int>(bits >> 23) - 127;
  bits = (bits & 0x007fffff) | 0x3f800000;
  float mantissa;
  memcpy(&mantissa, &bits, sizeof(mantissa));
  if (mantissa > 1.414213562f) {
    mantissa *= 0.5f;
    ++exponent;
  }
  const float ratio = (mantissa - 1.0f) / (mantissa + 1.0f);
  const float square = ratio * ratio;
  const float logarithm = 2.0f * ratio *
      (1.0f + square * (1.0f / 3 + square * (1.0f / 5 + square * (1.0f / 7 + square * (1.0f / 9)))));
  return static_cast<float>(exponent) * 0.693147181f + logarithm;
}

}  // namespace stm32
}  // namespace mesh
