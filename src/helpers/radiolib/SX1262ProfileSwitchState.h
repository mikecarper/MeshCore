#pragma once
#include <stdint.h>

// A short-lived capability for reusing a fully configured continuous-RX
// context. Standby outside the owned hop, staging, sleep and reset revoke it.
struct SX1262ProfileSwitchState {
  bool enabled;
  bool rxValid = false;
  bool open = false;
  bool active = false;
  bool standbyReady = false;
  int16_t error = 0;

  explicit SX1262ProfileSwitchState(bool enable) : enabled(enable) {}
  void invalidate() { rxValid = active = standbyReady = false; }
  void begin(bool continuousRx, bool warm) {
    open = true;
    error = 0;
    standbyReady = false;
    active = enabled && rxValid && continuousRx && warm;
  }
  void standbyResult(int16_t result) {
    if (result != 0) {
      if (open) error = result;
      invalidate();
    } else if (active) {
      standbyReady = true;
    }
  }
  bool canResumeFast() const { return open && active && rxValid && standbyReady && error == 0; }
  bool failed() const { return open && error != 0; }
  void rxResult(int16_t result, bool reusable) {
    if (result != 0) {
      if (open) error = result;
      invalidate();
    } else {
      rxValid = enabled && reusable;
      standbyReady = false;
    }
  }
  void end(bool success) {
    if (!success) invalidate();
    open = active = standbyReady = false;
    error = 0;
  }
};
