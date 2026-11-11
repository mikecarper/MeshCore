#pragma once

#include <Arduino.h>

enum class RotaryInputEvent : uint8_t {
  None,
  Next,
  Prev,
  Select,
  LongPress,
};

class RotaryInput {
public:
  virtual ~RotaryInput() = default;

  virtual bool begin() = 0;
  virtual RotaryInputEvent poll() = 0;
  virtual bool isReady() const = 0;

  // Optional switch support. Existing board-specific encoders need only
  // implement rotation. The UI uses the pin to avoid reading a shared switch
  // a second time through its ordinary user-button driver.
  virtual int buttonPin() const { return -1; }
  virtual bool isButtonPressed() const { return false; }
  // Event-driven boards must keep polling while a switch gesture has a
  // debounce or hold deadline; rotary-only board drivers need no deadline.
  virtual bool needsPolling() const { return false; }
  virtual bool isButtonGesturePending() const { return false; }
  // Custom controls may suspend rotation while a power-saving display is
  // off. Native board backends retain their existing behavior by default.
  virtual void setRotationPolling(bool) {}
};
