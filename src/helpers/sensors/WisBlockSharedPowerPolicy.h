#pragma once

#include <stdint.h>

namespace mesh {
namespace gps {

enum class WisBlockPeripheralMode : uint8_t {
  GpsOnly = 0,
  Shared = 1,
  SharedPeripherals = Shared,
};

enum WisBlockRailHold : uint8_t {
  RadioPower = 1U << 0,
  EthernetPower = 1U << 1,
  ExternalStorage = 1U << 2,
};

// Conservative live-service/bus ownership holds, not proof of physical module
// presence. Ethernet builds may still have an active worker or retained bus;
// keep their normal runtime policy until an explicit owner-aware handoff.
// RAK13800 PoE's separate 5V input does not depend on this switched 3V3_S rail.
constexpr uint8_t wisBlockCompileRailHolds() {
  return 0
#if defined(RAK_3401)
      | RadioPower
#endif
#if defined(ETHERNET_ENABLED) || defined(ETHERNET_USE_RAK13800) \
    || defined(RAK4631_COMBINED_ETHERNET)
      | EthernetPower
#endif
      ;
}

// No GPIO, bus probes, allocation, or persistence belongs in this policy.
// Integrators must finish their read-only external-storage probe while the
// discovery hold is set, then freeze its result together with the saved mode.
// A powered/shared GNSS UART still cannot be safely handed to another driver.
class WisBlockSharedPowerPolicy {
 public:
  explicit WisBlockSharedPowerPolicy(uint8_t initial_holds = 0)
      : _holds(static_cast<uint8_t>(initial_holds | wisBlockCompileRailHolds())),
        _state(DiscoveryActive) {}

  // Configuration is boot-only. Reject repeated or invalid configuration
  // without changing the established owners; changing mode requires reboot.
  bool configure(WisBlockPeripheralMode mode, uint8_t detected_holds = 0) {
    if (isConfigured()
        || (mode != WisBlockPeripheralMode::GpsOnly
            && mode != WisBlockPeripheralMode::Shared)
        || (detected_holds & static_cast<uint8_t>(~KnownHolds)) != 0) {
      return false;
    }
    _holds = static_cast<uint8_t>(_holds | detected_holds);
    _state = static_cast<uint8_t>(_state | Configured);
    if (mode == WisBlockPeripheralMode::Shared) _state |= SharedMode;
    return true;
  }

  void setDiscoveryActive(bool active) { setFlag(DiscoveryActive, active); }
  void setGpsActive(bool active) { setFlag(GpsActive, active); }

  bool isConfigured() const { return (_state & Configured) != 0; }
  bool discoveryActive() const { return (_state & DiscoveryActive) != 0; }
  bool gpsActive() const { return (_state & GpsActive) != 0; }
  WisBlockPeripheralMode mode() const {
    return (_state & SharedMode) != 0 ? WisBlockPeripheralMode::Shared
                                   : WisBlockPeripheralMode::GpsOnly;
  }
  uint8_t holds() const { return _holds; }

  // Permission to remove GPS power, independent of its current requested
  // state. This is not proof that a GNSS TX pin has already become isolated.
  bool gpsPowerIndependentlyControllable() const {
    return isConfigured() && !discoveryActive()
        && mode() == WisBlockPeripheralMode::GpsOnly && _holds == 0;
  }
  bool canPowerDown() const {
    return gpsPowerIndependentlyControllable() && !gpsActive();
  }
  bool railEnabled() const { return !canPowerDown(); }

 private:
  enum State : uint8_t {
    Configured = 1U << 0,
    SharedMode = 1U << 1,
    GpsActive = 1U << 2,
    DiscoveryActive = 1U << 3,
  };
  static constexpr uint8_t KnownHolds =
      RadioPower | EthernetPower | ExternalStorage;

  // Retain unknown constructor hold bits fail-closed. They must never become
  // permission to cut power merely because a future owner is not recognized.
  uint8_t _holds;
  uint8_t _state;

  void setFlag(uint8_t flag, bool enabled) {
    if (enabled) _state |= flag;
    else _state &= static_cast<uint8_t>(~flag);
  }
};

static_assert(sizeof(WisBlockSharedPowerPolicy) == 2,
              "WisBlock rail ownership must stay allocation-free and compact");

}  // namespace gps
}  // namespace mesh
