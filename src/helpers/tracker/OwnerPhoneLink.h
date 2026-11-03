#pragma once

#include <stdint.h>

namespace mesh { namespace tracker {

// Main-loop-owned state for the phone-disconnect cause only. BLE callbacks
// must be delivered to this object in order from that loop; they must not
// call it concurrently. A physical connection is not evidence of the owner
// until the caller has authenticated that peer.
class OwnerPhoneLink {
 public:
  static constexpr uint32_t kDisconnectGraceMs = 90000;
  enum class State : uint8_t { Disabled, Unknown, Near, Grace, Lost };
  enum class ReconnectPolicy : uint8_t { ClearLost, LatchLost };

  explicit OwnerPhoneLink(ReconnectPolicy policy = ReconnectPolicy::ClearLost)
      : reconnect_policy_(policy) {}

  // The caller enables this only for a configured owner in hiking mode.
  // Re-enabling starts Unknown: a connection before enabling does not count.
  void setEnabled(bool enabled) {
    if (enabled_ == enabled) return;
    enabled_ = enabled;
    reset();
  }

  // Reset only this phone cause. If the owner is still connected, the caller
  // must re-confirm that authenticated connection after resetting.
  void reset() {
    authenticated_connected_ = false;
    waiting_ = false;
    lost_ = false;
    disconnected_at_ = 0;
  }

  // Call only after the configured owner's link has been authenticated, not
  // for a raw connection, MAC-address match or BLE signal-strength reading.
  void authenticatedConnected() {
    if (!enabled_) return;
    authenticated_connected_ = true;
    waiting_ = false;
    if (reconnect_policy_ == ReconnectPolicy::ClearLost) lost_ = false;
  }

  // Unauthenticated or duplicate disconnects have no effect. The timestamp
  // should be captured when the authenticated link actually disconnects.
  void disconnected(uint32_t now) {
    if (!enabled_ || !authenticated_connected_) return;
    authenticated_connected_ = false;
    if (lost_) return;  // A manually latched cause remains active.
    disconnected_at_ = now;
    waiting_ = true;
  }

  void service(uint32_t now) {
    if (enabled_ && waiting_
        && uint32_t(now - disconnected_at_) >= kDisconnectGraceMs) {
      waiting_ = false;
      lost_ = true;
    }
  }

  bool isLost(uint32_t now) {
    service(now);
    return enabled_ && lost_;
  }

  State state(uint32_t now) {
    service(now);
    if (!enabled_) return State::Disabled;
    if (authenticated_connected_) return State::Near;
    if (waiting_) return State::Grace;
    return lost_ ? State::Lost : State::Unknown;
  }

  // A caller can cap its sleep to this delay. A due deadline returns true
  // with delay_ms=0, including when the check occurs across millis rollover.
  bool nextDelay(uint32_t now, uint32_t& delay_ms) const {
    if (!enabled_ || !waiting_) return false;
    const uint32_t elapsed = uint32_t(now - disconnected_at_);
    delay_ms = elapsed >= kDisconnectGraceMs ? 0 : kDisconnectGraceMs - elapsed;
    return true;
  }

 private:
  const ReconnectPolicy reconnect_policy_;
  bool enabled_ = false;
  bool authenticated_connected_ = false;
  bool waiting_ = false;
  bool lost_ = false;
  uint32_t disconnected_at_ = 0;
};

}} // namespace mesh::tracker
