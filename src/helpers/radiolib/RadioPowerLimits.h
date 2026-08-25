#pragma once

#include <stdint.h>
#include "LR2021Band.h"

// Radio families do not share a universal power range. Keep unspecified
// bounds at the persisted int8_t limits and let the active driver validate the
// request. Boards with an external PA can still impose a stricter input cap.

namespace mesh {

inline int8_t minLoRaTxPowerForFrequency(float frequency) {
#if defined(USE_LR2021)
  int8_t minimum = lr2021::isHighBand(frequency) ? -19 : -9;
#else
  (void)frequency;
  int8_t minimum = INT8_MIN;
#endif
#ifdef MIN_LORA_TX_POWER
  if (minimum < MIN_LORA_TX_POWER) minimum = MIN_LORA_TX_POWER;
#endif
  return minimum;
}

inline int8_t maxLoRaTxPowerForFrequency(float frequency) {
#if defined(USE_LR2021)
  int8_t maximum = lr2021::isHighBand(frequency) ? 12 : 22;
#else
  (void)frequency;
  int8_t maximum = INT8_MAX;
#endif
#ifdef MAX_LORA_TX_POWER
  if (maximum > MAX_LORA_TX_POWER) maximum = MAX_LORA_TX_POWER;
#endif
  return maximum;
}

inline bool isLoRaTxPowerValid(int32_t power, float frequency) {
  return power >= minLoRaTxPowerForFrequency(frequency)
      && power <= maxLoRaTxPowerForFrequency(frequency);
}

inline int8_t clampLoRaTxPower(int32_t power, float frequency) {
  const int8_t minimum = minLoRaTxPowerForFrequency(frequency);
  const int8_t maximum = maxLoRaTxPowerForFrequency(frequency);
  if (power < minimum) return minimum;
  if (power > maximum) return maximum;
  return static_cast<int8_t>(power);
}

}  // namespace mesh
