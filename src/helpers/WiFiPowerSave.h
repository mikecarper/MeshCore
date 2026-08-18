#pragma once

#include <stdint.h>

// Stored WiFi power-save values are 0=min, 1=none, and 2=max. Build profiles
// may override the fresh-install default without changing the persisted wire /
// file representation. Existing saved settings always take precedence.
#ifndef DEFAULT_WIFI_POWER_SAVE_MODE
#define DEFAULT_WIFI_POWER_SAVE_MODE 1
#endif

#if DEFAULT_WIFI_POWER_SAVE_MODE < 0 || DEFAULT_WIFI_POWER_SAVE_MODE > 2
#error "DEFAULT_WIFI_POWER_SAVE_MODE must be 0 (min), 1 (none), or 2 (max)"
#endif

namespace mesh {
namespace wifi {

static constexpr uint8_t kPowerSaveMin = 0;
static constexpr uint8_t kPowerSaveNone = 1;
static constexpr uint8_t kPowerSaveMax = 2;
static constexpr uint8_t kDefaultPowerSave = DEFAULT_WIFI_POWER_SAVE_MODE;

// ESP32 WiFi and Bluetooth coexistence requires modem sleep, so "none" must
// fall back to the minimum sleep policy whenever Bluetooth is active.
inline uint8_t effectivePowerSave(uint8_t configured,
                                  bool bluetooth_active) {
  if (configured > kPowerSaveMax) configured = kDefaultPowerSave;
  return bluetooth_active && configured == kPowerSaveNone
      ? kPowerSaveMin : configured;
}

}  // namespace wifi
}  // namespace mesh
