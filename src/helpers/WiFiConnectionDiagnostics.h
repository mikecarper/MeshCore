#pragma once

#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <helpers/bridges/MQTTErrorLabels.h>

namespace mesh {
namespace wifi {

enum class ConnectionPhase : uint8_t {
  Idle, Connecting, WaitingIP, Online, Disconnected, TimedOut, Stopped,
  ScanBusy, Blocked, NoSSID, PolicyFailed, APNotOnChannel
};

struct ConnectionDiagnosticsSnapshot {
  char ssid[33] = {};
  uint32_t attempts = 0;
  uint32_t attempt_ms = 0;
  uint32_t phase_ms = 0;
  uint32_t last_disconnect_ms = 0;
  uint8_t last_reason = 0;
  bool has_disconnect = false;
  ConnectionPhase phase = ConnectionPhase::Idle;
};

// Metadata only. The platform adapter owns synchronization; no method calls a
// driver, retains event pointers, or stores a password. Disconnect history is
// scoped to the requested SSID, rather than being mistaken for a new attempt.
class ConnectionDiagnostics {
 public:
  ConnectionDiagnosticsSnapshot snapshot() const { return state_; }

  void begin(const char* ssid, uint32_t now) {
    char requested[33] = {};
    if (ssid) {
      for (size_t i = 0; i < sizeof(requested) - 1 && ssid[i]; ++i) {
        requested[i] = ssid[i];
      }
    }
    if (strcmp(requested, state_.ssid) != 0) {
      state_ = ConnectionDiagnosticsSnapshot{};
      memcpy(state_.ssid, requested, sizeof(requested));
    }
    if (state_.attempts != UINT32_MAX) ++state_.attempts;
    state_.attempt_ms = now;
    state_.phase_ms = now;
    state_.phase = ConnectionPhase::Connecting;
  }

  void phase(ConnectionPhase value, uint32_t now) {
    if (state_.phase == value) return;
    // A caller's join deadline is not a second, more specific failure when
    // policy or a fixed-channel scan already prevented the driver attempt.
    if (value == ConnectionPhase::TimedOut && preflightFailed()) return;
    state_.phase = value;
    state_.phase_ms = now;
  }

  void associated(const uint8_t* ssid, size_t length, uint32_t now) {
    if (matches(ssid, length) && active()) {
      phase(ConnectionPhase::WaitingIP, now);
    }
  }

  // IP events contain no SSID. Require a matching association first so a late
  // event for a previous network cannot mark the new target online.
  void gotIP(uint32_t now) {
    if (state_.phase == ConnectionPhase::WaitingIP
        || state_.phase == ConnectionPhase::Online) {
      phase(ConnectionPhase::Online, now);
    }
  }

  void disconnected(const uint8_t* ssid, size_t length, uint8_t reason,
                    uint32_t now) {
    if (!matches(ssid, length) || reason == MQTTErrorLabels::kWifiAssocLeave) return;
    state_.last_reason = reason;
    state_.last_disconnect_ms = now;
    state_.has_disconnect = true;
    // A delayed callback may still provide useful history, but must not undo
    // an explicit timeout, stop, or rejection before the driver was called.
    if (active()) phase(ConnectionPhase::Disconnected, now);
  }

  void lostIP(uint32_t now) {
    if (state_.phase == ConnectionPhase::Online) {
      phase(ConnectionPhase::WaitingIP, now);
    }
  }

  void stopped(uint32_t now) {
    switch (state_.phase) {
      case ConnectionPhase::TimedOut:
      case ConnectionPhase::ScanBusy:
      case ConnectionPhase::Blocked:
      case ConnectionPhase::NoSSID:
      case ConnectionPhase::PolicyFailed:
      case ConnectionPhase::APNotOnChannel:
        return;
      default:
        phase(ConnectionPhase::Stopped, now);
    }
  }

 private:
  bool preflightFailed() const {
    return state_.phase == ConnectionPhase::ScanBusy
        || state_.phase == ConnectionPhase::Blocked
        || state_.phase == ConnectionPhase::NoSSID
        || state_.phase == ConnectionPhase::PolicyFailed
        || state_.phase == ConnectionPhase::APNotOnChannel;
  }

