#pragma once

#include <stdint.h>

namespace mesh { namespace findmy {

// The pinned S140 has one advertising set and one local identity address.
// All calls run on the application loop. Driver must keep BLE event callbacks
// thread-safe and may report a connection after a disconnect request succeeds.
template <typename Driver>
class Nrf52FindMyMediator {
  enum class Owner : uint8_t { None, Beacon, Dfu };
  Driver& driver_;
  Owner owner_ = Owner::None;
  bool initialized_ = false;
  bool initialization_fault_ = false;
  bool dfu_stopping_ = false;

  bool ensureInitialized() {
    if (initialization_fault_) return false;
    if (initialized_) return true;
    // A partial SoftDevice initialization is not safe to start a second time.
    if (!driver_.initialize()) { initialization_fault_ = true; return false; }
    initialized_ = true;
    return true;
  }

 public:
  explicit Nrf52FindMyMediator(Driver& driver) : driver_(driver) {}

  bool startBeacon(const uint8_t addr[6], const uint8_t payload[29], uint16_t interval_ms) {
    if (!addr || !payload || (addr[0] & 0xc0u) != 0xc0u
        || interval_ms < 20 || interval_ms > 10240 || owner_ != Owner::None) return false;
    if (!ensureInitialized() || driver_.connected() || driver_.scanning()
        || driver_.advertising()) return false;
    // Claim before modifying the address, so a partially failed setup is
    // always cleaned up before DFU or a new beacon may start.
    owner_ = Owner::Beacon;
    const uint16_t units = static_cast<uint16_t>((uint32_t(interval_ms) * 8u + 4u) / 5u);
    if (!driver_.setBeaconAddress(addr) || !driver_.configureBeacon(payload, units)
        || !driver_.startAdvertising()) {
      (void)stopBeacon();
      return false;
    }
    return true;
  }

  bool stopBeacon() {
    if (owner_ != Owner::Beacon) return true;
    driver_.preventRestart();
    if (driver_.advertising() && !driver_.stopAdvertising()) return false;
    // SoftDevice refuses identity address changes while scanning/connecting.
    if (driver_.connected() || driver_.scanning() || !driver_.restoreAddress()) return false;
    owner_ = Owner::None;
    return true;
  }

  bool beaconActive() const {
    return owner_ == Owner::Beacon && driver_.advertising();
  }

  // Claim before mutating the shared advertisement or its identity. If DFU
  // setup subsequently fails, finishDfuStart releases only an idle owner.
  bool claimDfu() {
    if (owner_ == Owner::Beacon && !stopBeacon()) return false;
    if (owner_ != Owner::None || !ensureInitialized() || driver_.connected()
        || driver_.scanning() || driver_.advertising()) return false;
    owner_ = Owner::Dfu;
    dfu_stopping_ = false;
    return true;
  }

  void finishDfuStart(bool success) {
    if (owner_ != Owner::Dfu || success) return;
    driver_.preventRestart();
    if ((!driver_.advertising() || driver_.stopAdvertising()) && !driver_.connected())
      owner_ = Owner::None;
  }

  bool stopDfu() {
    if (owner_ != Owner::Dfu) return true;
    driver_.preventRestart();
    if (driver_.advertising() && !driver_.stopAdvertising()) return false;
    if (driver_.connected() && !driver_.requestDisconnect() && driver_.connected()) return false;
    // Only release the DFU claim after an accepted disconnect. If the request
    // failed, a later spontaneous disconnect must not silently drop ownership
    // while the board still believes DFU is running.
    dfu_stopping_ = true;
    (void)dfuBusy();
    return true;
  }

  bool dfuBusy() {
    if (owner_ == Owner::Dfu && dfu_stopping_
        && !driver_.connected() && !driver_.advertising()) {
      owner_ = Owner::None;
      dfu_stopping_ = false;
    }
    return owner_ == Owner::Dfu;
  }
};

}} // namespace mesh::findmy
