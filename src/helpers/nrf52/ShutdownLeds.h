#pragma once

#include <Arduino.h>

namespace mesh_nrf52 {

inline void turnOffShutdownLed(int32_t pin) {
  // Several variants use -1, 0xff or PINS_COUNT for an absent LED. Never
  // convert one of these sentinels into an actual GPIO during shutdown.
  if (pin < 0 || static_cast<uint32_t>(pin) >= PINS_COUNT) return;
  if (digitalPinToPinName(pin) == 0xff) return;
  // Set the latch before enabling the output, including early boot shutdown.
  digitalWrite(pin, !LED_STATE_ON);
  pinMode(pin, OUTPUT);
}

inline void turnOffShutdownLeds() {
  // Bluefruit uses LED_BLUE. Prefer it over the legacy LED_CONN alias: some
  // variants retain an unused LED_CONN definition without a corresponding pin.
#if defined(LED_BLUE)
  turnOffShutdownLed(LED_BLUE);
#elif defined(LED_CONN)
  turnOffShutdownLed(LED_CONN);
#endif
#if defined(LED_BUILTIN)
  turnOffShutdownLed(LED_BUILTIN);
#endif
#if defined(LED_PIN)
  turnOffShutdownLed(LED_PIN);
#endif
#if defined(LED_RED)
  turnOffShutdownLed(LED_RED);
#endif
#if defined(LED_GREEN)
  turnOffShutdownLed(LED_GREEN);
#endif
}

} // namespace mesh_nrf52
