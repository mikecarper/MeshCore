#pragma once

#include <nrf_sdm.h>

namespace mesh_nrf52 {

// sd_softdevice_is_enabled is an SVC wrapper. With LTO, its assembly body
// does not tell GCC that the output pointer is written by the supervisor.
// Load the result from volatile storage after a compiler memory barrier.
inline uint32_t softdeviceIsEnabled(uint8_t& enabled) {
  volatile uint8_t state = 0;
  const uint32_t result = sd_softdevice_is_enabled((uint8_t*)&state);
  __asm volatile ("" ::: "memory");
  enabled = state;
  return result;
}

} // namespace mesh_nrf52
