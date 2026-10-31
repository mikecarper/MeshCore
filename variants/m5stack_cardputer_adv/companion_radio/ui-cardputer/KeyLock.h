#pragma once

#include "hw/CardputerKeyboard.h"

// Key lock for the Cardputer UI. While locked every key press is dropped
// except Fn+Q, which unlocks. Not persisted; one byte of state.
struct KeyLock {
  bool locked = false;

  static bool isToggle(const Keyboard::Event &e) {
    return e.modifiers.fn && e.key == Keyboard::KEY_Q;
  }

  // Returns true when the event must be dropped.
  bool filter(const Keyboard::Event &e) const { return locked && !isToggle(e); }

  void toggle() { locked = !locked; }
};
