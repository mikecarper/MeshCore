#pragma once

#include <stdint.h>

#ifndef MESH_RADIO_AUTO_SX1268
#define MESH_RADIO_AUTO_SX1268 0
#endif
#ifndef MESH_RADIO_AUTO_SX127X
#define MESH_RADIO_AUTO_SX127X 1
#endif
#ifndef MESH_RADIO_AUTO_LR1121
#define MESH_RADIO_AUTO_LR1121 0
#endif
#if (MESH_RADIO_AUTO_SX1268 != 0 && MESH_RADIO_AUTO_SX1268 != 1) \
    || (MESH_RADIO_AUTO_SX127X != 0 && MESH_RADIO_AUTO_SX127X != 1) \
    || (MESH_RADIO_AUTO_LR1121 != 0 && MESH_RADIO_AUTO_LR1121 != 1)
#error "Automatic radio-family options must be 0 or 1"
#endif

namespace mesh {
enum class LoRaRadioFamily : uint8_t { None, SX126x, SX127x, SX1268, LR1121 };

#if MESH_RADIO_AUTO_LR1121
// Muzi has one physical radio. Legacy RXPS timing helpers have no radio
// argument; publish its positively identified family for their default policy.
inline LoRaRadioFamily& autoLoRaRxPowerSavingFamily() {
  static LoRaRadioFamily family = LoRaRadioFamily::None;
  return family;
}
#endif

inline float autoLoRaInitialFrequency(LoRaRadioFamily family, float preferred) {
  // A universal image can contain a 900 MHz preset, but an SX1268 cannot.
  // Only its invalid initial default falls back; valid saved tuning is kept.
#if defined(MESH_E22_M33S) && MESH_E22_M33S
  const float low_band_default = 433.0f;
#else
  const float low_band_default = 434.0f;
#endif
  return family == LoRaRadioFamily::SX1268 && !(preferred >= 410.0f && preferred <= 810.0f)
      ? low_band_default : preferred;
}
}
