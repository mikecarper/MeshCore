#include "UsbLogging.h"

#if defined(ARDUINO)
#include <Arduino.h>
#include <atomic>
#include <new>

#if defined(MESH_DUAL_CDC_LOGGING)
  #if !defined(COMPANION_FEATURE_DEDICATED_USB_LOGGING) || \
      !COMPANION_FEATURE_DEDICATED_USB_LOGGING
    #error "MESH_DUAL_CDC_LOGGING requires its dedicated Companion capability"
  #endif
  #if defined(NRF52_PLATFORM)
    #define MESH_NRF52_DUAL_CDC_LOGGING 1
    #include <Adafruit_TinyUSB.h>
    #if !defined(CFG_TUD_CDC) || CFG_TUD_CDC < 2
      #error "MESH_DUAL_CDC_LOGGING requires CFG_TUD_CDC >= 2"
    #endif
  #elif defined(ESP32_PLATFORM) && defined(SOC_USB_OTG_SUPPORTED) && \
      SOC_USB_OTG_SUPPORTED && !ARDUINO_USB_MODE
    #define MESH_ESP32_DUAL_CDC_LOGGING 1
    #include <USB.h>
    #include <USBCDC.h>
    #include <nvs.h>
    #include <nvs_flash.h>
    #if !defined(CONFIG_TINYUSB_CDC_MAX_PORTS) || \
        CONFIG_TINYUSB_CDC_MAX_PORTS < 2
      #error "MESH_DUAL_CDC_LOGGING requires two ESP32 TinyUSB CDC ports"
    #endif
  #else
    #error "MESH_DUAL_CDC_LOGGING requires nRF52 or ESP32 native TinyUSB"
  #endif
#endif

namespace mesh {

static std::atomic<bool> usb_logging_enabled{true};
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

#if defined(MESH_DUAL_CDC_LOGGING)
static bool dedicated_usb_logging_port_configured = false;
static bool dedicated_usb_logging_port_started = false;
#if defined(MESH_NRF52_DUAL_CDC_LOGGING)
static Adafruit_USBD_CDC dedicated_usb_logging_port;
#elif defined(MESH_ESP32_DUAL_CDC_LOGGING)
static constexpr char USB_LOGGING_NVS_NAMESPACE[] = "mesh_usb";
static constexpr char USB_LOGGING_NVS_KEY[] = "log_port";
alignas(USBCDC) static uint8_t dedicated_usb_logging_port_storage[
    sizeof(USBCDC)];
static USBCDC* dedicated_usb_logging_port = nullptr;

static bool readEsp32UsbLoggingBootPreference() {
  // Missing or unreadable state must stay protocol-safe: Full Companion then
  // exposes only its primary framed CDC interface. The normal preference load
  // below mirrors an explicitly enabled setting for the following reboot.
  if (nvs_flash_init() != ESP_OK) return false;

  nvs_handle_t handle;
  if (nvs_open(USB_LOGGING_NVS_NAMESPACE, NVS_READONLY, &handle) != ESP_OK) {
    return false;
  }

  uint8_t value = 0;
  const esp_err_t result = nvs_get_u8(handle, USB_LOGGING_NVS_KEY, &value);
  nvs_close(handle);
  return result == ESP_OK && value != 0;
}

static bool writeEsp32UsbLoggingBootPreference(bool enabled) {
  if (nvs_flash_init() != ESP_OK) return false;

  nvs_handle_t handle;
  if (nvs_open(USB_LOGGING_NVS_NAMESPACE, NVS_READWRITE, &handle) != ESP_OK) {
    return false;
  }

  uint8_t current = 0;
  esp_err_t result = nvs_get_u8(handle, USB_LOGGING_NVS_KEY, &current);
  if (result == ESP_OK && current == (enabled ? 1 : 0)) {
    nvs_close(handle);
    return true;
  }
  result = nvs_set_u8(handle, USB_LOGGING_NVS_KEY, enabled ? 1 : 0);
  if (result == ESP_OK) result = nvs_commit(handle);
  nvs_close(handle);
  return result == ESP_OK;
}

class Esp32UsbLoggingBootstrap {
 public:
  Esp32UsbLoggingBootstrap() {
    const bool enabled = readEsp32UsbLoggingBootPreference();
    usb_logging_enabled.store(enabled, std::memory_order_relaxed);
    usb_logging_preference_known.store(true, std::memory_order_relaxed);
    if (!enabled) return;

    dedicated_usb_logging_port = new (dedicated_usb_logging_port_storage)
        USBCDC(1);
    dedicated_usb_logging_port_configured = true;
  }
};

static Esp32UsbLoggingBootstrap esp32_usb_logging_bootstrap;
#endif
#endif

bool isUsbLoggingEnabled() {
  return usb_logging_enabled.load(std::memory_order_relaxed);
}

void setUsbLoggingEnabled(bool enabled) {
  usb_logging_enabled.store(enabled, std::memory_order_relaxed);
  usb_logging_preference_known.store(true, std::memory_order_relaxed);
}

bool saveUsbLoggingBootPreference(bool enabled) {
#if defined(MESH_ESP32_DUAL_CDC_LOGGING)
  return writeEsp32UsbLoggingBootPreference(enabled);
#else
  (void)enabled;
  return true;
#endif
}

void beginUsbLoggingPort() {
#if defined(MESH_DUAL_CDC_LOGGING)
  if (dedicated_usb_logging_port_started
      || !usb_logging_preference_known.load(std::memory_order_relaxed)
      || !isUsbLoggingEnabled()) {
    return;
  }

#if defined(MESH_ESP32_DUAL_CDC_LOGGING)
  // ESP32 registers its descriptor before app_main starts native USB. If the
  // saved boot mirror omitted CDC 1, enabling logging takes effect after the
  // next reboot instead of trying to mutate a live USB descriptor.
  if (dedicated_usb_logging_port == nullptr) return;
  // Only the Companion interface may use the CDC control-line reboot gesture.
  // A terminal opening or closing the diagnostics interface must never reboot
  // the node or put it into the ROM downloader.
  dedicated_usb_logging_port->enableReboot(false);
  dedicated_usb_logging_port->begin(115200);
#elif defined(MESH_NRF52_DUAL_CDC_LOGGING)
  dedicated_usb_logging_port.begin(115200);
  dedicated_usb_logging_port_configured = true;
#endif
  dedicated_usb_logging_port_started = true;

#if defined(MESH_NRF52_DUAL_CDC_LOGGING)
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
#endif
}

Stream& usbLoggingPort() {
#if defined(MESH_DUAL_CDC_LOGGING)
#if defined(MESH_NRF52_DUAL_CDC_LOGGING)
  if (isUsbLoggingEnabled() && dedicated_usb_logging_port_started) {
    return dedicated_usb_logging_port;
  }
#elif defined(MESH_ESP32_DUAL_CDC_LOGGING)
  if (isUsbLoggingEnabled() && dedicated_usb_logging_port_started
      && dedicated_usb_logging_port != nullptr) {
    return *dedicated_usb_logging_port;
  }
#endif
  return null_usb_logging_stream;
#else
  if (!isUsbLoggingEnabled()) return null_usb_logging_stream;
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

}  // namespace mesh
#endif
