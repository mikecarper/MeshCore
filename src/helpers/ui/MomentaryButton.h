#pragma once

#include <Arduino.h>

#define BUTTON_EVENT_NONE        0
#define BUTTON_EVENT_CLICK       1
#define BUTTON_EVENT_LONG_PRESS  2
#define BUTTON_EVENT_DOUBLE_CLICK 3
#define BUTTON_EVENT_TRIPLE_CLICK 4
#define BUTTON_EVENT_QUADRUPLE_CLICK 5

#ifndef MOMENTARY_BUTTON_MULTI_CLICK_MS
#define MOMENTARY_BUTTON_MULTI_CLICK_MS 280
#endif

#ifndef MOMENTARY_BUTTON_WAKE_HOLD_MS
#define MOMENTARY_BUTTON_WAKE_HOLD_MS 0
#endif

class MomentaryButton {
  int8_t _pin;
  int8_t prev, cancel;
  bool _reverse, _pull;
  int _long_millis;
  int _threshold;  // analog mode
  uint32_t down_at;
  bool _press_active;
  uint8_t _click_count;
  uint32_t _last_click_time;
  int _multi_click_window;
  bool _pending_click;
  bool _quadruple_click = false;
  int8_t _candidate_level;
  uint32_t _candidate_since;
  bool _debouncing;
#if MOMENTARY_BUTTON_WAKE_HOLD_MS > 0
  uint32_t _last_pressed_at = 0;
  bool _wake_hold_active = false;
#endif

  bool isPressed(int level) const;

public:
  MomentaryButton(int8_t pin, int long_press_mills=0, bool reverse=false, bool pulldownup=false, bool multiclick=true);
  MomentaryButton(int8_t pin, int long_press_mills, int analog_threshold);
  virtual void begin();
  virtual int check(bool repeat_click=false);  // returns one of BUTTON_EVENT_*
  void enableQuadrupleClick() { _quadruple_click = true; }
  void cancelClick();  // suppress next BUTTON_EVENT_CLICK (if already in DOWN state)
  uint8_t getPin() { return _pin; }
  bool isPressed() const;
  bool isWakeHoldActive() const {
#if MOMENTARY_BUTTON_WAKE_HOLD_MS > 0
    return _wake_hold_active
        && (uint32_t)(millis() - _last_pressed_at) < MOMENTARY_BUTTON_WAKE_HOLD_MS;
#else
    return false;
#endif
  }
  bool needsPolling() const {
    return _debouncing || _press_active || _pending_click || isWakeHoldActive();
  }
};
