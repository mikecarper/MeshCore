#pragma once

// Canonical USB-capable images compile at least one of these two diagnostics.
// One runtime gate covers every diagnostic category that writes to the USB
// Serial stream, so a separate logging firmware is unnecessary.
#if defined(ARDUINO) && \
    ((defined(MESH_DEBUG) && MESH_DEBUG) || \
     (defined(MESH_PACKET_LOGGING) && MESH_PACKET_LOGGING))
  #define MESH_USB_LOGGING_AVAILABLE 1
#else
  #define MESH_USB_LOGGING_AVAILABLE 0
#endif

#if defined(ARDUINO)
#include <Arduino.h>

namespace mesh {

// Ordinary merged images start enabled. USB/Full Companion starts disabled to
// protect framed traffic and restores its saved choice after preferences load.
bool isUsbLoggingEnabled();
void setUsbLoggingEnabled(bool enabled);

// ESP32 native USB starts before setup(), so its next-boot interface count is
// mirrored outside the normal role preferences. Other platforms need no
// mirror. Returns false only when that mirror could not be saved.
bool saveUsbLoggingBootPreference(bool enabled);

// Start the optional dedicated USB logging interface. Ordinary and single-TTY
// builds use Serial; dual-CDC Full Companion builds use a second CDC ACM port.
void beginUsbLoggingPort();
Stream& usbLoggingPort();
bool hasDedicatedUsbLoggingPort();
bool isDedicatedUsbLoggingPortConfigured();
bool usbLoggingInterfaceRestartRequired();

}  // namespace mesh
#endif
