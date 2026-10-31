#pragma once

#include "LoRaRadioFamily.h"
#include <stddef.h>
#include <stdint.h>
#include <string.h>

namespace mesh {

// IO supplies reset(), ready(), readSX126Version(), readLR1121Version(),
// now() and pause(). Only reset and identification reads are permitted here;
// the selected driver's normal begin() owns TCXO, RF routing and calibration.
template <typename IO>
LoRaRadioFamily probeSX1262OrLR1121(IO& io, uint32_t timeout_ms = 250) {
  io.reset();
  uint32_t started = io.now();
  while (!io.ready()) {
    if (uint32_t(io.now() - started) >= timeout_ms) return LoRaRadioFamily::None;
    io.pause(1);
  }
  char signature[6] = {}, confirm[6] = {};
  if (io.readSX126Version(signature, sizeof(signature))
      && memcmp(signature, "SX1261", sizeof(signature)) == 0 && io.ready()
      && io.readSX126Version(confirm, sizeof(confirm))
      && memcmp(signature, confirm, sizeof(signature)) == 0)
    return LoRaRadioFamily::SX126x;

  // Reset between command sets. The SX126x read-register opcode is not a
  // supported LR11xx command, so discard that error before its GetVersion.
  io.reset();
  started = io.now();
  while (!io.ready()) {
    if (uint32_t(io.now() - started) >= timeout_ms) return LoRaRadioFamily::None;
    io.pause(1);
  }
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
