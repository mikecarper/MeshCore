#pragma once

#include <stdint.h>

namespace mesh {
namespace wifi {

// MeshCore stores WiFi power save as: 0=min, 1=none, 2=max. ESP32 WiFi and
// Bluetooth coexistence requires modem sleep, so "none" must fall back to the
// minimum sleep policy whenever Bluetooth is active.
inline uint8_t effectivePowerSave(uint8_t configured,
                                  bool bluetooth_active) {
  if (configured > 2) configured = 1;
  return bluetooth_active && configured == 1 ? 0 : configured;
}

}  // namespace wifi
}  // namespace mesh
