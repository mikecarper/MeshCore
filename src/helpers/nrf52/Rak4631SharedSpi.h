#pragma once

#if defined(RAK4631_COMBINED_ETHERNET)

#include <atomic>

namespace mesh {
namespace ota {

// The combined RAK4631 image gives the WisBlock clock/data pins to one
// controller at a time. Reserve them before starting Ethernet's DHCP task;
// release them only after that task and every Ethernet socket have stopped.
// NOR discovery and QSPI staging must never touch these pins while reserved.
inline std::atomic<bool>& rak4631_ethernet_spi_owner() {
  static std::atomic<bool> owner{false};
  return owner;
}

inline bool rak4631_ethernet_owns_spi() {
  return rak4631_ethernet_spi_owner().load(std::memory_order_acquire);
}

inline void rak4631_set_ethernet_spi_owner(bool ethernet) {
  rak4631_ethernet_spi_owner().store(ethernet, std::memory_order_release);
}

} // namespace ota
} // namespace mesh

#endif
