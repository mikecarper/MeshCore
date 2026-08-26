#pragma once

#include <stddef.h>
#include <stdint.h>
#include <stdio.h>

namespace AlertFaultPolicy {

static const uint32_t kCheckIntervalMs = 5000UL;
static const uint16_t kMinIntervalMinutes = 60;
static const uint32_t kMsPerMinute = 60000UL;

enum class State : uint8_t { OK, FIRING };

struct Fault {
  State state;
  uint32_t fired_at_ms;
  uint32_t last_outage_started_ms;
};

struct OutageSnapshot {
  bool down;
  uint32_t started_ms;
  uint8_t reason;
};

enum class Action : uint8_t { None, FireDown, FireRecovered };

struct TickResult {
  Action action;
  uint32_t duration_ms;
};

static inline OutageSnapshot fromStartMs(uint32_t started_ms) {
  OutageSnapshot snapshot{};
  snapshot.down = started_ms != 0;
  snapshot.started_ms = started_ms;
  return snapshot;
}

// Pack the cross-task outage state into one atomic word. The explicit down bit
// means an outage beginning at millis()==0 remains representable.
static const uint64_t kOutageDownBit = 1ULL << 40;

static inline uint64_t packOutageSnapshot(OutageSnapshot snapshot) {
  if (!snapshot.down) {
    snapshot.started_ms = 0;
    snapshot.reason = 0;
  }
  uint64_t value = (uint64_t)snapshot.started_ms;
  value |= (uint64_t)snapshot.reason << 32;
  if (snapshot.down) value |= kOutageDownBit;
  return value;
}

static inline OutageSnapshot unpackOutageSnapshot(uint64_t value) {
  OutageSnapshot snapshot{};
  snapshot.started_ms = (uint32_t)value;
  snapshot.reason = (uint8_t)(value >> 32);
  snapshot.down = (value & kOutageDownBit) != 0;
  if (!snapshot.down) {
    snapshot.started_ms = 0;
    snapshot.reason = 0;
  }
  return snapshot;
}

static inline OutageSnapshot applyWifiStatus(uint32_t now, bool connected,
                                             OutageSnapshot current,
                                             bool initialized) {
  if (connected) {
    if (!initialized || current.down) return OutageSnapshot{};
    return current;
  }
  if (!initialized || !current.down) {
    OutageSnapshot snapshot{};
    snapshot.down = true;
    snapshot.started_ms = now;
    snapshot.reason = current.reason;
    return snapshot;
  }
  return current;
}

static inline OutageSnapshot applyWifiGotIp(OutageSnapshot) {
  return OutageSnapshot{};
}

// Preserve the first outage start and first useful reason. Reconnect attempts
// may produce later local disconnect events which must not rewrite either.
static inline OutageSnapshot applyWifiDisconnectEvent(
    uint32_t now, uint8_t reason, OutageSnapshot current) {
  if (!current.down) {
    OutageSnapshot snapshot{};
    snapshot.down = true;
    snapshot.started_ms = now;
    snapshot.reason = reason;
    return snapshot;
  }
  if (current.reason == 0 && reason != 0) current.reason = reason;
  return current;
}

static inline uint32_t elapsedMs(uint32_t now, uint32_t then) {
  return now - then;
}

static inline bool checkDue(uint32_t now, uint32_t next_check_ms) {
  return (int32_t)(now - next_check_ms) >= 0;
}

static inline uint32_t nextCheckMs(uint32_t now) {
  return now + kCheckIntervalMs;
}

static inline uint32_t minIntervalMs(uint16_t configured_minutes) {
  uint16_t minutes = configured_minutes < kMinIntervalMinutes
                         ? kMinIntervalMinutes
                         : configured_minutes;
  return (uint32_t)minutes * kMsPerMinute;
}

static inline uint32_t thresholdMs(uint16_t minutes) {
  return (uint32_t)minutes * kMsPerMinute;
}

static inline uint32_t downDurationMs(uint32_t now,
                                     const OutageSnapshot& snapshot) {
  return snapshot.down ? elapsedMs(now, snapshot.started_ms) : 0;
}

static inline bool rateLimitAllows(uint32_t now, uint32_t fired_at_ms,
                                   uint32_t min_interval_ms) {
  return fired_at_ms == 0 ||
         elapsedMs(now, fired_at_ms) >= min_interval_ms;
}

static inline TickResult tick(const Fault& fault, uint32_t now,
                              const OutageSnapshot& snapshot,
                              uint32_t threshold_ms,
                              uint32_t min_interval_ms) {
  TickResult result = {Action::None, 0};
  if (fault.state == State::OK) {
    const uint32_t down_ms = downDurationMs(now, snapshot);
    if (snapshot.down && down_ms >= threshold_ms &&
        rateLimitAllows(now, fault.fired_at_ms, min_interval_ms)) {
      result.action = Action::FireDown;
      result.duration_ms = down_ms;
    }
  } else if (!snapshot.down) {
    result.action = Action::FireRecovered;
    result.duration_ms = elapsedMs(now, fault.last_outage_started_ms);
  }
  return result;
}

static inline void commitDown(Fault& fault, uint32_t now,
                              uint32_t outage_start_ms) {
  fault.state = State::FIRING;
  fault.fired_at_ms = now;
  fault.last_outage_started_ms = outage_start_ms;
}

static inline void commitRecovered(Fault& fault) {
  fault.state = State::OK;
}

static inline void reset(Fault& fault) {
  fault.state = State::OK;
  fault.fired_at_ms = 0;
}

static inline void rearmIfDisabled(Fault& fault) {
  if (fault.state == State::FIRING) fault.state = State::OK;
}

static inline void formatAge(uint32_t age_ms, char* out, size_t out_size) {
  if (!out || out_size == 0) return;
  uint32_t seconds = age_ms / 1000U;
  uint32_t hours = seconds / 3600U;
  uint32_t minutes = (seconds % 3600U) / 60U;
  if (hours > 0) {
    snprintf(out, out_size, "%uh%um", (unsigned)hours, (unsigned)minutes);
  } else {
    snprintf(out, out_size, "%um", (unsigned)minutes);
  }
}

static inline bool formatWifiAlert(char* out, size_t out_size,
                                   const TickResult& result,
                                   const OutageSnapshot& snapshot) {
  if (!out || out_size == 0) return false;
  char age[16];
  formatAge(result.duration_ms, age, sizeof(age));
  if (result.action == Action::FireDown) {
    if (snapshot.reason != 0) {
      snprintf(out, out_size, "WiFi down %s (reason %u)", age,
               (unsigned)snapshot.reason);
    } else {
      snprintf(out, out_size, "WiFi down %s", age);
    }
    return true;
  }
  if (result.action == Action::FireRecovered) {
    snprintf(out, out_size, "WiFi recovered after %s", age);
    return true;
  }
  return false;
}

static inline void formatMqttDown(char* out, size_t out_size, int slot_1based,
                                  const char* preset_name,
                                  uint32_t duration_ms) {
  if (!out || out_size == 0) return;
  char age[16];
  formatAge(duration_ms, age, sizeof(age));
  snprintf(out, out_size, "MQTT slot %d (%s) down %s", slot_1based,
           preset_name ? preset_name : "?", age);
}

static inline void formatMqttRecovered(char* out, size_t out_size,
                                       int slot_1based,
                                       const char* preset_name,
                                       uint32_t duration_ms) {
  if (!out || out_size == 0) return;
  char age[16];
  formatAge(duration_ms, age, sizeof(age));
  snprintf(out, out_size, "MQTT slot %d (%s) recovered after %s", slot_1based,
           preset_name ? preset_name : "?", age);
}

}  // namespace AlertFaultPolicy
