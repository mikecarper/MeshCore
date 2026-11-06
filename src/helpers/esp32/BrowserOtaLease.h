#pragma once

#include <stddef.h>
#include <stdint.h>
#include <string.h>

// Independent authorization policy for a future browser OTA endpoint.
// It does not create an endpoint or relax the physical-USB ota.peer guard.
// All calls must be serialized by the owner's existing state lock.
namespace mesh { namespace ota {

struct BrowserOtaAuthority {
  bool lan = false;
  bool same_origin = false;
  bool explicit_login = false;
  uint32_t credential_generation = 0;
  const uint8_t* session = nullptr;
  size_t session_size = 0;
};

class BrowserOtaLease {
public:
  static constexpr uint32_t kClaimWindowMs = 5UL * 60UL * 1000UL;
  static constexpr uint32_t kMaxDurationMs = 12UL * 60UL * 60UL * 1000UL;
  static constexpr size_t kSessionBytes = 16;

  // Must be called exclusively for an actual, physically attached USB CLI
  // request. Never grant this by impersonating a USB-origin request from WiFi.
  bool arm(bool physical_usb, uint32_t now, uint32_t duration_ms) {
    if (!physical_usb || duration_ms == 0 || duration_ms > kMaxDurationMs)
      return false;
    revoke();
    _armed = true;
    _claim_deadline = now + kClaimWindowMs;
    _duration = duration_ms;
    return true;
  }

  bool claim(uint32_t now, const BrowserOtaAuthority& authority) {
    expire(now);
    if (!_armed || _claimed || !validAuthority(authority)) return false;
    memcpy(_session, authority.session, kSessionBytes);
    _generation = authority.credential_generation;
    _claimed = true;
    _deadline = now + _duration;
    return true;
  }

  bool permits(uint32_t now, const BrowserOtaAuthority& authority) {
    expire(now);
    if (!_claimed || !validAuthority(authority)) return false;
    if (authority.credential_generation != _generation) {
      revoke();
      return false;
    }
    uint8_t difference = 0;
    for (size_t i = 0; i < kSessionBytes; ++i)
      difference |= _session[i] ^ authority.session[i];
    return difference == 0;
  }

  // Call at a serialized main-loop boundary to detect a lease expiry even
  // when the browser has stopped polling. The owner must then detach the OTA
  // source and close its stream; this policy has no side effects on hardware.
  bool claimed(uint32_t now) {
    expire(now);
    return _claimed;
  }

  void revoke() {
    memset(_session, 0, sizeof(_session));
    _generation = _claim_deadline = _deadline = _duration = 0;
    _armed = _claimed = false;
  }

private:
  static bool reached(uint32_t now, uint32_t deadline) {
    return static_cast<int32_t>(now - deadline) >= 0;
  }
  static bool validAuthority(const BrowserOtaAuthority& a) {
    return a.lan && a.same_origin && a.explicit_login && a.session
        && a.session_size == kSessionBytes;
  }
  void expire(uint32_t now) {
    if ((_claimed && reached(now, _deadline)) ||
        (_armed && !_claimed && reached(now, _claim_deadline))) revoke();
  }

  uint8_t _session[kSessionBytes] = {};
  uint32_t _generation = 0, _claim_deadline = 0, _deadline = 0, _duration = 0;
  bool _armed = false, _claimed = false;
};

}} // namespace mesh::ota
