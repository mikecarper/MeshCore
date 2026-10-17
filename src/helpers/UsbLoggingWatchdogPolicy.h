#pragma once

#include "UsbLoggingStatus.h"

namespace mesh {

// Pure policy. Persistence, protocol ownership, USB operations and board
// reboot are deliberately handled by the caller, not from an ISR/USB task.
class UsbLoggingWatchdogPolicy {
 public:
  enum class Action : uint8_t { None, SoftRecovery, Reenumerate, Reboot, ResetBackoff };
  static constexpr uint8_t MAX_STEP = 8;
  static constexpr uint32_t BASE_MS = 3600000UL;
  static constexpr uint32_t MAX_MS = 604800000UL;
  static constexpr uint32_t EARLY_RECOVERY_MS = 300000UL;
  static constexpr uint32_t RECOVERY_GRACE_MS = 60000UL;
  static constexpr uint32_t HEALTHY_RESET_MS = 600000UL;
  static constexpr uint32_t STALL_OBSERVE_MS = 30000UL;

  static uint32_t intervalMs(uint8_t step) {
    return step >= MAX_STEP ? MAX_MS : BASE_MS << step;
  }
  static uint8_t nextStep(uint8_t step) {
    return step >= MAX_STEP ? MAX_STEP : uint8_t(step + 1);
  }

  void begin(uint32_t now, uint8_t step) {
    _now = now;
    _last_tick = now;
    _step = step <= MAX_STEP ? step : MAX_STEP;
    _clock_started = true;
    clearTracking();
  }

  Action update(uint32_t now, bool active, const UsbLoggingObservation& observation,
                bool recovery_safe = true, bool reboot_allowed = true) {
    tick(now);
    if (active && observation.supported && !_active) clearTracking();
    _active = active && observation.supported;
    const bool progress = _observed && observation.tx_progress != _progress;
    _progress = observation.tx_progress;
    if (!_observed || progress || !observation.pending) _pending_since = _now;
    _observed = true;
    _stalled = observation.host_connected && observation.reader_connected
        && observation.pending && _now - _pending_since >= STALL_OBSERVE_MS;
    if (!active || !observation.supported) {
      // Still observe USB stalls while Auto is qualifying or the watchdog is
      // off, without accumulating a reboot deadline. Enabling always starts
      // a new full interval via the activation edge above.
      _fault = _healthy = _need_progress = _deferred = false;
      _stage = 0;
      return Action::None;
    }
    if (_stalled) _need_progress = true;
    if (progress && observation.host_connected && observation.reader_connected) {
      _need_progress = false;
    }
    const bool bad = !observation.host_connected || !observation.reader_connected
        || _stalled || _need_progress;
    if (!bad) {
      _fault = false;
      _stage = 0;
      _deferred = false;
      if (!_healthy) { _healthy = true; _healthy_since = _now; }
      const bool reset_due = _step != 0 && _now - _healthy_since >= HEALTHY_RESET_MS;
      _deferred = reset_due && !recovery_safe;
      return reset_due && recovery_safe ? Action::ResetBackoff : Action::None;
    }
    _healthy = false;
    if (!_fault) {
      _fault = true;
      _fault_since = _stalled ? _pending_since : _now;
    }
    // A slow but genuinely draining endpoint gets a full new interval. A
    // purge or accepted producer write cannot postpone this deadline.
    if (progress && observation.host_connected && observation.reader_connected
        && _stage == 0) _fault_since = _now;
    const bool due = _stage != 0 || _now - _fault_since >= EARLY_RECOVERY_MS;
    _deferred = due && !recovery_safe;
    if (!due || !recovery_safe) return Action::None;
    if (_stage == 0) return Action::SoftRecovery;
    if (_now - _stage_since < RECOVERY_GRACE_MS) return Action::None;
    if (_stage == 1) return Action::Reenumerate;
    // Cheap USB recovery does not move the original board-reboot deadline.
    // Auto can attempt USB-only repair without ever authorizing an MCU reset.
    return reboot_allowed && _now - _fault_since >= intervalMs(_step)
        ? Action::Reboot : Action::None;
  }

