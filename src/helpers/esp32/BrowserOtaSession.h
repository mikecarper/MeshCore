#pragma once

#include <stddef.h>
#include <stdint.h>
#include "BrowserOtaLease.h"
#include "BrowserOtaStream.h"

// Unwired session coordinator. The owner must call these methods only after
// explicitly authenticating WebConfig and verifying the exact browser origin.
// HTTP handlers must marshal OTA source attach/detach and radio commands onto
// the mesh loop; none of those operations may run in an async HTTP callback.
// Two distinct locks are required: state_lock is always acquired first, then
// the stream's lock. Source transactions acquire only the stream lock.
namespace mesh { namespace ota {

class BrowserOtaSession {
public:
  BrowserOtaSession(BrowserOtaLock& state_lock, BrowserOtaStream& stream)
      : _state_lock(state_lock), _stream(stream) {}

  // Call from an actual local USB CLI command, never from a network command.
  // An active stream must first be detached from OtaContext by its owner.
  bool armFromUsb(bool physical_usb, uint32_t now, uint32_t duration_ms) {
    BrowserOtaGuard guard(_state_lock);
    if (_epoch || !physical_usb) return false;
    return _lease.arm(true, now, duration_ms);
  }

  uint32_t claim(uint32_t now, const BrowserOtaAuthority& authority) {
    BrowserOtaGuard guard(_state_lock);
    if (_epoch || !_lease.claim(now, authority)) return 0;
    const uint32_t epoch = _stream.open();
    if (!epoch) { _lease.revoke(); return 0; }
    _epoch = epoch;
    return epoch;
  }

  bool peerAuthorized(uint32_t now, const BrowserOtaAuthority& authority,
                      uint32_t epoch) {
    BrowserOtaGuard guard(_state_lock);
    return valid(now, authority, epoch);
  }

  bool request(uint32_t now, const BrowserOtaAuthority& authority, uint32_t epoch,
               uint32_t& sequence, uint8_t* output, size_t capacity, size_t& length) {
    BrowserOtaGuard guard(_state_lock);
    if (!valid(now, authority, epoch)) { length = 0; return false; }
    return _stream.browserRequest(epoch, sequence, output, capacity, length);
  }

  bool response(uint32_t now, const BrowserOtaAuthority& authority, uint32_t epoch,
                uint32_t sequence, const uint8_t* data, size_t length) {
    BrowserOtaGuard guard(_state_lock);
    return valid(now, authority, epoch) && _stream.browserResponse(epoch, sequence, data, length);
  }

  // The mesh loop uses this to schedule detach after explicit revocation or
  // expiry. Its owner must also revoke on logout, password change and a lost
  // browser heartbeat; this helper cannot itself detect those events.
  bool requiresDetach(uint32_t now) {
    BrowserOtaGuard guard(_state_lock);
    return _epoch && !_lease.claimed(now);
  }

  // For explicit local disable, web logout, password change, or WebConfig
  // shutdown. Must be followed by a mesh-loop source detach.
  void revokeFromOwner() {
    BrowserOtaGuard guard(_state_lock);
    _lease.revoke();
  }

  // Only after the mesh loop has detached the source from OtaContext. A stale
  // disconnect cannot end a later session. A fresh USB arm is then required.
  bool closedAfterDetach(uint32_t epoch) {
    BrowserOtaGuard guard(_state_lock);
    if (!_epoch || epoch != _epoch) return false;
    _stream.close(epoch);
    _epoch = 0;
    _lease.revoke();
    return true;
  }

  // Authenticated browser stop revokes immediately; the mesh-loop owner must
  // subsequently detach and call closedAfterDetach before reuse.
  bool revoke(uint32_t now, const BrowserOtaAuthority& authority, uint32_t epoch) {
    BrowserOtaGuard guard(_state_lock);
    if (!valid(now, authority, epoch)) return false;
    _lease.revoke();
    return true;
  }

private:
  bool valid(uint32_t now, const BrowserOtaAuthority& authority, uint32_t epoch) {
    return _epoch && epoch == _epoch && _lease.permits(now, authority);
  }
  BrowserOtaLock& _state_lock;
  BrowserOtaStream& _stream;
  BrowserOtaLease _lease;
  uint32_t _epoch = 0;
};

}} // namespace mesh::ota
