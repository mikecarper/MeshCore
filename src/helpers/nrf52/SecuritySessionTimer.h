#pragma once

#include <stdint.h>

// Keep the link available for the same two-minute window used by the pairing
// PIN screen.  An expired session is disconnected, but its bond is not erased.
static const uint32_t BLE_SECURITY_SESSION_TIMEOUT_MS = 120000UL;

class SecuritySessionTimer {
  bool _pending;
  uint32_t _started_at;

public:
  SecuritySessionTimer() : _pending(false), _started_at(0) {}

  void start(uint32_t now) {
    _pending = true;
    _started_at = now;
  }

  void cancel() { _pending = false; }
  bool pending() const { return _pending; }

  bool expired(uint32_t now,
               uint32_t timeout_ms = BLE_SECURITY_SESSION_TIMEOUT_MS) const {
    return _pending && (uint32_t)(now - _started_at) >= timeout_ms;
  }
};
