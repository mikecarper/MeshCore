#if defined(RP2040_PLATFORM) || defined(STM32_PLATFORM)
#include <Arduino.h>
#include <target.h>
#include "CustomPeripheralConfig.h"
#include "RuntimePinClaims.h"
#if MESH_CUSTOM_PERIPHERALS
#include "PeripheralPinPolicy.h"
#endif

bool mesh::MainBoard::isUserGpioAvailable(uint8_t pin) const {
#if MESH_CUSTOM_PERIPHERALS
  return isPeripheralOutputAvailable(pin)
      && !mesh::pins::claimedByOther(peripheralPinIdentity(pin), mesh::pins::Owner::UserGpio);
#else
  (void)pin;
  return false;
#endif
}

bool mesh::MainBoard::isPeripheralOutputAvailable(uint8_t pin) const {
#if MESH_CUSTOM_PERIPHERALS
  return PeripheralPinPolicy::available(pin, PeripheralPinPolicy::Use::Output);
#else
  (void)pin;
  return false;
#endif
}

bool mesh::MainBoard::isPeripheralPinAvailable(uint8_t pin, bool pullup) const {
#if MESH_CUSTOM_PERIPHERALS
  return PeripheralPinPolicy::available(pin, pullup ? PeripheralPinPolicy::Use::InputPullup
                                                 : PeripheralPinPolicy::Use::Input);
#else
  (void)pin;
  (void)pullup;
  return false;
#endif
}

uint16_t mesh::MainBoard::peripheralPinIdentity(uint8_t pin) const {
#if MESH_CUSTOM_PERIPHERALS
  const uint32_t physical = PeripheralPinPolicy::physicalPin(pin);
  return physical < UINT16_MAX ? static_cast<uint16_t>(physical) : UINT16_MAX;
#else
  return pin;
#endif
}

bool mesh::MainBoard::isPeripheralButtonAvailable(uint8_t pin, bool pullup) const {
#if MESH_CUSTOM_PERIPHERALS
  return PeripheralPinPolicy::availableButton(pin, pullup);
#else
  (void)pin;
  (void)pullup;
  return false;
#endif
}
#endif
