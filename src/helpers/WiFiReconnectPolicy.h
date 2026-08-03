#pragma once

#include <stdint.h>

// Pure, platform-independent timing state for the explicit WiFi reconnect
// fallback. The platform's automatic reconnect remains enabled where
// appropriate; this tracker guarantees that a stuck station is kicked with
// the saved credentials every five minutes while the link remains down.
namespace WiFiReconnectPolicy {

static const uint32_t kRetryIntervalMs = 5UL * 60UL * 1000UL;

static inline uint32_t elapsedMs(uint32_t now, uint32_t then) {
  return now - then;
}

class Tracker {
public:
  Tracker() : _tracking(false), _disconnected_since(0), _last_attempt(0) {}

  void noteConnected() {
    _tracking = false;
    _disconnected_since = 0;
    _last_attempt = 0;
  }

  // The first observation of a disconnected station starts both timers. This
  // treats the current/initial association as the first attempt and schedules
  // the explicit fallback five minutes later.
  void noteDisconnected(uint32_t now) {
    if (_tracking) return;
    _tracking = true;
    _disconnected_since = now;
    _last_attempt = now;
  }

  bool retryDue(uint32_t now) const {
    return _tracking
        && elapsedMs(now, _disconnected_since) >= kRetryIntervalMs
        && elapsedMs(now, _last_attempt) >= kRetryIntervalMs;
  }

  void noteAttempt(uint32_t now) {
    if (!_tracking) noteDisconnected(now);
    _last_attempt = now;
  }

  bool disconnectedFor(uint32_t now, uint32_t duration_ms) const {
    return _tracking && elapsedMs(now, _disconnected_since) >= duration_ms;
  }

  bool isTracking() const { return _tracking; }

private:
  bool _tracking;
  uint32_t _disconnected_since;
  uint32_t _last_attempt;
};

} // namespace WiFiReconnectPolicy
