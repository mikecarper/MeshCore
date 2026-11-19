#pragma once

#include <nrf.h>

namespace mesh {

inline void nrf52_reload_watchdog() {
  for (uint32_t channels = NRF_WDT->RREN & 0xFFu; channels; channels &= channels - 1u) {
    NRF_WDT->RR[__builtin_ctz(channels)] = WDT_RR_RR_Reload;
  }
}

} // namespace mesh
