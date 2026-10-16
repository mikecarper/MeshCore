#pragma once

#include <Arduino.h>

namespace mesh { namespace nrf52 {

// Registration and shutdown run on the application thread, never from an ISR.
// An inline function-local static is one shared object across translation
// units; a header-level static would silently separate the board and owner.
struct TerminalEthernetOwner {
  bool (*stop)(void*, uint32_t) = nullptr;
  void* context = nullptr;
};

inline TerminalEthernetOwner& terminalEthernetOwner() {
  static TerminalEthernetOwner owner;
  return owner;
}

inline bool registerTerminalEthernetShutdown(bool (*stop)(void*, uint32_t), void* context) {
#if defined(RAK_3401)
  // RAK13302 and RAK13800 occupy the same WisBlock IO slot and share SPI
  // SCK/MISO/MOSI/CS; Ethernet RESET also aliases the radio FEM enable IO3.
  // No runtime arbitration exists for this electrically conflicting pair.
  (void)stop;
  (void)context;
  return false;
#else
  if (!stop) return false;
  auto& owner = terminalEthernetOwner();
  if (owner.stop && (owner.stop != stop || owner.context != context)) return false;
  owner.stop = stop;
  owner.context = context;
  return true;
#endif
}

inline bool terminalEthernetShutdown(uint32_t timeout_ms = 500) {
  const auto& owner = terminalEthernetOwner();
  return !owner.stop || owner.stop(owner.context, timeout_ms);
}

// Only after the actual SPI owner has stopped/ended its controller. These
// WisBlock nets otherwise retain high CS/reset/MOSI latches into 3V3_S loads.
inline void releaseRakEthernetBusPads() {
  pinMode(3, INPUT);   // SCK
  pinMode(29, INPUT);  // MISO
  pinMode(30, INPUT);  // MOSI
  pinMode(26, INPUT);  // CS
  pinMode(21, INPUT);  // W5100S reset (IO3)
}

} } // namespace mesh::nrf52
