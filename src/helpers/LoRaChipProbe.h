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
// SX126x-only boards skip the unrelated SX127x register command entirely.
template <typename IO>
LoRaRadioFamily probeLoRaChip(IO& io, uint32_t timeout_ms = 250,
                             bool probe_sx127x = MESH_RADIO_AUTO_SX127X) {
  io.reset();
  if (probe_sx127x) {
    const uint8_t version = io.readSX127Version();
    if (isSX1276Version(version) && io.readSX127Version() == version)
      return LoRaRadioFamily::SX127x;
    // Discard the unrecognized command/status left by the SX127x read.
    io.reset();
  }
  const uint32_t started = io.now();
  while (uint32_t(io.now() - started) < timeout_ms) {
    if (io.ready()) {
      char signature[6] = {};
      char confirm[6] = {};
      if (!io.readSX126Version(signature, sizeof(signature))) {
        io.pause(1);
        continue;
      }
      // RadioLib expects SX1261 from SX1262, and SX1268 from SX1268.
      // They must select their own PA/range drivers rather than one alias.
      LoRaRadioFamily family = LoRaRadioFamily::None;
      if (memcmp(signature, "SX1261", sizeof(signature)) == 0)
        family = LoRaRadioFamily::SX126x;
#if MESH_RADIO_AUTO_SX1268
      else if (memcmp(signature, "SX1268", sizeof(signature)) == 0)
        family = LoRaRadioFamily::SX1268;
#endif
      if (family != LoRaRadioFamily::None && io.ready()
          && io.readSX126Version(confirm, sizeof(confirm))
          && memcmp(signature, confirm, sizeof(signature)) == 0)
        return family;
    }
    io.pause(1);
  }
  return LoRaRadioFamily::None;
}

}  // namespace mesh
