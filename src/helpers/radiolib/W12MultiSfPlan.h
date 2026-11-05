#pragma once

#include <stdint.h>
#include <math.h>

#include "LR2021SideDetectorConfig.h"

namespace mesh { namespace w12multisf {

// Admission for an experimental receive-only multi-SF plan. This does not
// change the modem: callers must still acquire it and prove TX/RX restoration.
enum class Admission : uint8_t {
  Ready, Unsupported, InvalidRange, InvalidBandwidth, DualRadio, RxPowerSaving,
  ExtraDetectors
};

struct Plan {
  uint8_t rx_primary = 0;
  uint8_t tx_sf = 0;
  uint8_t side_sf[mesh::lr2021::MAX_SIDE_DETECTORS] = {};
  uint8_t side_count = 0;
};

inline Admission makePlan(bool supported, uint8_t lower, uint8_t upper,
                          uint8_t tx_sf, float bandwidth_khz,
                          bool dual_radio, bool rx_power_saving,
                          bool extra_detectors, Plan& out) {
  out = {};
  if (!supported) return Admission::Unsupported;
  if (lower < 5 || upper > 12 || lower > upper || upper - lower > 3
      || tx_sf < 5 || tx_sf > 12) return Admission::InvalidRange;
  if (dual_radio) return Admission::DualRadio;
  if (rx_power_saving) return Admission::RxPowerSaving;
  if (extra_detectors) return Admission::ExtraDetectors;

  static constexpr float supported_bw[] = {31.25f, 41.67f, 41.7f, 62.5f,
      83.34f, 101.56f, 125, 203.125f, 250, 406.25f, 500, 812.5f, 1000};
  bool bandwidth_supported = false;
  for (float bw : supported_bw)
    if (fabsf(bandwidth_khz - bw) < 0.01f) bandwidth_supported = true;
  if (!bandwidth_supported) return Admission::InvalidBandwidth;

  Plan candidate;
  candidate.rx_primary = lower;
  candidate.tx_sf = tx_sf;
  candidate.side_count = upper - lower;
  for (uint8_t i = 0; i < candidate.side_count; ++i)
    candidate.side_sf[i] = lower + i + 1;
  if (!mesh::lr2021::validateSideDetectorSFs(candidate.side_sf,
          candidate.side_count, candidate.rx_primary, bandwidth_khz))
    return Admission::InvalidBandwidth;
  out = candidate;
  return Admission::Ready;
}

} }  // namespace mesh::w12multisf
