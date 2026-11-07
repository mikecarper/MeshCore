#pragma once

#include "RotaryInput.h"

// Based on the GPIO encoder driver by Rafal Wojdyla (omeg/MeshCore),
// commit 262058b374b553be71b462b632fd53c7dc4ffae7.
// Polling driver for a mechanical quadrature encoder, with an optional
// active-low push switch. Poll frequently; missed electrical transitions
// cannot be reconstructed. Use 3.3 V signals on 3.3 V boards.
class GpioRotaryInput : public RotaryInput {
public:
  GpioRotaryInput(int16_t clkPin, int16_t dtPin, int16_t buttonPin = -1,
                  uint16_t transitionsPerDetent = 4, bool reverse = false,
                  bool pullups = true);

  // Validates before stopping the previous configuration; rejection preserves
  // a working input. Does not initialize new pins until begin() is explicitly
  // called. Board-specific GPIO safety belongs to the caller.
  bool configure(int16_t clkPin, int16_t dtPin, int16_t buttonPin = -1,
                 uint16_t transitionsPerDetent = 4, bool reverse = false,
                 bool pullups = true);
  bool begin() override;
  void end();
  RotaryInputEvent poll() override;
  bool isReady() const override { return ready; }
  int buttonPin() const override { return button_pin; }
  bool isButtonPressed() const override;
  bool needsPolling() const override;
  bool isButtonGesturePending() const override;
  void setRotationPolling(bool active) override;

private:
  static bool validConfiguration(int16_t clkPin, int16_t dtPin,
                                 int16_t buttonPin, uint16_t steps);
  uint8_t readState() const;
  RotaryInputEvent pollRotation();
  RotaryInputEvent pollButton();

  int16_t clk_pin;
  int16_t dt_pin;
  int16_t button_pin;
  uint16_t transitions_per_detent;
  bool reverse;
  bool pullups;
  bool ready = false;
  bool rotation_polling = true;
  uint8_t previous_state = 0;
  int16_t transition_accumulator = 0;
  bool button_raw_pressed = false;
  bool button_pressed = false;
  bool button_armed = false;
  bool button_long_press_sent = false;
  uint32_t button_changed_at = 0;
  uint32_t button_down_at = 0;
};
