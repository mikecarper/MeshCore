#pragma once

#include <stdint.h>

namespace mesh {

static constexpr uint8_t CLOCK_SYNC_SAMPLE_SOURCE_SIGNED_ADVERT = 1;
static constexpr uint8_t CLOCK_SYNC_SAMPLE_SOURCE_PUBLIC_CHANNEL = 2;
// Used only when /clock_sync has no valid saved threshold; persisted operator settings win.
static constexpr uint32_t CLOCK_SYNC_DRIFT_DEFAULT_SECONDS = 10UL * 60UL;

inline bool clockSyncRequiresUniquePath(bool edge_mode) {
  return !edge_mode;
}

struct ClockSyncConsensusResult {
  bool consensus;
  uint8_t fresh_count;
  uint8_t agreeing_count;
  uint8_t required_count;
  uint32_t estimate;
};

// Evaluate an in-place set of current-time estimates. A configured quorum is
// always required, and when more samples are present the agreeing cluster must
// also be a strict majority. This makes an 8-vs-8 split fail instead of letting
// the upper median choose one side arbitrarily.
inline ClockSyncConsensusResult evaluateClockSyncConsensus(uint32_t* values,
                                                           uint8_t count,
                                                           uint8_t configured_required,
                                                           uint32_t window_seconds) {
  ClockSyncConsensusResult result = {false, count, 0, configured_required, 0};
  if (values == nullptr || count < configured_required) return result;

  uint8_t strict_majority = (uint8_t)(count / 2U + 1U);
  if (strict_majority > result.required_count) result.required_count = strict_majority;

  for (uint8_t i = 1; i < count; i++) {
    uint32_t value = values[i];
    uint8_t j = i;
    while (j > 0 && values[j - 1] > value) {
      values[j] = values[j - 1];
      j--;
    }
    values[j] = value;
  }

  result.estimate = values[count / 2U];
  for (uint8_t i = 0; i < count; i++) {
    uint32_t difference = values[i] > result.estimate
        ? values[i] - result.estimate : result.estimate - values[i];
    if (difference <= window_seconds) result.agreeing_count++;
  }
  result.consensus = result.agreeing_count >= result.required_count;
  return result;
}

}  // namespace mesh
