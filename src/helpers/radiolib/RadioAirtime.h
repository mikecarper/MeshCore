#pragma once

#include <stdint.h>

namespace mesh {

// RadioLib time-on-air APIs use an unsigned return type even though some
// implementations return negative RADIOLIB_ERR_* values on failure. Those
// errors therefore appear immediately below UINT32_MAX.
static constexpr uint32_t RADIOLIB_AIRTIME_ERROR_WINDOW_US = 4096UL;

inline bool isEncodedRadioLibAirtimeError(uint32_t airtime_us) {
  return airtime_us
      >= UINT32_MAX - (RADIOLIB_AIRTIME_ERROR_WINDOW_US - 1UL);
}

}  // namespace mesh
