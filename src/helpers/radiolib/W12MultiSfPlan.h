#pragma once

#include <stdint.h>
#include <math.h>

#include "LR2021SideDetectorConfig.h"

namespace mesh { namespace w12multisf {

// Admission for an experimental receive-only multi-SF plan. This does not
// change the modem: callers must still acquire it and prove TX/RX restoration.
enum class Admission : uint8_t {
  Ready, Unsupported, InvalidRange, TxOutsideRange, UnsupportedRange, InvalidBandwidth, DualRadio, RxPowerSaving,
  ExtraDetectors
};

struct Plan {
  uint8_t rx_primary = 0;
  uint8_t tx_sf = 0;
  uint8_t side_sf[mesh::lr2021::MAX_SIDE_DETECTORS] = {};
  uint8_t side_count = 0;
};

struct ActivityBounds {
  uint32_t preamble_ms = 0;
  uint32_t payload_ms = 0;
};

// Bound the LR2021 activity watchdog for the slowest enabled receive SF.
// Explicit header and the longest coding rate are assumed. The caller chooses
// the sender preamble budget; longer unconfigured preambles remain unqualified.
inline bool activityBounds(const Plan& plan, float bandwidth_khz,
                           uint16_t sender_preamble_symbols, uint16_t max_payload,
                           ActivityBounds& out) {
  out = {};
  const uint8_t slowest = plan.rx_primary + plan.side_count;
  if (slowest < 5 || slowest > 12 || !isfinite(bandwidth_khz)
      || bandwidth_khz <= 0 || bandwidth_khz > 1000 || !sender_preamble_symbols)
    return false;
  // Nominal bandwidths such as 83.34 kHz are rounded. Bias low so rounding
  // cannot shorten a deadline for the actual radio bandwidth.
  const uint32_t bw_hz = static_cast<uint32_t>(bandwidth_khz * 999.0f);
  if (!bw_hz) return false;
  const uint64_t symbol_us = ((uint64_t(1) << slowest) * 1000000u + bw_hz - 1) / bw_hz;
  const uint32_t denominator = 4u * (slowest - (symbol_us >= 16000 ? 2u : 0u));
  const uint32_t payload_numerator = 8u * max_payload + 64u;
  const uint32_t blocks = (payload_numerator + denominator - 1) / denominator;
  // Extra symbols cover framing, rounding and the receive-side IRQ guard.
  const uint64_t preamble_us = (uint64_t(sender_preamble_symbols) + 20u) * symbol_us;
  const uint64_t payload_us = (16u + 8u * blocks) * symbol_us;
  const uint64_t preamble_ms = (preamble_us + 999u) / 1000u;
  const uint64_t payload_ms = (payload_us + 999u) / 1000u;
  if (preamble_ms > UINT32_MAX || payload_ms > UINT32_MAX) return false;
  out.preamble_ms = static_cast<uint32_t>(preamble_ms);
  out.payload_ms = static_cast<uint32_t>(payload_ms);
  return true;
}

inline Admission makePlan(bool supported, uint8_t lower, uint8_t upper,
                          uint8_t tx_sf, float bandwidth_khz,
                          bool dual_radio, bool rx_power_saving,
                          bool extra_detectors, Plan& out) {
  out = {};
  if (!supported) return Admission::Unsupported;
  if (lower < 5 || upper > 12 || lower > upper || upper - lower > 3
      || tx_sf < 5 || tx_sf > 12) return Admission::InvalidRange;
  if (tx_sf < lower || tx_sf > upper) return Admission::TxOutsideRange;
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
  if (lower >= 10 && candidate.side_count > 1) return Admission::UnsupportedRange;
  for (uint8_t i = 0; i < candidate.side_count; ++i)
    candidate.side_sf[i] = lower + i + 1;
  if (!mesh::lr2021::validateSideDetectorSFs(candidate.side_sf,
          candidate.side_count, candidate.rx_primary, bandwidth_khz))
    return Admission::InvalidBandwidth;
  out = candidate;
  return Admission::Ready;
}

} }  // namespace mesh::w12multisf
