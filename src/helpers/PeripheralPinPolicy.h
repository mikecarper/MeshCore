#pragma once

#include <stdint.h>
#include "UserGpioPinPolicy.h"

// Include after Arduino.h and target.h. These are Arduino pin numbers (not
// raw nRF port numbers or STM32 PinNames). Board allowlists can only narrow
// valid pins; they never override flash, firmware, or system reservations.
namespace PeripheralPinPolicy {

enum class Use : uint8_t { Input, InputPullup, Output };
enum class Rejection : uint8_t {
  None, InvalidPin, NotExposed, SystemReserved, FirmwareReserved, NoOutput, NoPullup
};

inline uint32_t physicalPin(uint8_t pin) {
#if defined(NRF52_PLATFORM) && defined(PINS_COUNT)
  return pin < PINS_COUNT && g_ADigitalPinMap[pin] < 48
      ? g_ADigitalPinMap[pin] : UINT32_MAX;
#elif defined(STM32_PLATFORM) && defined(NUM_DIGITAL_PINS)
  return pin < NUM_DIGITAL_PINS && digitalPinToPinName(pin) != NC
      ? static_cast<uint32_t>(digitalPinToPinName(pin)) : UINT32_MAX;
#else
  return pin;
#endif
}

inline bool samePhysicalPin(uint8_t first, uint8_t second) {
  const uint32_t physical = physicalPin(first);
  return physical != UINT32_MAX && physical == physicalPin(second);
}

inline bool isMapped(uint8_t pin) {
#if defined(ESP32_PLATFORM)
  return digitalPinIsValid(pin);
#elif defined(NRF52_PLATFORM) && defined(PINS_COUNT)
  return physicalPin(pin) != UINT32_MAX;
#elif defined(RP2040_PLATFORM)
  // RP2040 GPIO30+ are not normal GPIOs (the flash interface is separate).
  return pin < 30;
#elif defined(STM32_PLATFORM) && defined(NUM_DIGITAL_PINS)
  return physicalPin(pin) != UINT32_MAX;
#else
  (void)pin;
  return false;
#endif
}

inline bool isSystemReserved(uint8_t pin) {
#if defined(ESP32_PLATFORM)
#if defined(CONFIG_IDF_TARGET_ESP32)
  if ((pin >= 6 && pin <= 11) || pin == 0 || pin == 2 || pin == 5
      || (pin >= 12 && pin <= 15)) return true;
#ifdef BOARD_HAS_PSRAM
  if (pin == 16 || pin == 17) return true;
#endif
#elif defined(CONFIG_IDF_TARGET_ESP32S2)
  if ((pin >= 26 && pin <= 32) || (pin >= 39 && pin <= 42)
      || pin == 0 || pin == 45 || pin == 46 || pin == 19 || pin == 20) return true;
#elif defined(CONFIG_IDF_TARGET_ESP32S3)
  if ((pin >= 26 && pin <= 32) || (pin >= 39 && pin <= 42)
      || pin == 0 || pin == 3 || pin == 45 || pin == 46
      || pin == 19 || pin == 20) return true;
#if defined(CONFIG_SPIRAM_MODE_OCT) || defined(CONFIG_ESPTOOLPY_OCT_FLASH)
  if (pin >= 33 && pin <= 37) return true;
#endif
#elif defined(CONFIG_IDF_TARGET_ESP32C3)
  if ((pin >= 12 && pin <= 17) || (pin >= 4 && pin <= 7)
      || pin == 2 || pin == 8 || pin == 9 || pin == 18 || pin == 19) return true;
#elif defined(CONFIG_IDF_TARGET_ESP32C6)
  if ((pin >= 24 && pin <= 30) || (pin >= 4 && pin <= 9)
      || pin == 15 || pin == 12 || pin == 13) return true;
#else
  // New ESP32 families need an explicit system-pin map before enabling them.
  return true;
#endif
#if !defined(ARDUINO_USB_CDC_ON_BOOT) || !ARDUINO_USB_CDC_ON_BOOT
  // With native USB CDC, Serial and the local console do not own UART0's
  // default pads. Explicit serial/bridge/GPS assignments remain reserved by
  // the target policy even in USB builds.
  if (pin == TX || pin == RX) return true;
#endif
#if !defined(PIN_BOARD_SDA) || !defined(PIN_BOARD_SCL)
  if (pin == SDA || pin == SCL) return true;
#endif
#elif defined(NRF52_PLATFORM)
  const uint32_t pad = physicalPin(pin);
  // LF crystal, NFC antenna and reset pads are not accessory connections.
  if (pad == 0 || pad == 1 || pad == 9 || pad == 10 || pad == 18) return true;
#if defined(RAK_3401) || defined(RAK_4631)
  // GPS detection toggles IO4/5; IO2 controls the switched peripheral rail.
  if (samePhysicalPin(pin, WB_IO2) || samePhysicalPin(pin, WB_IO4)
      || samePhysicalPin(pin, WB_IO5)) return true;
#endif
#elif defined(STM32_PLATFORM)
  // Keep debug, USB, clock and integrated-radio connections unavailable even
  // on variants which expose every MCU pad in their Arduino map.
  static const int64_t system[] = {
    -1,
#ifdef PA13
    PA13,
#endif
#ifdef PA14
    PA14,
#endif
#ifdef PA11
    PA11,
#endif
#ifdef PA12
    PA12,
#endif
#ifdef PC14
    PC14,
#endif
#ifdef PC15
    PC15,
#endif
#ifdef PH0
    PH0,
#endif
#ifdef PH1
    PH1,
#endif
#ifdef PH3
    PH3,
#endif
#if defined(STM32WLxx)
#ifdef PA4
    PA4,
#endif
#ifdef PA5
    PA5,
#endif
#ifdef PA6
    PA6,
#endif
#ifdef PA7
    PA7,
#endif
#ifdef PB0
    PB0,
#endif
#endif
  };
  for (size_t i = 0; i < sizeof(system) / sizeof(system[0]); ++i) {
    if (system[i] >= 0 && system[i] < UINT8_MAX
        && samePhysicalPin(pin, static_cast<uint8_t>(system[i]))) return true;
  }
#elif defined(RP2040_PLATFORM)
  // The usable header maps below exclude flash, wireless and power nets.
  (void)pin;
#else
  (void)pin;
  return true;
#endif
  return false;
}

inline bool isExposed(uint8_t pin) {
#if defined(USER_GPIO_ALLOWED_PINS)
  // Explicit board customization: comma-separated Arduino header pins.
  static const uint8_t exposed[] = { USER_GPIO_ALLOWED_PINS };
#elif defined(ESP32_PLATFORM) && defined(STATION_G2)
  // Preserve StationG2Board's IO extension socket restriction for inputs and
  // static output validation as well as the ordinary GPIO commands.
  static const uint8_t exposed[] = { 8, 9, 16, 17, 18, 39 };
#elif defined(ESP32_PLATFORM) && (defined(M5STACK_CARDPUTER_ADV) \
    || defined(MESH_SENSECAP_INDICATOR) || defined(SEEED_WIO_TRACKER_L2) || defined(LILYGO_TDECK))
  // Integrated keyboard/RGB/audio boards have internal wiring beyond the
  // ordinary target macros. Require an audited expansion-header allowlist.
  (void)pin;
  return false;
#elif defined(ESP32_PLATFORM)
  // ESP32 uses SoC GPIO numbering; system and target-owned pins are removed
  // separately, as in the existing GPIO command policy.
  (void)pin;
  return true;
#elif defined(NRF52_PLATFORM) && defined(HELTEC_T096)
  static const uint8_t exposed[] = {
    2, 4, 7, 8, 9, 10, 13, 15, 17, 20, 22, 23, 24, 25, 27, 29, 31,
    32, 33, 34, 35, 36, 37, 38, 39, 42, 43, 45, 47
  };
#elif defined(NRF52_PLATFORM) && defined(PROMICRO)
  return pin <= 17;
#elif defined(NRF52_PLATFORM) && (defined(XIAO_NRF52) || defined(IKOKA_NRF52))
  return pin <= 10;
#elif defined(NRF52_PLATFORM) && (defined(RAK_3401) || defined(RAK_4631))
  static const uint8_t exposed[] = {
    2, 3, 4, 5, 9, 10, 13, 14, 15, 16, 17, 19, 20, 21, 24, 25,
    26, 28, 29, 30, 31, 33, 34
  };
#elif defined(RP2040_PLATFORM) && defined(ARDUINO_SEEED_XIAO_RP2040)
  static const uint8_t exposed[] = { 26, 27, 28, 29, 6, 7, 0, 1, 2, 4, 3 };
#elif defined(RP2040_PLATFORM) && defined(RAK_11310)
  // RAK11300 WisBlock interface signals. Other pads connect to the radio,
  // LEDs, battery divider or board power and are not expansion pins.
  static const uint8_t exposed[] = {
    0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 16, 17, 18, 19, 20, 21, 22, 27, 28
  };
#elif defined(RP2040_PLATFORM) && (defined(ARDUINO_RASPBERRY_PI_PICO) \
    || defined(ARDUINO_RASPBERRY_PI_PICO_W))
  return pin <= 22 || (pin >= 26 && pin <= 28);
