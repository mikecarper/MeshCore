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
namespace mesh {

// This is intentionally session-only. Logging artifacts start enabled after
// every boot so a stale setting cannot make a diagnostic image look silent.
bool isUsbLoggingEnabled();
void setUsbLoggingEnabled(bool enabled);

}  // namespace mesh
#endif
