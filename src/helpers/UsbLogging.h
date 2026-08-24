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

// Logging starts enabled on first boot. Roles with saved preferences restore
// the persisted setting after their preferences are loaded.
bool isUsbLoggingEnabled();
void setUsbLoggingEnabled(bool enabled);

// Start the optional dedicated USB logging interface. Ordinary builds keep
// using Serial. nRF52 Full Companion builds expose a second CDC ACM interface
// so plaintext diagnostics never share the framed Companion stream.
void beginUsbLoggingPort();
Stream& usbLoggingPort();
bool hasDedicatedUsbLoggingPort();

}  // namespace mesh
#endif
