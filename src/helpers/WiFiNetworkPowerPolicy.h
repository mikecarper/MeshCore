#pragma once

#include <atomic>
#include <stdint.h>

namespace mesh {
namespace wifi {

static constexpr uint32_t kNetworkFailureGraceMs = 5UL * 60UL * 1000UL;
static constexpr uint32_t kNetworkRecoveryProbeMs = 2UL * 60UL * 1000UL;
static constexpr uint32_t kNetworkRecoveryIntervalMs = 30UL * 60UL * 1000UL;
static constexpr uint32_t kNetworkHealthyMs = 30000UL;

// Background network work without a configured direct MQTT uplink shares one
// outage clock. Failed attempts must not extend the grace period or cooldown.
// A configured direct MQTT uplink keeps this policy in the foreground state.
class NetworkPowerPolicy {
public:
  enum class Action { Run, Suspend, Resume };

  Action update(uint32_t now, bool useful_network, bool foreground,
                bool requested_probe = false) {
    if (!_started) {
      _started = true;
      _since = now;
    }
    if (useful_network) {
      const bool was_suspended = _suspended;
      _suspended = false;
      _probing = false;
      _since = now;
      return was_suspended ? Action::Resume : Action::Run;
    }
    if (_suspended) {
      if (foreground || requested_probe
          || now - _since >= kNetworkRecoveryIntervalMs) {
        _suspended = false;
        _probing = true;
        _since = now;
        return Action::Resume;
      }
      return Action::Suspend;
    }
    if (foreground) return Action::Run;
    const uint32_t budget = _probing
        ? kNetworkRecoveryProbeMs : kNetworkFailureGraceMs;
    if (now - _since >= budget) {
      _suspended = true;
      _probing = false;
      _since = now;
      return Action::Suspend;
    }
    return Action::Run;
  }

  bool suspended() const { return _suspended; }

private:
  bool _started = false;
  bool _suspended = false;
  bool _probing = false;
  uint32_t _since = 0;
};

enum class NetworkPowerHold : uint8_t {
  CompanionClient = 1,
  WebConfig = 2,
  OtaSeeder = 4,
  UdpBridge = 8
};

// One program-wide handoff between the MQTT owner task and the station/UI
// owners. Only the worker publishes suspension, after stopping TLS and SNTP.
// Foreground owners publish holds; they never stop the worker's transports.
class NetworkPowerControl {
public:
  bool managed() const { return _managed.load(std::memory_order_acquire); }
  void setManaged(bool value) {
    _managed.store(value, std::memory_order_release);
    if (!value) {
      _suspended.store(false, std::memory_order_release);
      _probe.store(false, std::memory_order_release);
    }
  }
  bool backgroundSuspended() const {
    return managed() && _suspended.load(std::memory_order_acquire);
  }
  void setSuspended(bool value) {
    _suspended.store(value, std::memory_order_release);
  }
  void setHold(NetworkPowerHold owner, bool value) {
    const uint8_t mask = static_cast<uint8_t>(owner);
    if (value) _holds.fetch_or(mask, std::memory_order_acq_rel);
    else _holds.fetch_and(static_cast<uint8_t>(~mask), std::memory_order_acq_rel);
  }
  bool foregroundActive() const {
    return _holds.load(std::memory_order_acquire) != 0;
  }
  void requestProbe() { _probe.store(true, std::memory_order_release); }
  bool consumeProbeRequest() {
    return _probe.exchange(false, std::memory_order_acq_rel);
  }

private:
  std::atomic<bool> _managed{false};
  std::atomic<bool> _suspended{false};
  std::atomic<bool> _probe{false};
  std::atomic<uint8_t> _holds{0};
};

inline NetworkPowerControl& networkPower() {
  static NetworkPowerControl control;
  return control;
}

}  // namespace wifi
}  // namespace mesh
