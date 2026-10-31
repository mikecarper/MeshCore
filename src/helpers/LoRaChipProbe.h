#pragma once

#include "LoRaRadioFamily.h"
#include <stddef.h>
#include <stdint.h>
#include <string.h>

namespace mesh {

// SX1276/78 and RFM9x share this register layout. SX1272 (0x22) does not.
inline bool isSX1276Version(uint8_t version) {
  return version == 0x12 || version == 0x13 || version == 0x11;
}

// IO supplies reset(), readSX127Version(), ready(), readSX126Version(),
// now(), and pause(). Identification never starts RX, CAD or transmission.
template <typename IO>
LoRaRadioFamily probeLoRaChip(IO& io, uint32_t timeout_ms = 250) {
  io.reset();
  const uint8_t version = io.readSX127Version();
  if (isSX1276Version(version) && io.readSX127Version() == version)
    return LoRaRadioFamily::SX127x;

  // Discard the unrecognized command/status left on an SX126x by the first
  // read. A reset enters RC standby, so no speculative mode-setting is needed.
  io.reset();
  const uint32_t started = io.now();
  while (uint32_t(io.now() - started) < timeout_ms) {
    if (io.ready()) {
      char signature[6] = {};
      char confirm[6] = {};
      // SX1262 intentionally reports the SX1261 family signature in RadioLib.
      if (io.readSX126Version(signature, sizeof(signature))
          && memcmp(signature, "SX1261", sizeof(signature)) == 0
          && io.ready()
          && io.readSX126Version(confirm, sizeof(confirm))
          && memcmp(signature, confirm, sizeof(signature)) == 0)
        return LoRaRadioFamily::SX126x;
    }
    io.pause(1);
  }
  return LoRaRadioFamily::None;
}

}  // namespace mesh
