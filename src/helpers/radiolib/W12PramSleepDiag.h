#pragma once

#include <stdint.h>
#include <stdio.h>

namespace mesh { namespace w12pram {

enum class WakeKind : uint8_t { Gpio, Timer, Other };

// RAM-only laboratory diagnostics. Failed SDK calls are not wakeups.
struct SleepDiag {
  uint32_t gpio = 0;
  uint32_t timer = 0;
  uint32_t other = 0;
  uint32_t gpio_radio_high = 0;

  static void increment(uint32_t& value) {
    if (value != UINT32_MAX) ++value;
  }

  void record(bool success, WakeKind kind, bool radio_high) {
    if (!success) return;
    if (kind == WakeKind::Gpio) {
      increment(gpio);
      if (radio_high) increment(gpio_radio_high);
    } else if (kind == WakeKind::Timer) {
      increment(timer);
    } else {
      increment(other);
    }
  }

  void format(char* reply, size_t size) const {
    snprintf(reply, size, "> gpio=%lu timer=%lu other=%lu radio_high=%lu (RAM only)",
             (unsigned long)gpio, (unsigned long)timer, (unsigned long)other,
             (unsigned long)gpio_radio_high);
  }
};

inline SleepDiag& sleepDiag() { static SleepDiag diag; return diag; }

} } // namespace mesh::w12pram
