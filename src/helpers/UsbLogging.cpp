#include "UsbLogging.h"

#if defined(ARDUINO)
#include <Arduino.h>
#include <atomic>

#if defined(NRF52_PLATFORM)
  #include "NonBlockingWriteStream.h"
#endif

#if defined(MESH_DUAL_CDC_LOGGING)
  #if !defined(COMPANION_FEATURE_DEDICATED_USB_LOGGING) || \
      !COMPANION_FEATURE_DEDICATED_USB_LOGGING
    #error "MESH_DUAL_CDC_LOGGING requires its dedicated Companion capability"
  #endif
  #if !defined(NRF52_PLATFORM)
    #error "MESH_DUAL_CDC_LOGGING is supported only by nRF52 Full Companion"
  #endif
  #include <Adafruit_TinyUSB.h>
  #if !defined(CFG_TUD_CDC) || CFG_TUD_CDC < 2
    #error "MESH_DUAL_CDC_LOGGING requires CFG_TUD_CDC >= 2"
  #endif
#endif

namespace mesh {

#if defined(ENABLE_USB_INTERFACE)
// A USB Companion owns its primary stream for framed traffic until saved
// preferences are loaded. Starting disabled prevents early boot diagnostics
// from corrupting that stream before single-TTY builds can enter terminal mode.
static std::atomic<bool> usb_logging_enabled{false};
#else
static std::atomic<bool> usb_logging_enabled{true};
#endif
static std::atomic<bool> usb_logging_preference_known{false};

class NullUsbLoggingStream : public Stream {
 public:
  int available() override { return 0; }
  int read() override { return -1; }
  int peek() override { return -1; }
  void flush() override {}
  size_t write(uint8_t) override { return 1; }
  size_t write(const uint8_t*, size_t size) override { return size; }
};

static NullUsbLoggingStream null_usb_logging_stream;

static void setPlatformDebugOutputEnabled(bool enabled) {
#if defined(ESP32_PLATFORM) && defined(ENABLE_USB_INTERFACE)
  // Arduino-ESP32 log_e()/ESP-IDF diagnostics otherwise write straight to
  // the same UART/CDC stream used by Binary Companion. Keep that low-level
  // route under the same runtime gate as MeshCore's own diagnostics.
  Serial.setDebugOutput(enabled);
#else
  (void)enabled;
#endif
}

#if defined(MESH_DUAL_CDC_LOGGING)
static bool dedicated_usb_logging_port_configured = false;
static bool dedicated_usb_logging_port_started = false;
static bool dedicated_usb_logging_port_connected = false;
static Adafruit_USBD_CDC dedicated_usb_logging_port;
static WholeRecordNonBlockingStream<>
    nonblocking_dedicated_usb_logging_port(dedicated_usb_logging_port);
#elif defined(NRF52_PLATFORM)
// Single-CDC nRF52 roles need the same protection. In particular, BLE debug
// callbacks and packet logging write through usbLoggingPort() without going
// through MeshCore's formatted-debug helper.
static WholeRecordNonBlockingStream<>
    nonblocking_primary_usb_logging_port(Serial);
#endif

bool isUsbLoggingEnabled() {
  return usb_logging_enabled.load(std::memory_order_relaxed);
}

void setUsbLoggingEnabled(bool enabled) {
  usb_logging_enabled.store(enabled, std::memory_order_relaxed);
  usb_logging_preference_known.store(true, std::memory_order_relaxed);
  setPlatformDebugOutputEnabled(enabled);
}

bool saveUsbLoggingBootPreference(bool enabled) {
  (void)enabled;
  return true;
}

void beginUsbLoggingPort() {
  // setup() calls this once before role preferences are loaded and again
  // afterwards. The first call silences framework diagnostics on a protected
  // ESP32 Companion stream; setUsbLoggingEnabled() restores them only when the
  // saved setting explicitly enables logging.
  setPlatformDebugOutputEnabled(isUsbLoggingEnabled());
#if defined(MESH_DUAL_CDC_LOGGING)
  if (dedicated_usb_logging_port_started
      || !usb_logging_preference_known.load(std::memory_order_relaxed)
      || !isUsbLoggingEnabled()) {
    return;
  }

  dedicated_usb_logging_port.begin(115200);
  // `begin()` installs the core's generic "TinyUSB Serial" name. Replace it
  // before (re-)enumeration so host USB tools can distinguish the log endpoint.
  dedicated_usb_logging_port.setStringDescriptor("MeshCore Logging");
  dedicated_usb_logging_port_configured = true;
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

void serviceUsbLoggingPort() {
#if defined(MESH_DUAL_CDC_LOGGING)
  bool connected = false;
  connected = dedicated_usb_logging_port_started
      && dedicated_usb_logging_port.dtr();

  if (connected && !dedicated_usb_logging_port_connected
      && isUsbLoggingEnabled()) {
    Stream& port = usbLoggingPort();
    port.println("MeshCore USB logging port");
    port.println("USB CDC 1; interface 02; Linux stable suffix: -if02");
  }
  dedicated_usb_logging_port_connected = connected;
#endif
}

Stream& usbLoggingPort() {
#if defined(MESH_DUAL_CDC_LOGGING)
  if (isUsbLoggingEnabled() && dedicated_usb_logging_port_started) {
    return nonblocking_dedicated_usb_logging_port;
  }
  return null_usb_logging_stream;
#else
  if (!isUsbLoggingEnabled()) return null_usb_logging_stream;
  #if defined(NRF52_PLATFORM)
    return nonblocking_primary_usb_logging_port;
  #else
    return Serial;
  #endif
#endif
}

bool hasDedicatedUsbLoggingPort() {
#if defined(MESH_DUAL_CDC_LOGGING)
  return true;
#else
  return false;
#endif
}

bool isDedicatedUsbLoggingPortConfigured() {
#if defined(MESH_DUAL_CDC_LOGGING)
  return dedicated_usb_logging_port_configured;
#else
  return false;
#endif
}

bool usbLoggingInterfaceRestartRequired() {
#if defined(MESH_DUAL_CDC_LOGGING)
  return dedicated_usb_logging_port_configured != isUsbLoggingEnabled();
#else
  return false;
#endif
}

const char* usbLoggingPortDescription() {
#if defined(MESH_DUAL_CDC_LOGGING)
  return "dedicated USB CDC 1, interface 02 (Linux: *-if02; tty/COM name is host-assigned)";
#else
  return "primary USB serial port (tty/COM name is host-assigned)";
#endif
}

}  // namespace mesh
#endif
