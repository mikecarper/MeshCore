#pragma once

#if defined(RAK4631_COMBINED_ETHERNET) || defined(OTA_RAK15002_SD_STORE)

#include <atomic>
#include <stdint.h>

namespace mesh {
namespace ota {

// The combined RAK4631 image gives the WisBlock clock/data pins to one
// controller at a time. Reserve them before starting Ethernet's DHCP task;
// release them only after that task and every Ethernet socket have stopped.
// NOR discovery and QSPI staging must never touch these pins while reserved.
enum class RakSharedSpiOwner : uint8_t { None, Ethernet, Sd, Nor };

inline std::atomic<RakSharedSpiOwner>& rak4631_shared_spi_owner() {
  static std::atomic<RakSharedSpiOwner> owner{RakSharedSpiOwner::None};
  return owner;
}

inline RakSharedSpiOwner rak4631_spi_owner() {
  return rak4631_shared_spi_owner().load(std::memory_order_acquire);
}

// Claims are main-loop lifetime reservations, not a recursive transaction
// lock. The same sole backend may confirm its retained claim; another device
// must never overwrite it, including on a failed mount or DHCP startup.
inline bool rak4631_try_acquire_spi(RakSharedSpiOwner wanted) {
  if (wanted == RakSharedSpiOwner::None) return false;
  auto expected = RakSharedSpiOwner::None;
  return rak4631_shared_spi_owner().compare_exchange_strong(
      expected, wanted, std::memory_order_acq_rel) || expected == wanted;
}

inline void rak4631_release_spi(RakSharedSpiOwner owner) {
  if (owner == RakSharedSpiOwner::None) return;
  auto expected = owner;
  (void)rak4631_shared_spi_owner().compare_exchange_strong(
      expected, RakSharedSpiOwner::None, std::memory_order_acq_rel);
}

inline bool rak4631_ethernet_owns_spi() {
  return rak4631_spi_owner() == RakSharedSpiOwner::Ethernet;
}

inline void rak4631_set_ethernet_spi_owner(bool ethernet) {
  if (ethernet) (void)rak4631_try_acquire_spi(RakSharedSpiOwner::Ethernet);
  else rak4631_release_spi(RakSharedSpiOwner::Ethernet);
}

} // namespace ota
} // namespace mesh

#endif
