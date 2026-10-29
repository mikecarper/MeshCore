#pragma once

#include <stddef.h>
#include <string.h>
#include <type_traits>

namespace mesh {

// Describe the primary Serial backend selected by this application build,
// not its board name, host port name, USB VID/PID or optional logging CDC.
// Include after Arduino.h. The caller supplies decltype(Serial), after any
// board-specific alias is visible, so a custom primary UART cannot inherit a
// native-USB claim merely because the unused USB peripheral has those flags.
template <typename SerialType, bool WithPrefix = false>
inline const char* usbModeName() {
#if defined(ESP32) || defined(ESP32_PLATFORM)
  if (std::is_same<SerialType, HardwareSerial>::value)
    return WithPrefix ? "usb.mode uart" : "uart";
  #if defined(ARDUINO_USB_CDC_ON_BOOT) && ARDUINO_USB_CDC_ON_BOOT
    #if defined(ARDUINO_USB_MODE) && ARDUINO_USB_MODE == 1
      if (std::is_same<SerialType, HWCDC>::value)
        return WithPrefix ? "usb.mode hardware-cdc-jtag" : "hardware-cdc-jtag";
    #elif defined(ARDUINO_USB_MODE) && ARDUINO_USB_MODE == 0
      if (std::is_same<SerialType, USBCDC>::value)
        return WithPrefix ? "usb.mode tinyusb" : "tinyusb";
    #endif
  #endif
#elif defined(NRF52_PLATFORM) && defined(USE_TINYUSB)
  if (std::is_same<SerialType, Adafruit_USBD_CDC>::value)
    return WithPrefix ? "usb.mode tinyusb" : "tinyusb";
#endif
  return WithPrefix ? "usb.mode unknown" : "unknown";
}

inline bool isUsbModeCommand(const char* command) {
  return command != nullptr && strcmp(command, "get usb.mode") == 0;
}

// Read-only, bounded and independent of diagnostic/logging build flags.
// No setter: changing a USB backend requires installing another application.
template <typename SerialType>
#if defined(__GNUC__)
__attribute__((noinline))
#endif
bool handleUsbModeCommand(const char* command, char* reply,
                                 size_t capacity, const SerialType&) {
  if (!isUsbModeCommand(command) || reply == nullptr || capacity == 0) return false;
  const char* response = usbModeName<SerialType, true>();
  size_t length = strlen(response);
  if (length >= capacity) length = capacity - 1;
  memcpy(reply, response, length);
  reply[length] = 0;
  return true;
}

}  // namespace mesh
