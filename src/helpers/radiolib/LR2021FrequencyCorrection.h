#pragma once

#include <stdint.h>

namespace mesh { namespace lr2021 {

constexpr int32_t MaxFrequencyCorrectionPpb = 5500;

inline bool validFrequencyCorrection(int32_t ppb) {
  return ppb >= -MaxFrequencyCorrectionPpb && ppb <= MaxFrequencyCorrectionPpb;
}

// The synthesizer is shared by RX and TX. Keep the selected profile's
// nominal frequency separate from the oscillator-corrected register value.
inline float correctedFrequencyMHz(float nominal_mhz, int32_t ppb) {
  return ppb == 0 ? nominal_mhz : nominal_mhz + nominal_mhz * (float(ppb) / 1000000000.0f);
}

}} // namespace mesh::lr2021
