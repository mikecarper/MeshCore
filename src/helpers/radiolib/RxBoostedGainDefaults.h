#pragma once

#include <stdint.h>

namespace mesh {
namespace radio {

// SX126x targets use SX126X_RX_BOOSTED_GAIN while LR11xx and several other
// radios use RX_BOOSTED_GAIN. Keep persisted first-install defaults aligned
// with whichever target-level setting initialized the physical radio.
constexpr uint8_t selectRxBoostedGainDefault(bool sx_configured,
                                             int sx_value,
                                             bool generic_configured,
                                             int generic_value,
                                             uint8_t fallback = 1) {
  return sx_configured ? (sx_value ? 1 : 0)
                       : (generic_configured ? (generic_value ? 1 : 0)
                                             : (fallback ? 1 : 0));
}

inline uint8_t configuredRxBoostedGainDefault() {
#ifdef SX126X_RX_BOOSTED_GAIN
  return selectRxBoostedGainDefault(true, SX126X_RX_BOOSTED_GAIN,
                                    false, 0);
#elif defined(RX_BOOSTED_GAIN)
  return selectRxBoostedGainDefault(false, 0, true, RX_BOOSTED_GAIN);
#else
  return selectRxBoostedGainDefault(false, 0, false, 0);
#endif
}

} // namespace radio
} // namespace mesh
