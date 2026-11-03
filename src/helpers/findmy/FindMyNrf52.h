#pragma once

#include <stdint.h>

#if defined(MESH_SENSOR_FINDMY) && MESH_SENSOR_FINDMY && defined(NRF52_PLATFORM)

namespace mesh { namespace findmy {

// Implemented by NRF52Board's single-owner BLE mediator. The driver and
// Bluefruit singleton have process lifetime; the wrapper itself is stateless.
bool nrf52StartBeacon(const uint8_t addr[6], const uint8_t payload[29], uint16_t interval_ms);
bool nrf52StopBeacon();
bool nrf52BeaconActive();

class Nrf52Beacon {
 public:
  bool start(const uint8_t addr[6], const uint8_t payload[29], uint16_t interval_ms = 1280) {
    return nrf52StartBeacon(addr, payload, interval_ms);
  }
  bool stop() { return nrf52StopBeacon(); }
  bool isActive() const { return nrf52BeaconActive(); }
};

}} // namespace mesh::findmy
#endif
