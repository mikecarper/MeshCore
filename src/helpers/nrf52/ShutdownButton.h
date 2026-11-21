#pragma once

#include <Arduino.h>
#include <nrf_gpio.h>

namespace mesh { namespace nrf52 {

// Normally the next press wakes the device. If the button stays held through
// shutdown, arm the opposite level so SYSTEMOFF lasts until it is released.
inline void armShutdownButton(uint32_t pin, int pressed, nrf_gpio_pin_pull_t pull) {
  const uint32_t started = millis();
  while (digitalRead(pin) == pressed && millis() - started < 2000u) {
    delay(10);
  }
  const auto sense = digitalRead(pin) == HIGH ? NRF_GPIO_PIN_SENSE_LOW : NRF_GPIO_PIN_SENSE_HIGH;
  nrf_gpio_cfg_sense_input(g_ADigitalPinMap[pin], pull, sense);
}

}}  // namespace mesh::nrf52
