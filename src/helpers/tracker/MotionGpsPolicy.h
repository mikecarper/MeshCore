#pragma once

#include <stdint.h>
#include "Lis2dhMotionLatch.h"

namespace mesh { namespace tracker { namespace motion {

// Schedules only motion-triggered acquisitions. Scheduled owner checks and
// explicit operator requests retain their own deadlines and are never delayed.
// All timestamps are monotonic 32-bit millis, and every delay is < 2^31 ms.
class GpsPolicy {
 public:
  static constexpr uint32_t kNormalDelayMs = 15UL * 60 * 1000;
  static constexpr uint32_t kHikingDelayMs = 5UL * 60 * 1000;
  static constexpr uint32_t kInitialFailureDelayMs = 60UL * 60 * 1000;
  static constexpr uint32_t kMaximumFailureDelayMs = 6UL * 60 * 60 * 1000;

  void reset() {
    pending_ = false;
    has_deadline_ = false;
    failures_ = 0;
    deadline_ = 0;
    last_completed_ = 0;
  }

  void noteMotion() { pending_ = true; }
  bool pending() const { return pending_; }

  bool due(uint32_t now) const {
    return pending_ && (!has_deadline_ || static_cast<int32_t>(now - deadline_) >= 0);
  }

  // Recalculate a not-yet-due successful-fix deadline when the user changes
  // profile. A failure backoff or work already due must never be postponed.
  void setHiking(bool hiking, uint32_t now) {
    const uint32_t delay = hiking ? kHikingDelayMs : kNormalDelayMs;
    if (delay == normal_delay_) return;
    normal_delay_ = delay;
    if (has_deadline_ && failures_ == 0 && static_cast<int32_t>(now - deadline_) < 0)
      deadline_ = last_completed_ + delay;
  }

  // Called for *any* tracker GPS start, including a scheduled/manual owner
  // check, so the same movement cannot immediately launch a second fix.
  void started() { pending_ = false; }

  void completed(uint32_t now, bool fresh_valid_fix) {
    uint32_t delay = normal_delay_;
    if (fresh_valid_fix) {
      failures_ = 0;
    } else {
      if (failures_ < 4) ++failures_;
      delay = kInitialFailureDelayMs << (failures_ - 1);
      if (delay > kMaximumFailureDelayMs) delay = kMaximumFailureDelayMs;
    }
    deadline_ = now + delay;
    last_completed_ = now;
    has_deadline_ = true;
  }

 private:
  bool pending_ = false;
  bool has_deadline_ = false;
  uint8_t failures_ = 0;
  uint32_t deadline_ = 0;
  uint32_t last_completed_ = 0;
  uint32_t normal_delay_ = kNormalDelayMs;
};

// "Quiet" is evidence accumulated from a working sensor, never inferred from
// missing I2C data. A motion edge is emitted once after a quiet spell; a
// 15-minute limit prevents small intermittent movements from repeatedly
// scheduling owner traffic.
class QuietPolicy {
 public:
  static constexpr uint32_t kQuietMs = 5UL * 60 * 1000;
  static constexpr uint32_t kEarlyOwnerCooldownMs = 15UL * 60 * 1000;

  void reset() {
    quiet_started_ = false;
    confirmed_ = false;
    has_early_ = false;
    quiet_since_ = last_early_ = 0;
  }

  bool observe(Observation observation, uint32_t now) {
    if (observation == Observation::Unknown) {
      quiet_started_ = confirmed_ = false;
      return false;
    }
    if (observation == Observation::NoMotionObserved) {
      if (!quiet_started_) { quiet_started_ = true; quiet_since_ = now; }
      if (uint32_t(now - quiet_since_) >= kQuietMs) confirmed_ = true;
      return false;
    }
    const bool may_request = confirmed_
        && (!has_early_ || uint32_t(now - last_early_) >= kEarlyOwnerCooldownMs);
    quiet_started_ = confirmed_ = false;
    if (may_request) { has_early_ = true; last_early_ = now; }
    return may_request;
  }

  bool confirmed() const { return confirmed_; }

 private:
  bool quiet_started_ = false;
  bool confirmed_ = false;
  bool has_early_ = false;
  uint32_t quiet_since_ = 0;
  uint32_t last_early_ = 0;
};

}}} // namespace mesh::tracker::motion
