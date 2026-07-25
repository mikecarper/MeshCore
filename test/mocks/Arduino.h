#pragma once

#include <cstdint>
#include <cmath>
#include <cstdio>
#include "Stream.h"

inline uint32_t g_mock_millis = 0;

using std::isnan;

inline uint32_t millis() {
  return g_mock_millis;
}

inline void delay(uint32_t ms) {
  g_mock_millis += ms;
}

inline char* ltoa(long value, char* dest, int base) {
  if (base == 10) {
    std::snprintf(dest, 16, "%ld", value);
  } else {
    dest[0] = '\0';
  }
  return dest;
}
