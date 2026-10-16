#pragma once

#include <stdint.h>

namespace station_g3 {

// Physical daughterboard sockets. This is unrelated to MeshCore's radio2 PHY.
struct RadioPins {
  uint8_t nss, irq, reset, busy, sck, miso, mosi, lna;
};

inline const RadioPins& pinsForSlot(uint8_t slot) {
  static const RadioPins primary = {11, 48, 21, 47, 12, 14, 13, 10};
  static const RadioPins secondary = {43, 2, 44, 1, 39, 41, 40, 42};
  return slot == 2 ? secondary : primary;
}

struct SlotState {
  uint8_t bootMode = 0;   // 0=auto, 1/2=explicit; frozen for this boot
  uint8_t savedMode = 0;  // next-boot setting, changes only after checked commit
  uint8_t last = 0;       // last successfully used socket
  uint8_t candidate = 1; // pin binding during a no-transmit probe
  uint8_t active = 0;
  uint8_t detected = 0;   // bit 0=primary, bit 1=secondary
  bool loaded = false;
  bool settingsHealthy = true;

  uint8_t choose(uint8_t mask) const {
    if (bootMode > 2) return 0;
    if (bootMode != 0) return mask & (1U << (bootMode - 1)) ? bootMode : 0;
    if (last >= 1 && last <= 2 && (mask & (1U << (last - 1)))) return last;
    return mask & 1U ? 1 : (mask & 2U ? 2 : 0);
  }
};

// Shared across translation units: board, driver, FEM and reset/wake logic
// must observe one binding, never separate header-static copies.
inline SlotState& slotState() {
  static SlotState state;
  return state;
}
inline const RadioPins& activePins() { return pinsForSlot(slotState().candidate); }
inline bool isRadioPin(uint8_t pin) {
  for (uint8_t slot = 1; slot <= 2; ++slot) {
    const RadioPins& p = pinsForSlot(slot);
    if (pin == p.nss || pin == p.irq || pin == p.reset || pin == p.busy ||
        pin == p.sck || pin == p.miso || pin == p.mosi || pin == p.lna) return true;
  }
  return false;
}

void loadSlotSettings();
bool saveSlotMode(uint8_t mode);
void rememberActiveSlot(uint8_t slot);
const char* slotModeName(uint8_t mode);

} // namespace station_g3
