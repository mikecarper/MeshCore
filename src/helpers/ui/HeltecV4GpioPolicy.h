#pragma once

#include <helpers/CustomPeripheralConfig.h>

namespace mesh { namespace ui {

namespace detail {
// One instance across translation units. Only the display settings loader
// writes this, before any display probe or custom-pin initialization.
inline bool& heltecV4OledExpansionBootState() {
  static bool active = false;
  return active;
}
inline bool& heltecV4OledExpansionAllocationState() {
  static bool allowed = false;
  return allowed;
}
}

inline bool heltecV4OledExpansionActive() {
#if MESH_CUSTOM_PERIPHERALS && defined(HELTEC_V4_DISPLAY_UNIFIED) && HELTEC_V4_DISPLAY_UNIFIED
  return detail::heltecV4OledExpansionBootState();
#else
  return false;
#endif
}

// A saved switch back to shared display wiring closes new allocations now,
// while the display driver keeps its boot-time ownership until reboot.
inline bool heltecV4OledExpansionPinsAvailable() {
#if MESH_CUSTOM_PERIPHERALS && defined(HELTEC_V4_DISPLAY_UNIFIED) && HELTEC_V4_DISPLAY_UNIFIED
  return heltecV4OledExpansionActive() && detail::heltecV4OledExpansionAllocationState();
#else
  return false;
#endif
}

inline void setHeltecV4OledExpansionAllocation(bool allowed) {
#if MESH_CUSTOM_PERIPHERALS && defined(HELTEC_V4_DISPLAY_UNIFIED) && HELTEC_V4_DISPLAY_UNIFIED
  detail::heltecV4OledExpansionAllocationState() = allowed && heltecV4OledExpansionActive();
#else
  (void)allowed;
#endif
}

// Boot-load operation only. CLI setters save the next boot's preference and
// must not call this while display drivers or accessory GPIOs are running.
inline void setHeltecV4OledExpansionAtBoot(bool active) {
#if MESH_CUSTOM_PERIPHERALS && defined(HELTEC_V4_DISPLAY_UNIFIED) && HELTEC_V4_DISPLAY_UNIFIED
  detail::heltecV4OledExpansionBootState() = active;
  detail::heltecV4OledExpansionAllocationState() = active;
#else
  (void)active;
#endif
}

}} // namespace mesh::ui
