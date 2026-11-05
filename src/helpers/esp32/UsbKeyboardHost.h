#pragma once

#if defined(MESH_USB_KEYBOARD_HOST) && MESH_USB_KEYBOARD_HOST
#include <stdint.h>

namespace mesh { namespace usbkeyboard {

enum class EventType : uint8_t { None, Connected, Disconnected, Report, Unsupported, Error, Stopped };
struct Event {
  EventType type;
  uint8_t report[8];
};

// A button press starts or extends a short host discovery window. The caller
// consumes events on the UI/mesh thread; no display/radio work runs in the
// host task. USB Companion remains unavailable in this opt-in build.
bool probe();
bool active();
bool next(Event& event);

} }
#endif
