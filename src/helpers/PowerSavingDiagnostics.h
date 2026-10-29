#pragma once

#include "CLIReplyText.h"

#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

namespace mesh {
namespace powerdiag {

// Observe the actual sleep decision, never a second approximation of it.
// All calls belong to the application/CLI task, not an ISR or worker task.
enum class Reason : uint8_t {
  PolicyOff, Startup, UsbSession, UsbLogging, UsbRecovery, Wireless, Button,
  Morse, DualRadio, Ota, Calibration, RadioWatchdog, Queue, Retry, Scheduled,
  SerialIO, Bridge, BridgeCleanup, BridgeWake, Network, Mesh, Tracker, Board,
  NoWake, Count
};
using Mask = uint32_t;
constexpr size_t ReasonCount = static_cast<size_t>(Reason::Count);
static_assert(ReasonCount < 32, "Power-saving reasons must fit one mask");
constexpr Mask mask(Reason reason) {
  return static_cast<size_t>(reason) < ReasonCount
      ? Mask(1) << static_cast<uint8_t>(reason) : 0;
}
constexpr Mask ValidMask = (Mask(1) << ReasonCount) - 1;
// This identifies a short wait call, not proof that the CPU did not sleep.
constexpr uint32_t ShortReturnMicros = 100;
#if defined(ESP32_PLATFORM)
constexpr size_t SdkBucketCount = 16;
constexpr uint32_t SdkBucketMillis = 16000;
enum SdkBucketFlags : uint8_t {
  SdkBucketOutside = 1, SdkBucketSaturated = 2, SdkBucketMissingClock = 4
};
#endif

inline const char* reasonName(Reason reason) {
  static const char* const names[] = {
    "policy.off", "startup", "usb.session", "usb.logging", "usb.recovery",
    "wireless", "button", "morse", "radio2", "ota", "calibration",
    "radio.watchdog", "queue", "retry", "scheduled", "serial", "bridge",
    "bridge.cleanup", "bridge.wake", "network", "mesh", "tracker", "board",
    "no.wake"
  };
  return static_cast<size_t>(reason) < ReasonCount
      ? names[static_cast<size_t>(reason)] : "unknown";
}

struct Snapshot {
  uint32_t blocked_ms[ReasonCount] = {};
  uint16_t transitions[ReasonCount] = {};
  Mask current = 0;
  Mask seen = 0;
  uint32_t union_blocked_ms = 0;
  uint32_t eligible_ms = 0;
  uint32_t last_ms = 0;
  uint32_t decision_at_ms = 0;
  uint32_t calls = 0;
  uint32_t returns = 0;
  uint32_t short_returns = 0;
  uint32_t board_vetoes = 0;
  uint32_t errors = 0;
  uint32_t call_us = 0;
  uint32_t max_call_us = 0;
  uint32_t last_call_us = 0;
  uint32_t call_begin_us = 0;
  int32_t detail = 0;
#if defined(ESP32_PLATFORM)
  // SDK entry counts over time, not physical sleep residency. Keep timestamps
  // in the millis domain so micros wrap cannot move an entry into another bin.
  uint32_t bucket_start_ms = 0;
  uint32_t bucket_outside_calls = 0;
  uint16_t sdk_call_bins[SdkBucketCount] = {};
  uint8_t bucket_flags = 0;
#endif
  bool enabled = false;
  bool observed = false;
  bool pending = false;
};
static_assert(sizeof(Snapshot) <= 256, "Power diagnostics exceeded its RAM budget");

inline Snapshot& state() {
  static Snapshot value;
  return value;
}
inline bool enabled() { return state().enabled; }

inline void addSaturating(uint32_t& value, uint32_t amount) {
  value = amount > UINT32_MAX - value ? UINT32_MAX : value + amount;
}
inline void increment(uint32_t& value) { addSaturating(value, 1); }

inline void account(Snapshot& value, uint32_t now_ms) {
  if (value.enabled && value.observed) {
    const uint32_t elapsed = now_ms - value.last_ms;
    if (value.current != 0) {
      addSaturating(value.union_blocked_ms, elapsed);
      for (size_t i = 0; i < ReasonCount; ++i) {
        if (value.current & (Mask(1) << i))
          addSaturating(value.blocked_ms[i], elapsed);
      }
    } else {
      addSaturating(value.eligible_ms, elapsed);
    }
  }
  value.last_ms = now_ms;
}

// A fresh on starts a new observation window. Off freezes it, so reconnecting
// USB to inspect a battery test does not overwrite the recorded blockers.
inline void setEnabled(bool on, uint32_t now_ms) {
  Snapshot& value = state();
  if (on == value.enabled) return;
  if (on) {
    value = Snapshot();
    value.enabled = true;
    value.last_ms = now_ms;
#if defined(ESP32_PLATFORM)
    value.bucket_start_ms = now_ms;
#endif
  } else {
    account(value, now_ms);
    value.enabled = false;
    value.pending = false;
  }
}
inline void reset(uint32_t now_ms) {
  Snapshot& value = state();
  const bool was_enabled = value.enabled;
  value = Snapshot();
  value.enabled = was_enabled;
  value.last_ms = now_ms;
#if defined(ESP32_PLATFORM)
  value.bucket_start_ms = now_ms;
#endif
}

inline void decision(Mask reasons, uint32_t now_ms) {
  Snapshot& value = state();
  if (!value.enabled) return;
  account(value, now_ms);
  reasons &= ValidMask;
  const Mask started = reasons & ~(value.observed ? value.current : 0);
  for (size_t i = 0; i < ReasonCount; ++i) {
    if ((started & (Mask(1) << i)) && value.transitions[i] != UINT16_MAX)
      ++value.transitions[i];
  }
  value.current = reasons;
  value.seen |= reasons;
  value.decision_at_ms = now_ms;
  value.observed = true;
}

// Instrument the actual SDK/event wait, not an outer board.sleep() which can
// decline the request or yield instead. Durations include ISR/SDK work and are
// explicitly not physical sleep residency or a current measurement.
inline void sleepBeginCounters(Snapshot& value, uint32_t now_us) {
  increment(value.calls);
  value.call_begin_us = now_us;
  value.pending = true;
}
inline void sleepBegin(uint32_t now_us) {
  Snapshot& value = state();
  if (!value.enabled) return;
  sleepBeginCounters(value, now_us);
#if defined(ESP32_PLATFORM)
  // Preserve old callers' counters while making an omitted millis clock
  // explicit. Unknown timestamps must never become invented overlap proof.
  value.bucket_flags |= SdkBucketMissingClock;
#endif
}
inline void sleepBegin(uint32_t now_us, uint32_t now_ms) {
#if defined(ESP32_PLATFORM)
  Snapshot& value = state();
  if (!value.enabled) return;
  if (value.calls == UINT32_MAX) value.bucket_flags |= SdkBucketSaturated;
  sleepBeginCounters(value, now_us);
  const uint32_t elapsed = now_ms - value.bucket_start_ms;
  const uint32_t index = elapsed / SdkBucketMillis;
  if (index >= SdkBucketCount) {
    value.bucket_flags |= SdkBucketOutside;
    if (value.bucket_outside_calls == UINT32_MAX)
      value.bucket_flags |= SdkBucketSaturated;
    increment(value.bucket_outside_calls);
  } else if (value.sdk_call_bins[index] == UINT16_MAX) {
    value.bucket_flags |= SdkBucketSaturated;
  } else {
    ++value.sdk_call_bins[index];
  }
#else
  (void)now_ms;
  sleepBegin(now_us);
#endif
}
inline void sleepEnd(uint32_t now_us) {
  Snapshot& value = state();
  if (!value.enabled || !value.pending) return;
  value.pending = false;
  const uint32_t duration = now_us - value.call_begin_us;
  increment(value.returns);
  if (duration < ShortReturnMicros) increment(value.short_returns);
  addSaturating(value.call_us, duration);
  value.last_call_us = duration;
  if (duration > value.max_call_us) value.max_call_us = duration;
}
inline void boardVeto(Reason reason, uint32_t now_ms, int32_t detail = 0) {
  if (!enabled()) return;
  increment(state().board_vetoes);
  state().pending = false;
  state().detail = detail;
  decision(state().current | mask(reason), now_ms);
}
inline void boardError(int32_t error, uint32_t now_ms) {
  if (!enabled()) return;
  increment(state().errors);
  state().pending = false;
  state().detail = error;
  decision(state().current | mask(Reason::Board), now_ms);
}
inline Snapshot snapshot(uint32_t now_ms) {
  Snapshot copy = state();
  account(copy, now_ms);
  return copy;
}

inline bool whitespace(char c) { return c == ' ' || c == '\t'; }
inline const char* suffix(const char* command, const char* prefix) {
  const size_t length = strlen(prefix);
  if (strncmp(command, prefix, length) != 0
      || (command[length] && !whitespace(command[length]))) return nullptr;
  const char* tail = command + length;
  while (whitespace(*tail)) ++tail;
  return tail;
}
inline bool argument(const char* text, const char* expected) {
  const size_t length = strlen(expected);
  if (strncmp(text, expected, length)) return false;
  text += length;
  while (whitespace(*text)) ++text;
  return *text == 0;
}
inline Reason firstReason(Mask reasons) {
  for (size_t i = 0; i < ReasonCount; ++i)
    if (reasons & (Mask(1) << i)) return static_cast<Reason>(i);
  return Reason::Count;
}
inline void formatAge(const Snapshot& value, uint32_t now_ms, char (&age)[12]) {
  if (value.observed)
    snprintf(age, sizeof(age), "%lu", (unsigned long)(now_ms - value.decision_at_ms));
  else strcpy(age, "unknown");
}

inline void formatPage(unsigned page, char* reply, size_t capacity,
                       uint32_t now_ms) {
  const Snapshot value = snapshot(now_ms);
  if (page == 0) {
    char age[12];
    formatAge(value, now_ms, age);
    const char* why = value.observed
        ? (value.current ? reasonName(firstReason(value.current)) : "eligible") : "unobserved";
    snprintf(reply, capacity,
        "> diag=%s age_ms=%s why=%s mask=%06lx seen=%06lx blocked_ms=%lu eligible_ms=%lu",
        value.enabled ? "on" : "off", age, why, (unsigned long)value.current,
        (unsigned long)value.seen, (unsigned long)value.union_blocked_ms,
        (unsigned long)value.eligible_ms);
  } else if (page == 1) {
    snprintf(reply, capacity,
        "> calls=%lu returns=%lu short=%lu veto=%lu errors=%lu pending=%u",
        (unsigned long)value.calls, (unsigned long)value.returns,
        (unsigned long)value.short_returns, (unsigned long)value.board_vetoes,
        (unsigned long)value.errors, (unsigned)value.pending);
  } else if (page == 2) {
    snprintf(reply, capacity,
        "> call_us=%lu max_us=%lu last_us=%lu detail=%ld short_lt_us=%lu; NOT sleep residency",
        (unsigned long)value.call_us, (unsigned long)value.max_call_us,
        (unsigned long)value.last_call_us, (long)value.detail,
        (unsigned long)ShortReturnMicros);
#if defined(ESP32_PLATFORM)
  } else if (page == 9 || page == 10) {
    const size_t first = page == 9 ? 0 : 8;
    const int initial = snprintf(reply, capacity,
        "> bucket_ms=%lu start_ms=%lu now_ms=%lu outside=%lu flags=%u bins%u=",
        (unsigned long)SdkBucketMillis, (unsigned long)value.bucket_start_ms,
        (unsigned long)now_ms, (unsigned long)value.bucket_outside_calls,
        (unsigned)value.bucket_flags, (unsigned)first);
    if (initial < 0 || static_cast<size_t>(initial) >= capacity) return;
    size_t used = static_cast<size_t>(initial);
    for (size_t i = first; i < first + 8; ++i) {
      const int count = snprintf(reply + used, capacity - used, "%s%u",
          i == first ? "" : ",", (unsigned)value.sdk_call_bins[i]);
      if (count < 0 || static_cast<size_t>(count) >= capacity - used) return;
      used += static_cast<size_t>(count);
    }
#endif
  } else {
    size_t used = 0;
    const int initial = snprintf(reply, capacity, "> transitions/blocked_ms:");
    if (initial < 0 || static_cast<size_t>(initial) >= capacity) return;
    used = static_cast<size_t>(initial);
    for (size_t i = (page - 3) * 4; i < (page - 3) * 4 + 4 && i < ReasonCount; ++i) {
      const int count = snprintf(reply + used, capacity - used, " %s=%u/%lu",
          reasonName(static_cast<Reason>(i)), (unsigned)value.transitions[i],
          (unsigned long)value.blocked_ms[i]);
      if (count < 0 || static_cast<size_t>(count) >= capacity - used) return;
      used += static_cast<size_t>(count);
    }
  }
}

inline bool handleCommand(const char* command, char* reply, size_t capacity,
                          uint32_t now_ms) {
  if (!command || !reply || capacity == 0) return false;
  const char* tail = suffix(command, "set powersaving.diag");
  if (tail) {
    if (argument(tail, "on") || argument(tail, "off")) {
      const bool on = argument(tail, "on");
      setEnabled(on, now_ms);
      snprintf(reply, capacity, MESH_CLI_REPLY_TEXT("OK - power diagnostics %s (RAM only; sleep policy unchanged)", "OK - power diag %s (RAM only; sleep policy unchanged)"),
          on ? "on" : "off");
    } else snprintf(reply, capacity, MESH_CLI_REPLY_TEXT("Error: use set powersaving.diag on|off", "Error: set powersaving.diag on|off"));
    return true;
  }
  tail = suffix(command, "clear powersaving.diag");
  if (tail) {
    if (!*tail) {
      reset(now_ms);
      snprintf(reply, capacity, MESH_CLI_REPLY_TEXT("OK - power diagnostics cleared; sleep policy unchanged", "OK - power diag cleared; sleep policy unchanged"));
    } else snprintf(reply, capacity, MESH_CLI_REPLY_TEXT("Error: use clear powersaving.diag", "Error: clear powersaving.diag"));
    return true;
  }
  tail = suffix(command, "get powersaving.diag");
  if (!tail) return false;
  unsigned page = 0;
  if (!*tail || argument(tail, "state")) page = 0;
  else if (argument(tail, "calls")) page = 1;
  else if (argument(tail, "time")) page = 2;
  else if (*tail >= '0' && *tail <= '8' && argument(tail + 1, "")) page = *tail - '0';
#if defined(ESP32_PLATFORM)
  else if (argument(tail, "9")) page = 9;
  else if (argument(tail, "10")) page = 10;
#endif
  else {
    for (size_t i = 0; i < ReasonCount; ++i) {
      const Reason reason = static_cast<Reason>(i);
      if (argument(tail, reasonName(reason))) {
        const Snapshot value = snapshot(now_ms);
        char age[12];
        formatAge(value, now_ms, age);
        snprintf(reply, capacity, "> %s active=%u seen=%u transitions=%u blocked_ms=%lu age_ms=%s",
            reasonName(reason), (unsigned)((value.current & mask(reason)) != 0),
            (unsigned)((value.seen & mask(reason)) != 0), (unsigned)value.transitions[i],
            (unsigned long)value.blocked_ms[i], age);
        return true;
      }
    }
#if defined(ESP32_PLATFORM)
    snprintf(reply, capacity, "Error: get powersaving.diag [0-10|state|calls|time|reason]; reason pages 3-8; SDK buckets 9-10");
#else
    snprintf(reply, capacity, "Error: get powersaving.diag [0-8|state|calls|time|reason]; reason pages 3-8");
#endif
    return true;
  }
  formatPage(page, reply, capacity, now_ms);
  return true;
}

} // namespace powerdiag
} // namespace mesh
