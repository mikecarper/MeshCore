#pragma once

#include <stdint.h>

namespace mesh {
namespace gps {

static const uint32_t DEFAULT_UPDATE_INTERVAL_SEC = 1;
static const uint32_t MAX_UPDATE_INTERVAL_SEC = 24UL * 60UL * 60UL;

inline bool parseUpdateInterval(const char* value, uint32_t& configured_sec) {
  if (value == nullptr || *value == 0) return false;

  uint32_t parsed = 0;
  for (const char* p = value; *p; ++p) {
    if (*p < '0' || *p > '9') return false;
    uint32_t digit = (uint32_t)(*p - '0');
    if (parsed > (MAX_UPDATE_INTERVAL_SEC - digit) / 10UL) return false;
    parsed = parsed * 10UL + digit;
  }

  configured_sec = parsed;
  return true;
}

inline uint32_t effectiveUpdateIntervalSec(uint32_t configured_sec) {
  return configured_sec == 0 ? DEFAULT_UPDATE_INTERVAL_SEC : configured_sec;
}

inline uint32_t updateIntervalMillis(uint32_t configured_sec) {
  return effectiveUpdateIntervalSec(configured_sec) * 1000UL;
}

} // namespace gps
} // namespace mesh
