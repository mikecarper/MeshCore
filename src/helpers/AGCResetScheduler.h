#pragma once

#include <stdint.h>

namespace mesh {

// Bounded reset cadence, not an AGC-health detector. Only an accepted reset
// sequence advances the adaptive stage; quiet traffic is not proof of failure.
class AGCResetScheduler {
  static constexpr uint32_t ADAPTIVE = 0x80000000UL;
  static constexpr uint32_t BASE_MASK = 0xfffffUL;
  static constexpr uint32_t STAGE = 0x100000UL;
  static constexpr uint32_t STREAK = 0x2000000UL;
  uint32_t _next = 0;
  uint32_t _recovery_count = 0;
  // Fixed mode stores the complete positive signed-millis interval. Adaptive
  // preferences use at most 255 * 4000 ms, leaving room for a five-bit stage
  // and one-bit attempt streak. This is RAM state, never a persisted format.
  uint32_t _state = 0;

public:
  void reset() {
    _next = _recovery_count = _state = 0;
  }

  void configure(uint32_t now, uint32_t base_ms, bool adaptive,
                 uint32_t recovery_count) {
    // All supported preference intervals fit in the signed millis half-range.
    // Invalid external input must not turn into an immediately due reset loop.
    if (base_ms == 0 || base_ms > INT32_MAX) {
      reset();
      return;
    }
    // Larger external intervals retain their exact fixed cadence. Every
    // supported preference fits the adaptive representation without rounding.
    const uint32_t config = base_ms
        | (adaptive && base_ms <= 1020000UL ? ADAPTIVE : 0);
    const uint32_t previous = _state & ADAPTIVE
        ? _state & (ADAPTIVE | BASE_MASK) : _state;
    if (previous != config || _recovery_count != recovery_count) {
      _state = config;
      _next = now + base_ms;
      _recovery_count = recovery_count;
    }
  }

  void noteTransmit(uint32_t now) {
    if (_state && !(_state & ADAPTIVE)) _next = now + _state;
  }

  bool due(uint32_t now) const {
    return _state != 0 && static_cast<int32_t>(now - _next) >= 0;
  }

  // A busy operation changes only its retry deadline, never the reset cadence
  // or adaptive stage. This permits sleep instead of spinning on a due reset.
  void noteDeferred(uint32_t now) {
    if (_state) _next = now + 50;
  }

  // Call only after an actual accepted reset sequence, not a busy/deferred one.
  void noteAttempt(uint32_t now) {
    if (!_state) return;
    uint32_t value = interval();
    if (_state & ADAPTIVE) {
      _state ^= STREAK;
      if (!(_state & STREAK) && value < 60000UL) {
        // Advance only below the ceiling: stage <= 16 even for a 1 ms base,
        // and the shifted interval remains below 120000 before clamping.
        _state += STAGE;
        value *= 2;
        if (value > 60000UL) value = 60000UL;
      }
    }
    // Anchor one interval to this accepted attempt, never replay missed slots.
    _next = now + value;
  }

  uint32_t interval() const {
    if (!(_state & ADAPTIVE)) return _state;
    const uint32_t base = _state & BASE_MASK;
    const uint32_t value = base << ((_state >> 20) & 31);
    return base < 60000UL && value > 60000UL ? 60000UL : value;
  }
  uint32_t nextAt() const { return _next; }
  bool recoveryMatches(uint32_t count) const { return _recovery_count == count; }

  bool nextDelay(uint32_t now, uint32_t& delay_ms) const {
    if (!_state) return false;
    delay_ms = due(now) ? 0 : _next - now;
    return true;
  }
};

}  // namespace mesh
