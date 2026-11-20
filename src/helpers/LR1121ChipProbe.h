#pragma once

#include "LoRaRadioFamily.h"
#include <stddef.h>
#include <stdint.h>
#include <string.h>

namespace mesh {

// Only reset and identification reads are permitted here. The selected
// driver's normal begin() owns TCXO, RF routing and calibration.
template <typename IO>
LoRaRadioFamily probeSX1262OrLR1121(IO& io, uint32_t timeout_ms = 250) {
  io.reset(1, 10);
  char signature[6] = {}, confirm[6] = {};
  if (io.waitReady(timeout_ms) && io.readSX126Version(signature, sizeof(signature))
      && memcmp(signature, "SX1261", sizeof(signature)) == 0 && io.ready()
      && io.readSX126Version(confirm, sizeof(confirm))
      && memcmp(signature, confirm, sizeof(signature)) == 0)
    return LoRaRadioFamily::SX126x;

  // An LR1121 can still be in reset when the SX1262 probe times out. Use
  // RadioLib's LR11xx reset pulse and settle time before sending GetVersion.
  // This also clears any error from the SX126x read-register opcode.
  io.reset(10, 300);
  if (!io.waitReady(timeout_ms)) return LoRaRadioFamily::None;
  uint8_t version[4] = {}, second[4] = {};
  // RadioLib's GetVersion returns [hardware, device, firmware major, minor].
  // LR1110=1, LR1120=2 and bootloader=0xdf must never select LR1121=3.
  if (io.readLR1121Version(version, sizeof(version)) && version[1] == 0x03
      && io.ready() && io.readLR1121Version(second, sizeof(second))
      && memcmp(version, second, sizeof(version)) == 0)
    return LoRaRadioFamily::LR1121;
  return LoRaRadioFamily::None;
}

}  // namespace mesh
