#pragma once

#include <stdint.h>

namespace mesh {
namespace ui {

constexpr bool shouldDisplayBluetoothPairingPin(bool bluetooth_enabled,
                                                bool bluetooth_connected,
                                                uint32_t pairing_pin) {
  return bluetooth_enabled && !bluetooth_connected && pairing_pin != 0;
}

constexpr bool isBluetoothPairingPromptActive(bool bluetooth_enabled,
                                              bool bluetooth_connected,
                                              uint32_t display_until,
                                              uint32_t now) {
  return bluetooth_enabled && !bluetooth_connected && display_until != 0
      && static_cast<int32_t>(now - display_until) < 0;
}

}  // namespace ui
}  // namespace mesh
