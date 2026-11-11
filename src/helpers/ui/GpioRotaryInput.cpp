#include "GpioRotaryInput.h"

namespace {
// Signed movement for (previous AB << 2) | current AB. Adjacent bounce
// reverses the previous movement; a two-bit jump loses position and resets
// the unfinished detent instead of combining unrelated movement.
constexpr int8_t QUADRATURE_TRANSITIONS[16] = {
   0, -1,  1,  0,
   1,  0,  0, -1,
  -1,  0,  0,  1,
   0,  1, -1,  0,
};
constexpr uint32_t BUTTON_DEBOUNCE_MILLIS = 25;
constexpr uint32_t BUTTON_LONG_PRESS_MILLIS = 1200;
#if defined(NRF52_PLATFORM) || defined(RP2040_PLATFORM)
// Wake sd_app_evt_wait()/WFI so task-context polling can observe the edge.
// No quadrature or button state is changed in interrupt context.
void wakeRotaryPoller() {}
#endif
}

GpioRotaryInput::GpioRotaryInput(int16_t clkPin, int16_t dtPin,
                               int16_t buttonPin, uint16_t transitionsPerDetent,
                               bool reverseDirection, bool usePullups)
  : clk_pin(clkPin), dt_pin(dtPin), button_pin(buttonPin),
    transitions_per_detent(transitionsPerDetent), reverse(reverseDirection),
    pullups(usePullups) { }

bool GpioRotaryInput::validConfiguration(int16_t clkPin, int16_t dtPin,
                                        int16_t buttonPin, uint16_t steps) {
  return clkPin >= 0 && dtPin >= 0 && buttonPin >= -1 &&
         clkPin != dtPin && clkPin != buttonPin && dtPin != buttonPin &&
         steps >= 1 && steps <= 255;
}

bool GpioRotaryInput::configure(int16_t clkPin, int16_t dtPin,
                                int16_t buttonPin, uint16_t transitionsPerDetent,
                                bool reverseDirection, bool usePullups) {
  if (!validConfiguration(clkPin, dtPin, buttonPin, transitionsPerDetent)) {
    return false;
  }
  end();
  clk_pin = clkPin;
  dt_pin = dtPin;
  button_pin = buttonPin;
  transitions_per_detent = transitionsPerDetent;
  reverse = reverseDirection;
  pullups = usePullups;
  return true;
}

uint8_t GpioRotaryInput::readState() const {
  return (digitalRead(clk_pin) == LOW ? 0 : 2) |
         (digitalRead(dt_pin) == LOW ? 0 : 1);
}

bool GpioRotaryInput::begin() {
  ready = false;
  transition_accumulator = 0;
  button_raw_pressed = false;
  button_pressed = false;
  button_armed = false;
  button_long_press_sent = false;

  // Check every pin before touching any GPIO. poll() never retries a failed
  // begin, so an invalid or disabled configuration remains inert.
  if (!validConfiguration(clk_pin, dt_pin, button_pin, transitions_per_detent)) {
    return false;
  }

  pinMode(clk_pin, pullups ? INPUT_PULLUP : INPUT);
  pinMode(dt_pin, pullups ? INPUT_PULLUP : INPUT);
  if (button_pin >= 0) {
    pinMode(button_pin, pullups ? INPUT_PULLUP : INPUT);
    button_raw_pressed = digitalRead(button_pin) == LOW;
    button_pressed = button_raw_pressed;
    // A switch held during initialization must be released before it can
    // produce a press or enter the UI's held-button rescue action.
    button_armed = !button_pressed;
    button_changed_at = millis();
    button_down_at = button_changed_at;
  }
  previous_state = readState();
#if defined(NRF52_PLATFORM) || defined(RP2040_PLATFORM)
  if (rotation_polling) {
    attachInterrupt(uint32_t(clk_pin), wakeRotaryPoller, CHANGE);
    attachInterrupt(uint32_t(dt_pin), wakeRotaryPoller, CHANGE);
  }
  if (button_pin >= 0) attachInterrupt(uint32_t(button_pin), wakeRotaryPoller, CHANGE);
#endif
  ready = true;
  return true;
}

void GpioRotaryInput::end() {
  // Remove our optional pull-ups when pins are reassigned. Never access pins
  // from a rejected configuration or from an object that was not started.
  if (ready) {
#if defined(NRF52_PLATFORM) || defined(RP2040_PLATFORM)
    if (rotation_polling) {
      detachInterrupt(uint32_t(clk_pin));
      detachInterrupt(uint32_t(dt_pin));
    }
    if (button_pin >= 0) detachInterrupt(uint32_t(button_pin));
#endif
    pinMode(clk_pin, INPUT);
    pinMode(dt_pin, INPUT);
    if (button_pin >= 0) pinMode(button_pin, INPUT);
  }
  ready = false;
  transition_accumulator = 0;
  button_armed = false;
  button_pressed = false;
  button_raw_pressed = false;
  button_long_press_sent = false;
}