  // A Deferred backend result must not call this: it is retried by the caller.
  // Attempted means requested, not proven healthy. Only observed completion
  // can clear a previously stalled transport after destructive cleanup.
  void actionAttempted(Action action) {
    if (action == Action::SoftRecovery) {
      _need_progress = _need_progress || _stalled;
      _stage = 1;
      _stage_since = _now;
    } else if (action == Action::Reenumerate) {
      _stage = 2;
      _stage_since = _now;
    } else if (action == Action::Reboot) {
      _stage = 3;
    }
  }

  // Call only after the next retry tier was made durable. A failed commit
  // must never authorize a reboot using the old one-hour tier.
  void backoffCommitted(uint8_t step) {
    _step = step <= MAX_STEP ? step : MAX_STEP;
  }
  void restartInterval() {
    _stage = 0;
    _fault_since = _now;
    // Restarting after a veto is not evidence of transport completion.
    // Only update() clears a latched stall after real TX progress.
  }
  uint8_t backoffStep() const { return _step; }
  uint8_t stage() const { return _stage; }
  bool stalled() const { return _stalled || _need_progress; }
  bool deferred() const { return _deferred; }
  uint32_t inactiveSeconds() const {
    const uint64_t seconds = _fault ? (_now - _fault_since) / 1000 : 0;
    return seconds > UINT32_MAX ? UINT32_MAX : uint32_t(seconds);
  }

 private:
  void tick(uint32_t now) {
    if (!_clock_started) { begin(now, _step); return; }
    const uint32_t delta = now - _last_tick;
    // Ignore small out-of-order clock samples, while extending normal
    // millis rollover to a monotonic clock for nodes up longer than 49 days.
    if (delta > 0x80000000UL && uint32_t(_last_tick - now) <= 5000) return;
    _now += delta;
    _last_tick = now;
  }
  void clearTracking() {
    _fault = false;
    _healthy = false;
    _observed = false;
    _stalled = false;
    _need_progress = false;
    _deferred = false;
    _stage = 0;
    _fault_since = _pending_since = _healthy_since = _stage_since = _now;
  }
  uint64_t _now = 0, _fault_since = 0, _pending_since = 0;
  uint64_t _healthy_since = 0, _stage_since = 0;
  uint32_t _last_tick = 0, _progress = 0;
  uint8_t _step = 0, _stage = 0;
  bool _clock_started = false, _observed = false, _fault = false, _healthy = false;
  bool _active = false;
  bool _stalled = false, _need_progress = false, _deferred = false;
};

// Auto is a continuous qualification, not accumulated occasional cable use.
// Its timer is intentionally volatile across reboot: a cold start cannot prove
// that the logging reader stayed connected while the MCU was down.
class UsbLoggingAutoArmPolicy {
 public:
  static constexpr uint32_t QUALIFICATION_MS = 1209600000UL;  // 14 days
  void begin(uint32_t now) {
    _now = now;
    _last_tick = now;
    _started = true;
    _qualifying = false;
    _since = _now;
  }
  bool update(uint32_t now, bool qualifying, bool auto_mode) {
    if (!_started) begin(now);
    const uint32_t delta = now - _last_tick;
    if (!(delta > 0x80000000UL && uint32_t(_last_tick - now) <= 5000)) {
      _now += delta;
      _last_tick = now;
    }
    if (!auto_mode || !qualifying) {
      _qualifying = false;
      _since = _now;
      return false;
    }
    if (!_qualifying) { _qualifying = true; _since = _now; }
    return _now - _since >= QUALIFICATION_MS;
  }
  uint32_t connectedSeconds() const {
    const uint64_t seconds = _qualifying ? (_now - _since) / 1000 : 0;
    return seconds > UINT32_MAX ? UINT32_MAX : uint32_t(seconds);
  }
 private:
  uint64_t _now = 0, _since = 0;
  uint32_t _last_tick = 0;
  bool _started = false, _qualifying = false;
};

}  // namespace mesh
