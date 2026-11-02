#pragma once

#include <stdint.h>

// Pure, platform-independent timing state for the explicit WiFi reconnect
// fallback. The platform's automatic reconnect remains enabled where
// appropriate; this tracker guarantees that a stuck station is kicked with
// the saved credentials every five minutes while the link remains down.
// A controlled recovery build permits two earlier retries without turning a
// persistent outage into a continuous association loop.
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
#if defined(MESH_WIFI_RECOVERY) && MESH_WIFI_RECOVERY
    _early_retries = 0;
#endif
  }

  // The first observation of a disconnected station starts both timers. This
  // treats the current/initial association as the first attempt and starts
  // the explicit fallback timer.
  void noteDisconnected(uint32_t now) {
    if (_tracking) return;
    _tracking = true;
    _disconnected_since = now;
    _last_attempt = now;
  }

  bool retryDue(uint32_t now) const {
    uint32_t interval = kRetryIntervalMs;
#if defined(MESH_WIFI_RECOVERY) && MESH_WIFI_RECOVERY
    if (_early_retries < 2) interval = 60000UL;
#endif
    return _tracking
        && elapsedMs(now, _disconnected_since) >= interval
        && elapsedMs(now, _last_attempt) >= interval;
  }

  void noteAttempt(uint32_t now) {
#if defined(MESH_WIFI_RECOVERY) && MESH_WIFI_RECOVERY
    // Initial association and early manual joins refresh the quiet window.
    // Only a due fallback consumes one of the two early recovery attempts.
    if (_early_retries < 2 && retryDue(now)) ++_early_retries;
#endif
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
#if defined(MESH_WIFI_RECOVERY) && MESH_WIFI_RECOVERY
  uint8_t _early_retries = 0;
#endif
};

} // namespace WiFiReconnectPolicy