#elif defined(STM32_PLATFORM) && defined(MESH_WIO_E5_MINI)
  // Mini's labeled header nets; SWD and its boot button remain locked out.
  return pin <= 18;
#else
  (void)pin;
  return false;
#endif

#if defined(USER_GPIO_ALLOWED_PINS) \
    || (defined(ESP32_PLATFORM) && defined(STATION_G2)) \
    || (defined(NRF52_PLATFORM) && (defined(HELTEC_T096) || defined(RAK_3401) || defined(RAK_4631)) \
        && !defined(PROMICRO) && !defined(XIAO_NRF52) && !defined(IKOKA_NRF52)) \
    || (defined(RP2040_PLATFORM) && (defined(ARDUINO_SEEED_XIAO_RP2040) || defined(RAK_11310)))
  for (size_t i = 0; i < sizeof(exposed) / sizeof(exposed[0]); ++i) {
    // Only the documented Arduino number is exposed. An undocumented alias
    // must not expand this list even when it happens to map to the same pad.
    if (pin == exposed[i]) return true;
  }
  return false;
#endif
}

inline Rejection rejection(uint8_t pin, Use use) {
  if (!isMapped(pin)) return Rejection::InvalidPin;
  if (isSystemReserved(pin)) return Rejection::SystemReserved;
  if (UserGpioPinPolicy::isFirmwareReserved(pin)) return Rejection::FirmwareReserved;
  if (!isExposed(pin)) return Rejection::NotExposed;
#if defined(ESP32_PLATFORM)
  if (use == Use::Output && !digitalPinCanOutput(pin)) return Rejection::NoOutput;
#if defined(CONFIG_IDF_TARGET_ESP32)
  // GPIO34..39 are usable inputs only with external pull resistors.
  if (use == Use::InputPullup && pin >= 34 && pin <= 39) return Rejection::NoPullup;
#endif
#else
  (void)use;
#endif
  return Rejection::None;
}

