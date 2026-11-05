#pragma once
#include <stdint.h>

namespace mesh { namespace usbkeyboard {

// A press opens a 30-second discovery window. An attached keyboard keeps it
// open; after unplugging, the same window allows time for a reconnection.
class ProbeWindow {
public:
  static constexpr uint32_t DurationMillis = 30000;
  void extend(uint32_t now) { _last_activity = now; }
  bool expired(uint32_t now, bool connected) {
    if (connected) { _last_activity = now; return false; }
    return static_cast<uint32_t>(now - _last_activity) >= DurationMillis;
  }
private:
  uint32_t _last_activity = 0;
};

} }
