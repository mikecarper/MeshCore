#include <cassert>
#include <cstdint>
#include <cstring>
#include "helpers/findmy/Nrf52FindMyMediator.h"

struct Driver {
  bool init_ok = true, set_ok = true, config_ok = true, start_ok = true;
  bool stop_ok = true, restore_ok = true, disconnect_ok = true;
  bool advertising_ = false, connected_ = false, scanning_ = false;
  bool beacon_address = false, disconnect_requested = false, restart = true;
  unsigned initializes = 0, stops = 0, restores = 0, starts = 0;
  uint16_t interval = 0;
  bool initialize() { ++initializes; return init_ok; }
  bool advertising() const { return advertising_; }
  bool connected() const { return connected_; }
  bool scanning() const { return scanning_; }
  void preventRestart() { restart = false; }
  bool stopAdvertising() { ++stops; if (!stop_ok) return false; advertising_ = false; return true; }
  bool restoreAddress() { ++restores; if (!restore_ok) return false; beacon_address = false; return true; }
  bool setBeaconAddress(const uint8_t*) { if (!set_ok) return false; beacon_address = true; return true; }
  bool configureBeacon(const uint8_t*, uint16_t units) { interval = units; return config_ok; }
  bool startAdvertising() { ++starts; if (!start_ok) return false; advertising_ = true; return true; }
  bool requestDisconnect() { disconnect_requested = true; return disconnect_ok; }
};

int main() {
  const uint8_t addr[6] = {0xc1, 2, 3, 4, 5, 6};
  const uint8_t badaddr[6] = {0x01, 2, 3, 4, 5, 6};
  const uint8_t payload[29] = {0x4c, 0};
  using mesh::findmy::Nrf52FindMyMediator;
  Driver d;
  Nrf52FindMyMediator<Driver> m(d);

  assert(!m.startBeacon(badaddr, payload, 1280));
  assert(!m.startBeacon(addr, payload, 19));
  assert(!m.startBeacon(addr, payload, 10241));
  assert(d.initializes == 0);
  assert(m.startBeacon(addr, payload, 21));
  assert(d.initializes == 1 && d.interval == 34 && m.beaconActive());
  assert(d.beacon_address && !m.dfuBusy());

  // A failed stop or failed address restore never hands ownership to DFU.
  d.stop_ok = false;
  assert(!m.claimDfu() && m.beaconActive() && d.beacon_address);
  d.stop_ok = true; d.restore_ok = false;
  assert(!m.claimDfu() && !m.beaconActive() && d.beacon_address);
  d.restore_ok = true;
  assert(m.claimDfu() && !d.beacon_address && m.dfuBusy());
  assert(d.initializes == 1); // Bluefruit.begin happens once per boot.

  // An accepted disconnect is asynchronous: the beacon stays blocked until
  // the driver observes that the DFU connection actually ended.
  d.advertising_ = true;
  d.connected_ = true;
  m.finishDfuStart(true);
  assert(m.stopDfu() && d.disconnect_requested && !d.restart && m.dfuBusy());
  assert(!m.startBeacon(addr, payload, 1280));
  d.connected_ = false;
  assert(!m.dfuBusy());
  assert(m.startBeacon(addr, payload, 10240) && d.interval == 16384);
  assert(m.stopBeacon() && !m.beaconActive() && !d.beacon_address);

  // Unknown active BLE owners or scanning must not be overwritten.
  d.advertising_ = true;
  assert(!m.startBeacon(addr, payload, 1280) && !m.claimDfu());
  d.advertising_ = false; d.scanning_ = true;
  assert(!m.startBeacon(addr, payload, 1280) && !m.claimDfu());
  d.scanning_ = false;

  // Failed beacon setup restores the address, or retains the owner until it
  // can be restored; DFU cannot reuse the controller while cleanup fails.
  d.config_ok = false;
  assert(!m.startBeacon(addr, payload, 1280) && !d.beacon_address);
  d.restore_ok = false;
  assert(!m.startBeacon(addr, payload, 1280) && d.beacon_address);
  assert(!m.claimDfu());
  d.restore_ok = true; d.config_ok = true;
  assert(m.stopBeacon() && !d.beacon_address && m.claimDfu());
  m.finishDfuStart(false);
  assert(!m.dfuBusy() && m.startBeacon(addr, payload, 1280));
  assert(m.stopBeacon());

  // Initialization failure can leave SoftDevice partially enabled; never
  // call Bluefruit.begin a second time in that boot.
  Driver broken; broken.init_ok = false;
  Nrf52FindMyMediator<Driver> fail(broken);
  assert(!fail.startBeacon(addr, payload, 1280));
  assert(!fail.claimDfu() && broken.initializes == 1);

  Driver connection; Nrf52FindMyMediator<Driver> c(connection);
  assert(c.claimDfu()); c.finishDfuStart(true); connection.connected_ = true;
  connection.disconnect_ok = false;
  assert(!c.stopDfu() && c.dfuBusy());
  assert(!c.startBeacon(addr, payload, 1280));
  connection.connected_ = false; // The peer disconnects after our failure.
  assert(c.dfuBusy() && !c.startBeacon(addr, payload, 1280));
  connection.connected_ = true;
  connection.disconnect_ok = true;
  assert(c.stopDfu() && c.dfuBusy());
  connection.connected_ = false;
  assert(!c.dfuBusy() && c.startBeacon(addr, payload, 1280));
}
