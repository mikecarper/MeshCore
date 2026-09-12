#pragma once

#include <stdint.h>
#include <stddef.h>
#include <stdio.h>
#include <string.h>

namespace mesh {

// Runtime overrides only. A zero duration selects the saved normal settings.
class RepeaterRadioTiming {
  uint32_t _temp_duration_secs = 0;

public:
  static constexpr bool DEFAULT_RX_WATCHDOG_ENABLED = true;
  static constexpr uint32_t HOUR_SECS = 3600UL;
  static constexpr uint32_t HOUR_MS = HOUR_SECS * 1000UL;

  void setTempDuration(uint32_t seconds) { _temp_duration_secs = seconds; }
  uint32_t tempDuration() const { return _temp_duration_secs; }
  bool isTemporary() const { return _temp_duration_secs != 0; }

  uint32_t watchdogMillis(bool normal_enabled) const {
    if (isTemporary()) {
      return _temp_duration_secs >= 12UL * HOUR_SECS ? 12UL * HOUR_MS : 0;
    }
    return normal_enabled ? 24UL * HOUR_MS : 0;
  }

  const char* watchdogLabel(bool normal_enabled) const {
    if (isTemporary()) {
      return watchdogMillis(normal_enabled) ? "12hours" : "off (temp<12hours)";
    }
    return normal_enabled ? "24hours" : "off";
  }

  uint32_t localAdvertMinutes(uint32_t normal_minutes) const {
    return isTemporary() ? 60 : normal_minutes;
  }

  uint32_t floodAdvertHours(uint32_t normal_hours) const {
    return _temp_duration_secs > 3UL * HOUR_SECS ? 3 : normal_hours;
  }

  static void formatDuration(char* dest, size_t size, uint32_t seconds) {
    const uint32_t days = seconds / (24UL * HOUR_SECS);
    const uint32_t hours = (seconds / HOUR_SECS) % 24;
    const uint32_t minutes = (seconds / 60) % 60;
    if (seconds % 60) {
      snprintf(dest, size, "%lu mins %lu sec (%lud%luh%lum)",
               (unsigned long)(seconds / 60), (unsigned long)(seconds % 60),
               (unsigned long)days, (unsigned long)hours, (unsigned long)minutes);
    } else {
      snprintf(dest, size, "%lu mins (%lud%luh%lum)",
               (unsigned long)(seconds / 60),
               (unsigned long)days, (unsigned long)hours, (unsigned long)minutes);
    }
  }

  static void appendTempWatchdogNote(char* reply, size_t size, uint32_t seconds) {
    const size_t used = strlen(reply);
    if (used >= size) return;
    RepeaterRadioTiming timing;
    timing.setTempDuration(seconds);
    snprintf(reply + used, size - used, "; rx.watchdog=%s", timing.watchdogLabel(true));
  }
};

// A sliding no-RX deadline, rebased on activation and each successful receive.
// No wall clock, periodic flash writes, or extra radio/CPU wakeups are needed.
class RxInactivityWatchdog {
  uint32_t _interval = 0;
  uint32_t _window_start = 0;
  uint32_t _last_rx = 0;

public:
  void reset() { _interval = 0; }

  bool expired(uint32_t now, uint32_t last_rx, uint32_t interval) {
    if (_interval != interval) {
      _interval = interval;
      _window_start = now;
      _last_rx = last_rx;
      return false;
    }
    if (interval == 0) return false;
    if (last_rx != _last_rx) {
      _last_rx = last_rx;
      _window_start = last_rx;
    }
    return (uint32_t)(now - _window_start) >= interval;
  }
};

} // namespace mesh
