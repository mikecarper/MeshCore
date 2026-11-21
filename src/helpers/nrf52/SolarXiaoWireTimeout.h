#pragma once

// Included in the SolarXiao build of the nRF52 Wire master driver.
namespace mesh { namespace solar {

class WireWait {
  // Shared by the phases of one Wire transmit or receive. The poll limit
  // still terminates before/without scheduler ticks.
  unsigned remaining = 25000;

  static void abortTransfer(NRF_TWIM_Type* bus) {
    bus->EVENTS_STOPPED = 0;
    bus->TASKS_RESUME = 1;  // TWIM cannot STOP while suspended.
    bus->TASKS_STOP = 1;
    for (unsigned i = 0; i < 200 && !bus->EVENTS_STOPPED; ++i) {
      delayMicroseconds(5);
    }
    // A peripheral holding SCL low can prevent even STOPPED. Disable the
    // master to release its pins/DMA; retain the selected pins and frequency.
    bus->ENABLE = TWIM_ENABLE_ENABLE_Disabled;
    bus->EVENTS_RXSTARTED = 0;
    bus->EVENTS_TXSTARTED = 0;
    bus->EVENTS_LASTRX = 0;
    bus->EVENTS_LASTTX = 0;
    bus->EVENTS_SUSPENDED = 0;
    bus->EVENTS_STOPPED = 0;
    bus->EVENTS_ERROR = 0;
    bus->ERRORSRC = bus->ERRORSRC;  // write one to clear
    bus->ENABLE = TWIM_ENABLE_ENABLE_Enabled;
  }

public:
  bool wait(NRF_TWIM_Type* bus, volatile uint32_t& event, bool acceptError) {
    while (!event && !(acceptError && bus->EVENTS_ERROR)) {
      if (!remaining) {
        abortTransfer(bus);
        return false;
      }
      --remaining;
      delayMicroseconds(2);
    }
    return true;
  }
};

}}  // namespace mesh::solar
