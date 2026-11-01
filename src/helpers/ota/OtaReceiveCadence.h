#pragma once

#include <stdint.h>

namespace mesh { namespace ota {

// Learn DATA service time, not request/proof/idle turnaround. Call observe()
// only after accepting a new, structurally valid fragment. A flight boundary
// clears timestamp anchors while retaining the learned interval; a new fetch,
// wire representation or radio configuration must reset the whole estimator.
class OtaReceiveCadence {
public:
  static constexpr uint32_t MaxSampleMs = 60000;

  OtaReceiveCadence() { reset(); }

  void reset() {
    interval_ms_ = 0;
    beginFlight();
  }

  void beginFlight() {
    for (uint8_t i = 0; i < 2; ++i) anchors_[i].block = NoBlock;
    next_anchor_ = 0;
  }

  bool observe(uint32_t block, uint8_t fragment, uint32_t now_ms) {
    if (block == NoBlock || fragment >= 16) return false;
    Anchor* anchor = nullptr;
    for (uint8_t i = 0; i < 2; ++i) {
      if (anchors_[i].block == block) { anchor = &anchors_[i]; break; }
    }
    if (!anchor) {
      anchor = &anchors_[next_anchor_];
      next_anchor_ ^= 1;
      anchor->block = block;
      anchor->fragment = fragment;
      anchor->at_ms = now_ms;
      return false;
    }
    // Duplicate and backward copies must not move the timestamp forward and
    // manufacture a short cadence for the next newly accepted fragment.
    if (fragment <= anchor->fragment) return false;
    const bool consecutive = fragment == uint8_t(anchor->fragment + 1);
    const uint32_t elapsed = now_ms - anchor->at_ms;  // millis rollover is valid
    anchor->fragment = fragment;
    anchor->at_ms = now_ms;
    // Skipped fragments can include recovery turns; do not teach their gap as
    // normal packet pacing. A later consecutive pair starts sampling again.
    if (!consecutive || elapsed == 0 || elapsed > MaxSampleMs) return false;
    if (elapsed >= interval_ms_) interval_ms_ = elapsed;
    else interval_ms_ -= (interval_ms_ - elapsed + 3) / 4;
    return true;
  }

  uint32_t serviceIntervalMs(uint32_t physical_fallback_ms) const {
    // The caller's airtime/path/duty allowance remains an absolute floor.
    return interval_ms_ > physical_fallback_ms ? interval_ms_ : physical_fallback_ms;
  }

private:
  static constexpr uint32_t NoBlock = UINT32_MAX;
  struct Anchor {
    uint32_t block;
    uint32_t at_ms;
    uint8_t fragment;
  };
  Anchor anchors_[2];
  uint32_t interval_ms_;
  uint8_t next_anchor_;
};

static_assert(sizeof(OtaReceiveCadence) <= 40,
              "OTA receive cadence must remain allocation-free and bounded");

} }
