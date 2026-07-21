#pragma once

#include <stdint.h>

namespace mesh {

enum class RadioRecoveryAction : uint8_t {
  NONE,
  SOFT,
  HARD,
};

// Tracks proof that the radio hardware is alive independently of the MCU main
// loop.  A successful packet, IRQ, or TX resets the schedule.  Silence first
// requests a non-destructive RX/AGC re-arm, then a radio-only hardware reset.
class RadioLivenessTracker {
  static const uint32_t HARD_RETRY_MS = 30000UL;

  uint32_t _last_activity;
  uint32_t _last_hard_attempt;
  uint8_t _stage;

public:
  RadioLivenessTracker() : _last_activity(0), _last_hard_attempt(0), _stage(0) {}

  void begin(uint32_t now) {
    _last_activity = now;
    _last_hard_attempt = 0;
    _stage = 0;
  }

  void noteActivity(uint32_t now) {
    _last_activity = now;
    _last_hard_attempt = 0;
    _stage = 0;
  }

  // A completed hard recovery is a safe point from which to restart the
  // liveness window. Failed recoveries remain escalated and retry at a bounded
  // cadence, instead of being silently deferred for another full interval.
  void noteHardRecoveryResult(uint32_t now, bool success) {
    if (success) noteActivity(now);
  }

  RadioRecoveryAction poll(uint32_t now, uint32_t soft_after_ms,
                           uint32_t hard_after_ms) {
    const uint32_t silent_for = now - _last_activity;
    if (hard_after_ms > 0 && silent_for >= hard_after_ms) {
      if (_stage < 2 || now - _last_hard_attempt >= HARD_RETRY_MS) {
        _stage = 2;
        _last_hard_attempt = now;
        return RadioRecoveryAction::HARD;
      }
      return RadioRecoveryAction::NONE;
    }
    if (_stage == 0 && soft_after_ms > 0 && silent_for >= soft_after_ms) {
      _stage = 1;
      return RadioRecoveryAction::SOFT;
    }
    return RadioRecoveryAction::NONE;
  }

  uint32_t lastActivity() const { return _last_activity; }
  uint8_t stage() const { return _stage; }
};

} // namespace mesh
