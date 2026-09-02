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

namespace detail {

// Keep the identity-marker reconnect rule pure so host tests can exercise the
// DTR edge case without a TinyUSB device. The USB owner task passes the actual
// TinyUSB write result here. A short write means the old connection vanished
// mid-record; byte zero is the only safe starting point for the next host.
inline size_t nextUsbLoggingIdentityOffset(size_t current_offset,
                                           size_t requested,
                                           size_t written) {
  return written == requested ? current_offset + written : 0;
}

}  // namespace detail

// Ordinary merged images start enabled. USB/Full Companion starts disabled to
// protect framed traffic and restores its saved choice after preferences load.
bool isUsbLoggingEnabled();
void setUsbLoggingEnabled(bool enabled);

// Kept as a platform-neutral persistence hook. Single-TTY builds need no
// descriptor mirror; nRF52 selects its optional second interface after loading
// the normal role preferences.
bool saveUsbLoggingBootPreference(bool enabled);

// Start the optional dedicated USB logging interface. Ordinary and single-TTY
// builds use Serial; nRF52 Full Companion uses a second CDC ACM port.
void beginUsbLoggingPort();
// Enable owner-task service when a host opens the dedicated logging endpoint.
// That task emits the identity marker from the actual TinyUSB write result and
// retries a busy CDC TX FIFO without waiting.
// The device can report its USB interface, but the host alone chooses names
// such as /dev/ttyACM1 or COM7.
void serviceUsbLoggingPort();
Stream& usbLoggingPort();
// Primary USB Companion data stream. On nRF52 this uses one direct TinyUSB
// FIFO attempt per write so a host-side open/close race cannot trap the main
// loop in Adafruit_USBD_CDC::write(). Other platforms retain Serial.
Stream& usbCompanionPort();
// Serial mOTA requests are binary records of at most 11 bytes. On nRF52 this
// facade admits a request only when the complete record fits in CDC0's current
// TX capacity, so a retry can never append to a prefix from the prior attempt.
Stream& usbMotaPort();
// Functional ASCII output needs the same zero-wait USB primitive plus a
// bounded queue so a normal host receives complete multi-line replies. Service
// it from the application loop; discard it before changing the CDC protocol or
// after a host disconnect so stale text cannot prefix a later Binary session.
Stream& usbTerminalPort();
void serviceUsbTerminalPort();
void discardUsbTerminalOutput();
bool hasPendingUsbTerminalOutput();
// Consume an exact CDC0 DTR-low edge captured in TinyUSB's owner task. This is
// separate from polling the current DTR value: Windows can close and reopen a
// COM handle between two application-loop iterations.
bool takeUsbTerminalSessionReset();
// After the application has reset its protocol state, atomically purge any
// CDC0 writer that raced the close callback and reopen the producer gate. A
// false result is a zero-wait busy indication; retry it next loop.
bool tryCompleteUsbTerminalSessionReset();
bool hasDedicatedUsbLoggingPort();
bool isDedicatedUsbLoggingPortConfigured();
bool usbLoggingInterfaceRestartRequired();
const char* usbLoggingPortDescription();

}  // namespace mesh
#endif
