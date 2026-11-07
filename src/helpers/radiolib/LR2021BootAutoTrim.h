#pragma once

#include <stdint.h>

namespace mesh { namespace lr2021 {

// Scheduling only: the existing AutoTrim still verifies three signed peers
// and an independent final batch. This class neither selects nor applies trim.
// Runtime computes enabled/calibrated from the actual saved policy/calibration,
// and supplies eligible only after checking current primary radio ownership.
class LR2021BootAutoTrim {
 public:
  enum class State : uint8_t {
    Disabled, WaitingRadio, Ready, WaitingReferences, Backoff,
    Retained, Cancelled, Error, Exhausted
  };
  static constexpr uint8_t MaximumAttempts = 2;
  static constexpr uint32_t AttemptTimeoutMs = 15UL * 60UL * 1000UL;
  static constexpr uint32_t BackoffMs = 30UL * 60UL * 1000UL;

  // Boot-only reset. An ordinary setting change must never reset the budget,
  // cancellation, or storage/recovery error guards.
  void reset(uint32_t now, bool enabled, bool calibrated, bool storage_ok) {
    started_at_ = finished_at_ = now;
    attempts_ = 0;
    enabled_ = enabled; calibrated_ = calibrated; storage_ok_ = storage_ok;
    running_ = backoff_ = cancelled_ = error_ = false;
    refresh(now, false);
  }

  bool ready(uint32_t now, bool eligible) {
    refresh(now, eligible);
    return state_ == State::Ready;
  }

  // Consume an admitted background attempt before bounded storage preflight;
  // failed preflight finishes unsafe. Repeated or out-of-order notifications
  // cannot consume or refresh another attempt.
  bool started(uint32_t now) {
    if (state_ != State::Ready || running_ || attempts_ >= MaximumAttempts) return false;
    ++attempts_; running_ = true; backoff_ = false; started_at_ = now;
    refresh(now, false);
    return true;
  }

  // safe means checked rollback or verified persisted calibration completed.
  // A silent/incomplete reference window is uncalibrated, never a zero error.
  void finished(uint32_t now, bool calibrated, bool safe) {
    if (!running_) return;
    running_ = false; calibrated_ = calibrated_ || calibrated;
    error_ = error_ || !safe;
    finished_at_ = now; backoff_ = !calibrated_ && safe;
    refresh(now, false);
  }

  // A manual calibration/cancel command reserves the current boot for manual
  // intent. Runtime must separately cancel/restore the existing AutoTrim.
  void cancel() {
    cancelled_ = true;
    refresh(started_at_, false);
  }

  void setEnabled(uint32_t now, bool enabled) {
    enabled_ = enabled;
    refresh(now, false);
  }

  State state() const { return state_; }
  uint8_t attempts() const { return attempts_; }
  bool enabled() const { return enabled_; }
  bool calibrated() const { return calibrated_; }
  bool inProgress() const { return running_; }
  bool expired(uint32_t now) const {
    return running_ && uint32_t(now - started_at_) >= AttemptTimeoutMs;
  }
  // Timer delay only: zero is not permission to start. ready() also enforces
  // calibration, ownership, opt-out, attempt, cancel and error gates.
  uint32_t nextDelay(uint32_t now) const {
    if (!backoff_ || running_ || !enabled_ || calibrated_ || cancelled_
        || error_ || !storage_ok_ || attempts_ >= MaximumAttempts) return 0;
    const uint32_t elapsed = now - finished_at_;
    return elapsed < BackoffMs ? BackoffMs - elapsed : 0;
  }
  static const char* stateName(State state) {
    switch (state) {
      case State::WaitingRadio: return "waiting-radio";
      case State::Ready: return "ready";
      case State::WaitingReferences: return "waiting-refs";
      case State::Backoff: return "backoff";
      case State::Retained: return "retained";
      case State::Cancelled: return "cancelled";
      case State::Error: return "error";
      case State::Exhausted: return "exhausted";
      default: return "disabled";
    }
  }
  const char* stateName() const { return stateName(state_); }

 private:
  void refresh(uint32_t now, bool eligible) {
    // Once observed due, retain that fact while ownership remains unavailable;
    // a later complete millis wrap must not arm the same backoff again.
    if (backoff_ && uint32_t(now - finished_at_) >= BackoffMs) backoff_ = false;
    if (!storage_ok_ || error_) state_ = State::Error;
    else if (cancelled_) state_ = State::Cancelled;
    else if (calibrated_) state_ = State::Retained;
    else if (!enabled_) state_ = State::Disabled;
    else if (running_) state_ = State::WaitingReferences;
    else if (attempts_ >= MaximumAttempts) state_ = State::Exhausted;
    else if (nextDelay(now)) state_ = State::Backoff;
    else state_ = eligible ? State::Ready : State::WaitingRadio;
  }
  uint32_t started_at_ = 0, finished_at_ = 0;
  State state_ = State::Disabled;
  uint8_t attempts_ = 0;
  bool enabled_ = false, calibrated_ = false, storage_ok_ = false;
  bool running_ = false, backoff_ = false, cancelled_ = false, error_ = false;
};

static_assert(sizeof(LR2021BootAutoTrim) <= 24, "Boot auto-trim policy must remain bounded");
using BootAutoTrim = LR2021BootAutoTrim;

}}  // namespace mesh::lr2021
