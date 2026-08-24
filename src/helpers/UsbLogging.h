#pragma once

// Logging artifacts compile at least one of these two diagnostics. Keep the
// command surface out of ordinary images while providing one runtime gate for
// every diagnostic category that writes to the USB Serial stream.
#if defined(ARDUINO) && \
    ((defined(MESH_DEBUG) && MESH_DEBUG) || \
     (defined(MESH_PACKET_LOGGING) && MESH_PACKET_LOGGING))
  #define MESH_USB_LOGGING_AVAILABLE 1
#else
  #define MESH_USB_LOGGING_AVAILABLE 0
#endif

#if defined(ARDUINO)
class Stream;

namespace mesh {

// Ordinary logging images start enabled. Dual-CDC Full Companion starts with
// logging disabled and restores its saved choice after preferences load.
bool isUsbLoggingEnabled();
void setUsbLoggingEnabled(bool enabled);

// ESP32 native USB starts before setup(), so its next-boot interface count is
// mirrored outside the normal role preferences. Other platforms need no
// mirror. Returns false only when that mirror could not be saved.
bool saveUsbLoggingBootPreference(bool enabled);

// Start the optional dedicated USB logging interface. Ordinary builds keep
// using Serial. Supported Full Companion builds expose a second CDC ACM
// interface so plaintext diagnostics never share the framed Companion stream.
void beginUsbLoggingPort();
Stream& usbLoggingPort();
bool hasDedicatedUsbLoggingPort();
bool isDedicatedUsbLoggingPortConfigured();
bool usbLoggingInterfaceRestartRequired();

}  // namespace mesh
#endif
