#pragma once

#include <freertos/FreeRTOS.h>
#include <freertos/task.h>

// Optional, same-owner scheduling experiment. This does not create a radio
// task or make the wrapper thread-safe. Pair begin/end in the existing radio
// owner's RX-to-RX retune hooks, including failed and deferred operations.
// The default request of zero preserves the owner's existing priority.
class ESP32RetunePriority {
  UBaseType_t _requested = 0;
  UBaseType_t _saved = 0;
  UBaseType_t _depth = 0;
  TaskHandle_t _owner = nullptr;
  bool _boosted = false;

public:
  ESP32RetunePriority() = default;
  ESP32RetunePriority(const ESP32RetunePriority&) = delete;
  ESP32RetunePriority& operator=(const ESP32RetunePriority&) = delete;

  // A changed request applies to the next outer scope. It never replaces the
  // original priority retained for an operation that is already in progress.
  bool setPriority(UBaseType_t priority) {
    if (priority >= configMAX_PRIORITIES) return false;
    _requested = priority;
    return true;
  }
  UBaseType_t requestedPriority() const { return _requested; }
  bool isActive() const { return _boosted; }

  void begin(bool continuousRx) {
    if (_depth++ != 0) return;
    if (!continuousRx || _requested == 0) return;
    _saved = uxTaskPriorityGet(nullptr);
    if (_saved >= _requested) return;  // Never lower a higher-priority owner.
    _owner = xTaskGetCurrentTaskHandle();
    _boosted = true;
    vTaskPrioritySet(_owner, _requested);
  }

  void end() {
    if (_depth == 0 || --_depth != 0 || !_boosted) return;
    // Clear our state before restoring: lowering priority may immediately
    // schedule another task. Keep the saved owner/priority across that switch.
    const TaskHandle_t owner = _owner;
    const UBaseType_t saved = _saved;
    _owner = nullptr;
    _boosted = false;
    vTaskPrioritySet(owner, saved);
  }
};
