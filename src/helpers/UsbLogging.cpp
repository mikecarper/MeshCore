#include "UsbLogging.h"

#if defined(ARDUINO)
#include <Arduino.h>
#include <atomic>

#if defined(MESH_DUAL_CDC_LOGGING)
  #if !defined(NRF52_PLATFORM) || !defined(COMPANION_RADIO_FULL)
    #error "MESH_DUAL_CDC_LOGGING is only supported by nRF52 Full Companion"
  #endif
  #if !defined(CFG_TUD_CDC) || CFG_TUD_CDC < 2
    #error "MESH_DUAL_CDC_LOGGING requires CFG_TUD_CDC >= 2"
  #endif
  #include <Adafruit_TinyUSB.h>
#endif

namespace mesh {

static std::atomic<bool> usb_logging_enabled{true};

#if defined(MESH_DUAL_CDC_LOGGING)
static Adafruit_USBD_CDC dedicated_usb_logging_port;
static bool dedicated_usb_logging_port_started = false;
#endif

bool isUsbLoggingEnabled() {
  return usb_logging_enabled.load(std::memory_order_relaxed);
}

void setUsbLoggingEnabled(bool enabled) {
  usb_logging_enabled.store(enabled, std::memory_order_relaxed);
}

void beginUsbLoggingPort() {
#if defined(MESH_DUAL_CDC_LOGGING)
  if (dedicated_usb_logging_port_started) return;

  dedicated_usb_logging_port.begin(115200);
  dedicated_usb_logging_port_started = true;

  // The nRF52 core starts TinyUSB before setup(). If the host completed
  // enumeration in that small window, reconnect once so it reads the expanded
  // two-CDC descriptor. Usually enumeration has not completed and no reconnect
  // is needed.
  if (TinyUSBDevice.mounted()) {
    TinyUSBDevice.detach();
    delay(10);
    TinyUSBDevice.attach();
  }
#endif
}

Stream& usbLoggingPort() {
#if defined(MESH_DUAL_CDC_LOGGING)
  return dedicated_usb_logging_port;
#else
  return Serial;
#endif
}

bool hasDedicatedUsbLoggingPort() {
#if defined(MESH_DUAL_CDC_LOGGING)
  return true;
#else
  return false;
#endif
}

}  // namespace mesh
#endif
