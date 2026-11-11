#pragma once
#include <stdint.h>
namespace mesh {
// Automatic legacy half of primary refresh at >=240 seconds. Never a timer.
constexpr uint32_t AGC_OLD_AUTO = UINT32_MAX;
inline bool validOldAGCInterval(uint32_t value) {
  return value == AGC_OLD_AUTO || value <= 86400;
}
inline uint32_t oldAGCIntervalMillis(uint32_t value) {
  return value <= 86400 ? value * 1000UL : 0;
}
} // namespace mesh
