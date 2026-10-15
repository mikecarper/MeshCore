#pragma once

#include <math.h>
#include <stddef.h>
#include <stdio.h>
#include <string.h>

namespace mesh {

// Opt-in bench safety boundary, not a user setting or production default.
// Receive profiles may use other frequencies; no transmitter may do so.
inline bool hilTransmitFrequencyAllowed(float frequency_mhz) {
#if defined(MESH_HIL_TX_FREQ_MHZ)
  static_assert(MESH_HIL_TX_FREQ_MHZ >= 150 && MESH_HIL_TX_FREQ_MHZ <= 2500,
                "HIL TX frequency must be an explicit valid MHz value");
  return isfinite(frequency_mhz)
      && fabsf(frequency_mhz - static_cast<float>(MESH_HIL_TX_FREQ_MHZ)) <= 0.0001f;
#else
  (void)frequency_mhz;
  return true;
#endif
}

// Runtime preflight must identify the same compiled boundary as the RF gate.
// No setter exists, and ordinary production images expose no such command.
inline bool handleHilTxFrequencyCommand(const char* command, char* reply, size_t capacity) {
#if defined(MESH_HIL_TX_FREQ_MHZ)
  if (!command || !reply || !capacity || strcmp(command, "get lab.tx") != 0) return false;
  snprintf(reply, capacity,
           "> HIL TX lock=%.3f MHz; off-frequency TX blocked; primary RX-only when off-frequency",
           static_cast<double>(MESH_HIL_TX_FREQ_MHZ));
  return true;
#else
  (void)command;
  (void)reply;
  (void)capacity;
  return false;
#endif
}

}  // namespace mesh
