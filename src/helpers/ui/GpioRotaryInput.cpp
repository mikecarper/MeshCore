#include "GpioRotaryInput.h"
#include <helpers/CustomPeripheralConfig.h>
#if defined(ESP32_PLATFORM) && MESH_CUSTOM_PERIPHERALS
#include <helpers/I2cExpander.h>
#endif

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
bool inputMode(int16_t pin, bool pullup) {
#if defined(ESP32_PLATFORM) && MESH_CUSTOM_PERIPHERALS
  if (mesh::expander::isPin(pin)) return mesh::expander::configurePin(pin, false, pullup);
#endif
  pinMode(pin, pullup ? INPUT_PULLUP : INPUT);
  return true;
}
bool inputLevel(int16_t pin, bool& high) {
#if defined(ESP32_PLATFORM) && MESH_CUSTOM_PERIPHERALS
  if (mesh::expander::isPin(pin)) return mesh::expander::read(pin, high);
#endif
  high = digitalRead(pin) != LOW;
  return true;
}
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

bool GpioRotaryInput::readState(uint8_t& state) const {
#if defined(ESP32_PLATFORM) && MESH_CUSTOM_PERIPHERALS
  if (mesh::expander::isPin(clk_pin) && mesh::expander::isPin(dt_pin))
    return mesh::expander::readPair(clk_pin, dt_pin, state);
#endif
  bool clk, dt;
  if (!inputLevel(clk_pin, clk) || !inputLevel(dt_pin, dt)) return false;
  state = (clk ? 2 : 0) | (dt ? 1 : 0);
  return true;
}

bool GpioRotaryInput::readButton(bool& pressed) const {
  bool high;
  if (!inputLevel(button_pin, high)) return false;
  pressed = !high;
  return true;
}

bool GpioRotaryInput::begin() {
  if (retained_button_pullup) end();
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

  // A partial expander configuration is released through end() too. No
  // expander identifier is ever passed to an Arduino GPIO operation.
  ready = true;
  if (!inputMode(clk_pin, pullups) || !inputMode(dt_pin, pullups)) {
    stopAfterIoFailure(); return false;
  }
  if (button_pin >= 0) {
    if (!inputMode(button_pin, pullups) || !readButton(button_raw_pressed)) {
      stopAfterIoFailure(); return false;
    }
    button_pressed = button_raw_pressed;
    // A switch held during initialization must be released before it can
    // produce a press or enter the UI's held-button rescue action.
    button_armed = !button_pressed;
    button_changed_at = millis();
    button_down_at = button_changed_at;
  }
  if (!readState(previous_state)) { stopAfterIoFailure(); return false; }
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
  // A fault may leave the native user switch pulled up for the ordinary
  // board-button fallback. Explicit disable/reassignment still releases it.
  if (retained_button_pullup) {
    inputMode(button_pin, false);
    retained_button_pullup = false;
  }
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
    inputMode(clk_pin, false);
    inputMode(dt_pin, false);
    if (button_pin >= 0) inputMode(button_pin, false);
  }
  ready = false;
  transition_accumulator = 0;
  button_armed = false;
  button_pressed = false;
  button_raw_pressed = false;
  button_long_press_sent = false;
}

void GpioRotaryInput::stopAfterIoFailure() {
  bool native_button = button_pin >= 0;
#if defined(ESP32_PLATFORM) && MESH_CUSTOM_PERIPHERALS
  native_button = native_button && !mesh::expander::isPin(button_pin);
#endif
  const bool preserve_pullup = native_button && pullups;
  end();
  if (preserve_pullup) {
    // Settings keep this pin claimed until an explicit disable/reassignment.
    // Losing an I2C encoder must not leave a shared native user button floating.
    pinMode(button_pin, INPUT_PULLUP);
    retained_button_pullup = true;
  }
}

RotaryInputEvent GpioRotaryInput::poll() {
  if (!ready) return RotaryInputEvent::None;

  // Sample both sources even when they coincide. A switch action wins, and
  // the completed rotation is consumed rather than replayed on the next poll.
  RotaryInputEvent rotation = rotation_polling ? pollRotation() : RotaryInputEvent::None;
  if (!ready) return RotaryInputEvent::None;
  if (button_pin >= 0) {
    RotaryInputEvent button = pollButton();
    if (button != RotaryInputEvent::None) return button;
  }
  return rotation;
}

bool GpioRotaryInput::isButtonPressed() const {
  bool pressed = false;
  return ready && button_pin >= 0 && button_armed && button_pressed &&
         readButton(pressed) && pressed;
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
  if (ready && !readState(previous_state)) stopAfterIoFailure();
#if defined(NRF52_PLATFORM) || defined(RP2040_PLATFORM)
  if (ready && active) {
    attachInterrupt(uint32_t(clk_pin), wakeRotaryPoller, CHANGE);
    attachInterrupt(uint32_t(dt_pin), wakeRotaryPoller, CHANGE);
  }
#endif
}

RotaryInputEvent GpioRotaryInput::pollButton() {
  const uint32_t now = millis();
  bool pressed;
  if (!readButton(pressed)) { stopAfterIoFailure(); return RotaryInputEvent::None; }
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
  uint8_t current_state;
  // A lost bus sample stops the input. It is not treated as a released
  // contact, and reconnecting a device cannot replay a partial detent/click.
  if (!readState(current_state)) { stopAfterIoFailure(); return RotaryInputEvent::None; }
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