RotaryInputEvent GpioRotaryInput::poll() {
  if (!ready) return RotaryInputEvent::None;

  // Sample both sources even when they coincide. A switch action wins, and
  // the completed rotation is consumed rather than replayed on the next poll.
  RotaryInputEvent rotation = rotation_polling ? pollRotation() : RotaryInputEvent::None;
  if (button_pin >= 0) {
    RotaryInputEvent button = pollButton();
    if (button != RotaryInputEvent::None) return button;
  }
  return rotation;
}

bool GpioRotaryInput::isButtonPressed() const {
  return ready && button_pin >= 0 && button_armed && button_pressed &&
         digitalRead(button_pin) == LOW;
}

bool GpioRotaryInput::needsPolling() const {
#if defined(ESP32_PLATFORM)
  // GPIO light-sleep wake alone does not release the main task's ordinary
  // 10 ms RTOS delay. Keep the existing cooperative 1 ms loop cadence while
  // a custom encoder's rotation is enabled. The UI suspends rotation when a
  // power-saving display is off; its switch remains available to wake it.
  // This veto affects manual CPU sleep, not WiFi/Bluetooth modem policies.
  return (ready && rotation_polling) || isButtonGesturePending();
#else
  return isButtonGesturePending();
#endif
}

bool GpioRotaryInput::isButtonGesturePending() const {
  return ready && button_pin >= 0 &&
      (button_raw_pressed != button_pressed ||
       (button_armed && button_pressed && !button_long_press_sent));
}

void GpioRotaryInput::setRotationPolling(bool active) {
  if (rotation_polling == active) return;
#if defined(NRF52_PLATFORM) || defined(RP2040_PLATFORM)
  if (ready && !active) {
    detachInterrupt(uint32_t(clk_pin));
    detachInterrupt(uint32_t(dt_pin));
  }
#endif
  rotation_polling = active;
  transition_accumulator = 0;
  // No AB polling is needed while suspended. Sampling on each mode change
  // makes the first resumed step independent of turns made with the screen off.
  if (ready) previous_state = readState();
#if defined(NRF52_PLATFORM) || defined(RP2040_PLATFORM)
  if (ready && active) {
    attachInterrupt(uint32_t(clk_pin), wakeRotaryPoller, CHANGE);
    attachInterrupt(uint32_t(dt_pin), wakeRotaryPoller, CHANGE);
  }
#endif
}

RotaryInputEvent GpioRotaryInput::pollButton() {
  const uint32_t now = millis();
  const bool pressed = digitalRead(button_pin) == LOW;
  if (pressed != button_raw_pressed) {
    button_raw_pressed = pressed;
    button_changed_at = now;
  }

  if (button_raw_pressed != button_pressed &&
      uint32_t(now - button_changed_at) >= BUTTON_DEBOUNCE_MILLIS) {
    button_pressed = button_raw_pressed;
    if (!button_armed) {
      if (!button_pressed) button_armed = true;
      return RotaryInputEvent::None;
    }
    if (button_pressed) {
      button_down_at = button_changed_at;
      button_long_press_sent = false;
    } else if (!button_long_press_sent) {
      return uint32_t(button_changed_at - button_down_at) >= BUTTON_LONG_PRESS_MILLIS
          ? RotaryInputEvent::LongPress : RotaryInputEvent::Select;
    }
  }

  if (button_armed && button_pressed && button_raw_pressed &&
      !button_long_press_sent &&
      uint32_t(now - button_down_at) >= BUTTON_LONG_PRESS_MILLIS) {
    button_long_press_sent = true;
    return RotaryInputEvent::LongPress;
  }
  return RotaryInputEvent::None;
}

RotaryInputEvent GpioRotaryInput::pollRotation() {
  const uint8_t current_state = readState();
  if (current_state == previous_state) return RotaryInputEvent::None;

  const int8_t movement =
      QUADRATURE_TRANSITIONS[(previous_state << 2) | current_state];
  previous_state = current_state;
  if (movement == 0) {
    transition_accumulator = 0;
    return RotaryInputEvent::None;
  }
  transition_accumulator += reverse ? -movement : movement;
  if (transition_accumulator >= int16_t(transitions_per_detent)) {
    transition_accumulator = 0;
    return RotaryInputEvent::Next;
  }
  // Keep both comparisons signed, including on 16-bit platforms.
  if (transition_accumulator <= -int16_t(transitions_per_detent)) {
    transition_accumulator = 0;
    return RotaryInputEvent::Prev;
  }
  return RotaryInputEvent::None;
}