inline bool available(uint8_t pin, Use use) {
  return rejection(pin, use) == Rejection::None;
}

inline Rejection buttonRejection(uint8_t pin, bool pullup = true) {
#ifdef PIN_USER_BTN
  const int64_t user_button = PIN_USER_BTN;
  if (user_button >= 0 && user_button < UINT8_MAX
      && pin == static_cast<uint8_t>(user_button)) {
    if (!isMapped(pin)) return Rejection::InvalidPin;
#if defined(USER_BTN_PRESSED)
    // The custom encoder switch is active low. A native active-high button
    // cannot share that input until the encoder supports switch polarity.
    if (USER_BTN_PRESSED != LOW) return Rejection::FirmwareReserved;
#endif
    if (UserGpioPinPolicy::isFirmwareReserved(pin, true)) return Rejection::FirmwareReserved;
#if defined(ESP32_PLATFORM) && defined(CONFIG_IDF_TARGET_ESP32)
    if (pullup && pin >= 34 && pin <= 39) return Rejection::NoPullup;
#else
    (void)pullup;
#endif
    // Existing buttons may intentionally use strapping pins, and do not need
    // to appear in an expansion-header allowlist.
    return Rejection::None;
  }
#endif
  return rejection(pin, pullup ? Use::InputPullup : Use::Input);
}

inline bool availableButton(uint8_t pin, bool pullup = true) {
  return buttonRejection(pin, pullup) == Rejection::None;
}

inline const char* reason(Rejection rejected) {
  switch (rejected) {
    case Rejection::None: return "available";
    case Rejection::InvalidPin: return "invalid or unmapped pin";
    case Rejection::NotExposed: return "pin is not a supported expansion pin";
    case Rejection::SystemReserved: return "pin is reserved for boot, memory, USB, debug or power";
    case Rejection::FirmwareReserved: return "pin is used by board hardware";
    case Rejection::NoOutput: return "pin does not support output";
    case Rejection::NoPullup: return "pin requires an external pull-up";
  }
  return "pin is unavailable";
}

}  // namespace PeripheralPinPolicy
