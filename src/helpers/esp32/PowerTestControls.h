#pragma once

#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include "CpuClockPolicy.h"

namespace mesh {
namespace esp32 {

// Local experiments only. Nothing here is persisted; reboot restores the
// production policy. RF parameters and calibration settling are unchanged.
struct PowerTestControls {
  bool sleep_enabled = true;
  uint8_t slice_ms = 10;
  uint8_t guard_ms = 4;
  uint8_t minimum_ms = 2;
  uint8_t settle_ms = 0;  // zero selects the radio family's normal idle settle
};

inline PowerTestControls& powerTestControls() {
  static PowerTestControls value;
  return value;
}

enum class UartQuietAction : uint8_t { Status, Schedule, Cancel };
using UartQuietHandler = bool (*)(void*, UartQuietAction, uint16_t, char*, size_t);
struct UartQuietControl {
  void* owner = nullptr;
  UartQuietHandler handle = nullptr;
};
inline UartQuietControl& uartQuietControl() {
  static UartQuietControl value;
  return value;
}
inline void registerUartQuietControl(void* owner, UartQuietHandler handle) {
  uartQuietControl().owner = owner;
  uartQuietControl().handle = handle;
}

// The physical UART and its role's protocol session must stop together. A
// failed Park callback must leave that session intact so the console remains
// usable. Infrastructure roles without a framed session need no callback.
enum class UartQuietSessionAction : uint8_t { Park, Resume };
using UartQuietSessionHandler = bool (*)(void*, UartQuietSessionAction);
struct UartQuietSessionControl {
  void* owner = nullptr;
  UartQuietSessionHandler handle = nullptr;
};
inline UartQuietSessionControl& uartQuietSessionControl() {
  static UartQuietSessionControl value;
  return value;
}
inline void registerUartQuietSessionControl(void* owner, UartQuietSessionHandler handle) {
  uartQuietSessionControl().owner = owner;
  uartQuietSessionControl().handle = handle;
}
inline bool transitionUartQuietSession(UartQuietSessionAction action) {
  const auto& control = uartQuietSessionControl();
  return !control.handle || control.handle(control.owner, action);
}

inline bool manualCpuSleepEnabled() { return powerTestControls().sleep_enabled; }
inline uint32_t profileSleepSliceMillis() { return powerTestControls().slice_ms; }
#if defined(MESH_W12_PRAM_EXPERIMENT) && MESH_W12_PRAM_EXPERIMENT
// The on-chip scanner needs only its periodic health poll; host-managed
// profile switching must continue to use the short slice above.
inline constexpr uint32_t autonomousProfileSleepSliceMillis() { return 1000; }
#endif
inline uint32_t profileSleepGuardMicros() { return uint32_t(powerTestControls().guard_ms) * 1000; }
inline uint32_t profileSleepMinimumMicros() { return uint32_t(powerTestControls().minimum_ms) * 1000; }
inline uint32_t profileSleepSettleMillis(uint32_t normal) {
  return powerTestControls().settle_ms ? powerTestControls().settle_ms : normal;
}

// Infrastructure has no Bluetooth modem policy, but must honor a saved CPU
// choice at boot and after preference changes without reconfiguring each loop.
template <typename Board>
bool serviceCpuFrequencyPolicy(Board& board, uint16_t selected, bool powersaving,
                               uint32_t now_ms, bool force = false, bool profiles_enabled = false) {
  struct State {
    uint16_t selected = UINT16_MAX;
    bool powersaving = false;
    bool profiles_enabled = false;
    uint32_t retry_at = 0;
  };
  static State state;
  profiles_enabled = cpuclock::profilePerformanceEnabled(profiles_enabled);
  board.setRadioProfileCpuClockPolicy(profiles_enabled);
  if (!force && state.selected == selected && state.powersaving == powersaving
      && state.profiles_enabled == profiles_enabled) return true;
  if (!force && state.retry_at && static_cast<int32_t>(now_ms - state.retry_at) < 0) return false;
  if (!board.applyCpuFrequencyMHz(selected, powersaving)) {
    state.retry_at = now_ms + 5000;
    if (!state.retry_at) state.retry_at = 1;
    return false;
  }
  state.selected = selected;
  state.powersaving = powersaving;
  state.profiles_enabled = profiles_enabled;
  state.retry_at = 0;
  return true;
}

inline bool powerTestNumber(const char* text, uint16_t minimum, uint16_t maximum, uint16_t& output) {
  if (!text || !*text) return false;
  uint32_t value = 0;
  for (const char* p = text; *p; ++p) {
    if (*p < '0' || *p > '9') return false;
    value = value * 10 + unsigned(*p - '0');
    if (value > maximum) return false;
  }
  if (value < minimum) return false;
  output = uint16_t(value);
  return true;
}

inline void formatPowerTestControls(char* reply, size_t capacity) {
  const auto& value = powerTestControls();
  char settle[8], uart[48] = "uart=unsupported";
  if (value.settle_ms) snprintf(settle, sizeof(settle), "%u", unsigned(value.settle_ms));
  else strcpy(settle, "auto");
  const auto& quiet = uartQuietControl();
  if (quiet.handle) quiet.handle(quiet.owner, UartQuietAction::Status, 0, uart, sizeof(uart));
  snprintf(reply, capacity, "sleep=%s slice_ms=%u guard_ms=%u min_ms=%u settle_ms=%s %s; RAM only",
      value.sleep_enabled ? "on" : "off", unsigned(value.slice_ms), unsigned(value.guard_ms),
      unsigned(value.minimum_ms), settle, uart);
}

inline bool handlePowerTestCommand(const char* command, char* reply, size_t capacity, bool local) {
  const bool get = strcmp(command, "get powersaving.test") == 0;
  const char* arguments = strncmp(command, "set powersaving.test ", 21) == 0 ? command + 21 : nullptr;
  if (!get && !arguments && strcmp(command, "set powersaving.test") != 0) return false;
  if (!local) {
    snprintf(reply, capacity, "Error: powersaving.test is local only");
    return true;
  }
  if (get) { formatPowerTestControls(reply, capacity); return true; }
  auto& value = powerTestControls();
  if (arguments && strcmp(arguments, "reset") == 0) {
    const auto& quiet = uartQuietControl();
    if (quiet.handle && !quiet.handle(quiet.owner, UartQuietAction::Cancel, 0, reply, capacity)) return true;
    value = PowerTestControls();
  } else if (arguments && (strcmp(arguments, "sleep on") == 0 || strcmp(arguments, "sleep off") == 0)) {
    value.sleep_enabled = strcmp(arguments, "sleep on") == 0;
  } else if (arguments && strncmp(arguments, "uart_quiet ", 11) == 0) {
    const auto& quiet = uartQuietControl();
    uint16_t seconds = 0;
    const bool cancel = strcmp(arguments + 11, "off") == 0;
    if (!cancel && !powerTestNumber(arguments + 11, 5, 300, seconds)) {
      snprintf(reply, capacity, "Error: uart_quiet requires 5..300 seconds or off"); return true;
    }
    if (!quiet.handle) snprintf(reply, capacity, "Error: UART quiet is unsupported");
    else quiet.handle(quiet.owner, cancel ? UartQuietAction::Cancel : UartQuietAction::Schedule,
                      seconds, reply, capacity);
    return true;
  } else {
    uint16_t number = 0;
    if (arguments && strncmp(arguments, "slice_ms ", 9) == 0
        && powerTestNumber(arguments + 9, 2, 20, number)) value.slice_ms = uint8_t(number);
    else if (arguments && strncmp(arguments, "guard_ms ", 9) == 0
        && powerTestNumber(arguments + 9, 0, 20, number)) value.guard_ms = uint8_t(number);
    else if (arguments && strncmp(arguments, "min_ms ", 7) == 0
        && powerTestNumber(arguments + 7, 1, 10, number)) value.minimum_ms = uint8_t(number);
    else if (arguments && strcmp(arguments, "settle_ms auto") == 0) value.settle_ms = 0;
    else if (arguments && strncmp(arguments, "settle_ms ", 10) == 0
        && powerTestNumber(arguments + 10, 1, 50, number)) value.settle_ms = uint8_t(number);
    else {
      snprintf(reply, capacity, "Error: use sleep on|off, slice_ms 2..20, guard_ms 0..20, min_ms 1..10, settle_ms auto|1..50, uart_quiet 5..300|off, reset");
      return true;
    }
  }
  formatPowerTestControls(reply, capacity);
  return true;
}

}  // namespace esp32
}  // namespace mesh