  bool matches(const uint8_t* ssid, size_t length) const {
    return ssid && length && length <= 32 && state_.ssid[0]
        && strlen(state_.ssid) == length
        && memcmp(state_.ssid, ssid, length) == 0;
  }

  bool active() const {
    return state_.phase == ConnectionPhase::Connecting
        || state_.phase == ConnectionPhase::WaitingIP
        || state_.phase == ConnectionPhase::Online
        || state_.phase == ConnectionPhase::Disconnected;
  }

  ConnectionDiagnosticsSnapshot state_;
};

inline const char* connectionPhaseName(ConnectionPhase phase) {
  switch (phase) {
    case ConnectionPhase::Idle: return "idle";
    case ConnectionPhase::Connecting: return "connecting";
    case ConnectionPhase::WaitingIP: return "associated, awaiting IP";
    case ConnectionPhase::Online: return "connected";
    case ConnectionPhase::Disconnected: return "disconnected";
    case ConnectionPhase::TimedOut: return "connection timed out";
    case ConnectionPhase::Stopped: return "stopped";
    case ConnectionPhase::ScanBusy: return "waiting for WiFi scan";
    case ConnectionPhase::Blocked: return "blocked by radio setting";
    case ConnectionPhase::NoSSID: return "no network configured";
    case ConnectionPhase::PolicyFailed: return "radio policy failed";
    case ConnectionPhase::APNotOnChannel: return "network absent on mesh channel";
    default: return "unknown";
  }
}

// The complete report fits a 157-byte routed reply even with maximum uint32_t
// counters and elapsed times. Small destinations receive a terminated prefix
// and false; no fields are silently omitted to make a report appear complete.
inline bool formatConnectionDiagnostics(
    char* output, size_t capacity, const ConnectionDiagnosticsSnapshot& value,
    uint32_t now) {
  if (!output || !capacity) return false;
  char attempt_age[24];
  if (value.attempts) {
    snprintf(attempt_age, sizeof(attempt_age), "%lus ago",
             static_cast<unsigned long>(uint32_t(now - value.attempt_ms) / 1000));
  } else {
    memcpy(attempt_age, "never", sizeof("never"));
  }
  char last_drop[96];
  if (value.has_disconnect) {
    const char* label = MQTTErrorLabels::wifiReason(value.last_reason);
    snprintf(last_drop, sizeof(last_drop), "driver %u %s; %lus ago",
             static_cast<unsigned>(value.last_reason), label ? label : "unknown reason",
             static_cast<unsigned long>(uint32_t(now - value.last_disconnect_ms) / 1000));
  } else {
    memcpy(last_drop, "no driver reason recorded", sizeof("no driver reason recorded"));
  }
  const int count = snprintf(output, capacity,
      "> WiFi: %s; tries: %lu; last try: %s\nLast drop: %s",
      connectionPhaseName(value.phase), static_cast<unsigned long>(value.attempts),
      attempt_age, last_drop);
  return count >= 0 && static_cast<size_t>(count) < capacity;
}

// Add history only when the entire hint fits. A status containing a setup AP
// address must remain intact even when there is no room for the extra detail.
inline bool appendConnectionFailureHint(
    char* output, size_t capacity, const ConnectionDiagnosticsSnapshot& value,
    uint32_t now) {
  if (!output || !capacity || !value.has_disconnect) return false;
  size_t used = 0;
  while (used < capacity && output[used]) ++used;
  if (used == capacity) return false;
  const char* label = MQTTErrorLabels::wifiReason(value.last_reason);
  if (value.last_reason == MQTTErrorLabels::kWifiAuthFail) label = "auth failed";
  char hint[96];
  const int count = snprintf(hint, sizeof(hint), "; last drop: %u %s; %lus ago",
      static_cast<unsigned>(value.last_reason), label ? label : "unknown reason",
      static_cast<unsigned long>(uint32_t(now - value.last_disconnect_ms) / 1000));
  if (count < 0 || static_cast<size_t>(count) >= sizeof(hint)
      || static_cast<size_t>(count) >= capacity - used) return false;
  memcpy(output + used, hint, static_cast<size_t>(count) + 1);
  return true;
}

} // namespace wifi
} // namespace mesh
