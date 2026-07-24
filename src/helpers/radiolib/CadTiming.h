#pragma once

#include <stdint.h>

namespace mesh {

static constexpr uint32_t CAD_SCAN_MIN_TIMEOUT_MS = 100UL;
static constexpr uint32_t CAD_SCAN_MAX_TIMEOUT_MS = 3500UL;
static constexpr uint32_t CAD_SCAN_FALLBACK_TIMEOUT_MS = 500UL;

// RadioLib's SX126x CAD default examines four LoRa symbols. Allow six symbols
// plus fixed command/TCXO overhead, then clamp the result so normal profiles
// fail quickly without rejecting deliberately slow custom radio settings.
inline uint32_t calculateCadScanTimeoutMillis(uint8_t sf, float bandwidth_khz) {
  if (sf < 5 || sf > 12 || bandwidth_khz <= 0.0f) {
    return CAD_SCAN_FALLBACK_TIMEOUT_MS;
  }

  const float symbol_ms = static_cast<float>(1UL << sf) / bandwidth_khz;
  const float padded_ms = symbol_ms * 6.0f + 20.0f;
  uint32_t timeout_ms = static_cast<uint32_t>(padded_ms + 0.999f);

  if (timeout_ms < CAD_SCAN_MIN_TIMEOUT_MS) return CAD_SCAN_MIN_TIMEOUT_MS;
  if (timeout_ms > CAD_SCAN_MAX_TIMEOUT_MS) return CAD_SCAN_MAX_TIMEOUT_MS;
  return timeout_ms;
}

} // namespace mesh
