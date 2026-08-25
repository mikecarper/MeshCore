#pragma once

namespace mesh {
namespace lr2021 {

// RadioLib and the LR2021 use separate low- and high-frequency front ends.
// There are no valid channels near this boundary: LF ends at 1090 MHz and HF
// starts at 1900 MHz, so the midpoint is a stable band selector.
static constexpr float LF_HF_CROSSOVER_MHZ = 1500.0f;

inline bool isHighBand(float frequency) {
  return frequency > LF_HF_CROSSOVER_MHZ;
}

}  // namespace lr2021
}  // namespace mesh
