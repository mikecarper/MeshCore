#pragma once

#include <stdint.h>

namespace mesh {
class Identity;
class Radio;

#if defined(ESP32_PLATFORM) || defined(NRF52_PLATFORM)
bool verifyAdvertWhileScanning(Radio& radio, const Identity& identity,
                               const uint8_t* signature, const uint8_t* message, int length);
#endif
}  // namespace mesh
