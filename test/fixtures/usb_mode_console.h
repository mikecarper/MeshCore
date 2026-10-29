#pragma once

// Dispatch fixtures have no Arduino console, but must retain the real USB-mode
// handler. Model its backend types separately from each fixture's framed reply
// transport; no console reads, writes or USB peripherals are needed here.
struct HardwareSerial {};
struct HWCDC {};
struct USBCDC {};
struct Adafruit_USBD_CDC {};
#if (defined(ESP32) || defined(ESP32_PLATFORM)) \
    && defined(ARDUINO_USB_CDC_ON_BOOT) && ARDUINO_USB_CDC_ON_BOOT
  #if defined(ARDUINO_USB_MODE) && ARDUINO_USB_MODE == 1
inline HWCDC Serial;
  #else
inline USBCDC Serial;
  #endif
#elif defined(NRF52_PLATFORM) && defined(USE_TINYUSB)
inline Adafruit_USBD_CDC Serial;
#else
inline HardwareSerial Serial;
#endif

#include <helpers/UsbMode.h>
