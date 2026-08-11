#pragma once

#if defined(ESP32_PLATFORM)

#include <stddef.h>
#include <stdint.h>

namespace mesh {

// Capture true hardware entropy during Arduino's early initialization, before
// variant, board, ADC, Wi-Fi, or Bluetooth setup. ESP32Board::begin() calls
// this again as an idempotent fallback.
void initializeESP32TrueRandom();

// XOR previously captured true hardware entropy into every requested byte.
// This is fail-closed: if a complete hardware block is unavailable, dest is
// securely erased and the ESP32 restarts instead of returning pseudo-only
// output.
void mixESP32TrueRandom(uint8_t* dest, size_t size);

// Securely erase and permanently close the startup pool once the persisted or
// newly generated identity is ready. It cannot be reopened later because ADC
// or RF peripherals may have started by then.
void discardESP32TrueRandom();

} // namespace mesh

#endif
