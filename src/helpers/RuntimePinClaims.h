#pragma once

#include <stdint.h>
#include "CustomPeripheralConfig.h"
#if MESH_CUSTOM_PERIPHERALS && defined(ESP32_PLATFORM)
#include <atomic>
#endif

namespace mesh { namespace pins {

enum class Owner : uint8_t { UserGpio, Rotary, Outputs, Notifications, None };

inline uint64_t pinMask(uint16_t physical_pin) {
  return physical_pin < 64 ? UINT64_C(1) << physical_pin : 0;
}

#if MESH_CUSTOM_PERIPHERALS
namespace detail {
// Only task/main-loop code may claim pins, never an ISR. ESP32 command and
// network tasks use a non-blocking lock: a preempted owner cannot deadlock a
// higher-priority requester. Other supported MCUs run these services in one
// cooperative loop and need no interrupt or SDK-specific locking.
struct Registry {
#if defined(ESP32_PLATFORM)
  std::atomic<uint32_t> masks[4][2]{};
  std::atomic_flag busy = ATOMIC_FLAG_INIT;
#else
  uint32_t masks[4][2]{};
#endif
};
inline Registry& registry() { static Registry value; return value; }
inline uint32_t read(unsigned owner, unsigned half) {
#if defined(ESP32_PLATFORM)
  return registry().masks[owner][half].load(std::memory_order_acquire);
#else
  return registry().masks[owner][half];
#endif
}
}

inline uint64_t held(Owner owner) {
  const unsigned index = static_cast<unsigned>(owner);
  if (index >= 4) return 0;
  return detail::read(index, 0) | (uint64_t(detail::read(index, 1)) << 32);
}

inline bool claimedByOther(uint16_t pin, Owner except = Owner::None) {
  const uint64_t mask = pinMask(pin);
  if (!mask) return true;
  for (unsigned i = 0; i < 4; ++i)
    if (i != static_cast<unsigned>(except) && (held(static_cast<Owner>(i)) & mask))
      return true;
  return false;
}

// Add claims before saving/applying a new configuration. Keep old claims until
// the old hardware is released, then release only the retired pins. A failed
// save can release the newly added bits without disturbing the running setup.
inline bool tryClaim(Owner owner, uint64_t additional) {
  const unsigned index = static_cast<unsigned>(owner);
  if (index >= 4) return false;
#if defined(ESP32_PLATFORM)
  if (detail::registry().busy.test_and_set(std::memory_order_acquire)) return false;
#endif
  bool free = true;
  for (unsigned i = 0; i < 4; ++i)
    if (i != index && (held(static_cast<Owner>(i)) & additional)) free = false;
  if (free) {
    for (unsigned half = 0; half < 2; ++half) {
      const uint32_t bits = static_cast<uint32_t>(additional >> (half * 32));
#if defined(ESP32_PLATFORM)
      detail::registry().masks[index][half].fetch_or(bits, std::memory_order_release);
#else
      detail::registry().masks[index][half] |= bits;
#endif
    }
  }
#if defined(ESP32_PLATFORM)
  detail::registry().busy.clear(std::memory_order_release);
#endif
  return free;
}

inline void release(Owner owner, uint64_t retired) {
  const unsigned index = static_cast<unsigned>(owner);
  if (index >= 4) return;
  for (unsigned half = 0; half < 2; ++half) {
    const uint32_t bits = static_cast<uint32_t>(retired >> (half * 32));
#if defined(ESP32_PLATFORM)
    detail::registry().masks[index][half].fetch_and(~bits, std::memory_order_release);
#else
    detail::registry().masks[index][half] &= ~bits;
#endif
  }
}
#else
inline uint64_t held(Owner) { return 0; }
inline bool claimedByOther(uint16_t, Owner = Owner::None) { return false; }
inline bool tryClaim(Owner, uint64_t) { return true; }
inline void release(Owner, uint64_t) {}
#endif

}} // namespace mesh::pins
