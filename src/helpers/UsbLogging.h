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

// ESP32-S2/S3 native USB CDC uses USBCDC, not the hardware USB-Serial-JTAG
// driver. Its write timeout only bounds a mutex, not waiting for FIFO space.
// All roles sharing this CDC therefore need the single-attempt transport.
#if defined(ESP32) && defined(ARDUINO_USB_MODE) && ARDUINO_USB_MODE == 0 \
    && defined(ARDUINO_USB_CDC_ON_BOOT) && ARDUINO_USB_CDC_ON_BOOT
  #define MESH_ESP32_TINYUSB_NONBLOCKING 1
#else
  #define MESH_ESP32_TINYUSB_NONBLOCKING 0
#endif

// ESP32-S3 USB-Serial/JTAG (HWCDC) has a software TX ring which can retain a
// previous host's bytes across a USB bus reset. This capability is independent
// of the Companion role: room, repeater, and sensor consoles need the same
// session gate and bounded writes.
#if defined(ESP32) && defined(ARDUINO_USB_MODE) && ARDUINO_USB_MODE == 1 \
    && defined(ARDUINO_USB_CDC_ON_BOOT) && ARDUINO_USB_CDC_ON_BOOT
  #define MESH_ESP32_HWCDC_SESSION_GUARD 1
#else
  #define MESH_ESP32_HWCDC_SESSION_GUARD 0
#endif

#if MESH_ESP32_TINYUSB_NONBLOCKING || MESH_ESP32_HWCDC_SESSION_GUARD
  #define MESH_ESP32_USB_CONSOLE_COOPERATIVE 1
#else
  #define MESH_ESP32_USB_CONSOLE_COOPERATIVE 0
#endif

namespace mesh {

#ifndef MESH_ESP32_USB_TX_BUFFER_SIZE
  #define MESH_ESP32_USB_TX_BUFFER_SIZE 4096
#endif

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

// Record the HWCDC TX ring size actually allocated during early setup. A zero
// value keeps reset cleanup quarantined and eligible for a minimum-size retry
// instead of mistaking an allocation failure for a permanently non-empty ring.
void setUsbCompanionTxBufferCapacity(size_t capacity);

// Configure ESP32 HWCDC's bounded write timeout and TX ring before Serial.begin
// creates its mutex and enables the USB ISR. Safe as a no-op on other ports.
void prepareUsbLoggingPort();

// Start the optional dedicated USB logging interface. Ordinary and single-TTY
// builds use Serial; nRF52 Full Companion uses a second CDC ACM port. Call this
// after Serial.begin() so native USB roles also register session events.
void beginUsbLoggingPort();
// Enable owner-task service when a host opens the dedicated logging endpoint.
// That task emits the identity marker from the actual TinyUSB write result and
// retries a busy CDC TX FIFO without waiting.
// The device can report its USB interface, but the host alone chooses names
// such as /dev/ttyACM1 or COM7.
void serviceUsbLoggingPort();
Stream& usbLoggingPort();
// Primary USB Companion data stream. Native TinyUSB on nRF52 and ESP32 uses
// one FIFO attempt per write, never the framework's wait-for-space loop.
// Callers retain and retry unwritten suffixes. Other platforms retain Serial
// or their hardware-CDC session facade.
Stream& usbCompanionPort();
// Serial mOTA requests are binary records of at most 11 bytes. On TinyUSB this
// facade admits a request only when the complete record fits in CDC0's current
// TX capacity, so a retry can never append to a prefix from the prior attempt.
Stream& usbMotaPort();
// Functional ASCII output needs the same zero-wait USB primitive plus a
// bounded queue so a normal host receives complete multi-line replies. Service
// it from the application loop; discard it before changing the CDC protocol or
// after a host disconnect so stale text cannot prefix a later Binary session.
Stream& usbTerminalPort();
// Repeater/room-server/sensor console: protect both ESP32 native USB transports
// while preserving the historical raw Serial behavior of other platforms.
Stream& usbConsolePort();
// Diagnostic backlog alone must not starve console input. Native ESP32 CDC
// admits another command when its reserved functional capacity is available;
// other platforms preserve their existing command-processing policy.
bool canAcceptUsbConsoleCommand();
void serviceUsbTerminalPort();
void discardUsbTerminalOutput();
bool hasPendingUsbTerminalOutput();
// Bytes refused while an ESP32 TinyUSB terminal host was connected. A visible
// overflow marker is emitted once queue capacity recovers; other ports return 0.
uint32_t usbTerminalDroppedBytes();
// Consume a primary-USB session boundary reported by the USB owner task:
// CDC0 DTR-low on TinyUSB, or a hardware CDC bus reset on ESP32. This is
// independent of polling current line/SOF state; ESP32 retains that poll as a
// fallback because its bundled framework event queue is finite.
bool takeUsbTerminalSessionReset();
// After the application has reset its protocol state, atomically purge any
// CDC0 writer that raced the close callback and reopen the producer gate. A
// false result is a zero-wait busy indication; retry it next loop.
bool tryCompleteUsbTerminalSessionReset();
// Purge platform USB-driver bytes at a confirmed host-session boundary. On
// ESP32 HWCDC this briefly detaches the PHY and gates diagnostics so stale RX
// or TX data cannot cross into a newly enumerated host session. A false result
// leaves that quarantine intact and should be retried from a later loop.
bool resetUsbCompanionTransport();
bool hasDedicatedUsbLoggingPort();
bool isDedicatedUsbLoggingPortConfigured();
bool usbLoggingInterfaceRestartRequired();
const char* usbLoggingPortDescription();

}  // namespace mesh
#endif
