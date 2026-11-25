#include "UsbDataLock.h"

#if defined(NRF52_PLATFORM) || defined(ESP32)
#include <Arduino.h>
#include <stdio.h>
#include <string.h>

#if defined(NRF52_PLATFORM)
#include <InternalFileSystem.h>
extern "C" int mesh_lfs_set_usb_lock(lfs_t*, bool);
#else
#include <nvs.h>
#include <nvs_flash.h>
#include <soc/soc_caps.h>
#if SOC_USB_SERIAL_JTAG_SUPPORTED
#include <hal/usb_serial_jtag_ll.h>
#include <esp_idf_version.h>
#endif
#if defined(ARDUINO_USB_MODE) && ARDUINO_USB_MODE == 0 && CONFIG_TINYUSB_ENABLED
#include <esp32-hal-tinyusb.h>
#endif
#endif

namespace mesh {
namespace {
#if defined(NRF52_PLATFORM)
constexpr char marker[] = "MCULOCK1";

const char* scope() {
  // OTAFIX reserves the final two pages for MBR parameters and settings.
  // Check only the bootloader bank so the application's own filename cannot match.
  const uint32_t limit = NRF_FICR->CODEPAGESIZE * NRF_FICR->CODESIZE - 8192;
  uint32_t start = NRF_UICR->NRFFW[0];
  if (start == UINT32_MAX) start = *reinterpret_cast<const uint32_t*>(0xff8);
  if (!(start & 4095) && start >= limit - 0x10000 && start < limit) {
    for (uint32_t address = start; address + 8 <= limit; address += 4)
      if (!memcmp(reinterpret_cast<const void*>(address), marker, 8)) return "app+DFU";
  }
  return "app only";
}

bool copyLocked(unsigned copy) {
#if defined(NRF52840_XXAA)
  const uintptr_t start = 0xed000;
#else
  const uintptr_t start = 0x6d000;
#endif
  const auto* bytes = reinterpret_cast<const volatile uint8_t*>(start + 128 * copy + 52);
  for (unsigned i = 0; i < sizeof(marker) - 1; ++i) {
    if (bytes[i] != marker[i]) return false;
  }
  return true;
}
bool readSetting(bool& locked) {
  locked = copyLocked(0) || copyLocked(1);
  return true;
}
bool loadBootSetting() {
  return copyLocked(0) || copyLocked(1);
}
bool saveSetting(bool locked) {
  if (!InternalFS.Adafruit_LittleFS::begin()) return false;
  InternalFS._lockFS();
  const int rc = mesh_lfs_set_usb_lock(InternalFS._getFS(), locked);
  InternalFS._unlockFS();
  return rc == 0 && copyLocked(0) == locked && copyLocked(1) == locked;
}
#else
const char* scope() { return "app only"; }
constexpr char settingsNamespace[] = "mesh-usb";
constexpr char key[] = "locked";
bool readSetting(bool& locked) {
  nvs_handle_t handle;
  uint8_t value = 0;
  esp_err_t rc = nvs_open(settingsNamespace, NVS_READONLY, &handle);
  if (rc == ESP_OK) {
    rc = nvs_get_u8(handle, key, &value);
    nvs_close(handle);
  }
  locked = value == 1;
  return rc == ESP_ERR_NVS_NOT_FOUND || (rc == ESP_OK && value <= 1);
}
bool loadBootSetting() {
  bool locked;
  return !readSetting(locked) || locked;
}
bool saveSetting(bool locked) {
  bool saved;
  if (readSetting(saved) && saved == locked) return true;
  nvs_handle_t handle;
  if (nvs_open(settingsNamespace, NVS_READWRITE, &handle) != ESP_OK) return false;
  const bool ok = nvs_set_u8(handle, key, locked) == ESP_OK && nvs_commit(handle) == ESP_OK;
  nvs_close(handle);
  return ok && readSetting(saved) && saved == locked;
}
#endif
}  // namespace

#if defined(NRF52_PLATFORM)
// The SoftDevice can deliver USB power events from its interrupt handler.
static volatile bool activeLock = true;
bool usbDataLocked() { return activeLock; }
extern "C" bool meshNrfUsbPowerLocked() { return activeLock; }
#else
bool usbDataLocked() {
  static const bool locked = loadBootSetting();
  return locked;
}
#endif

void applyUsbDataLockAtBoot() {
#if defined(NRF52_PLATFORM)
  activeLock = loadBootSetting();
#elif defined(ESP32)
  if (!usbDataLocked()) return;
  Serial.end();
#if defined(ARDUINO_USB_MODE) && ARDUINO_USB_MODE == 0 && CONFIG_TINYUSB_ENABLED
  if (tud_inited()) tud_disconnect();
#endif
#if SOC_USB_SERIAL_JTAG_SUPPORTED
#if ESP_IDF_VERSION_MAJOR >= 5
  usb_serial_jtag_ll_phy_enable_pad(false);
#else
  usb_serial_jtag_ll_enable_pad(false);
#endif
#endif
#endif
}

bool handleUsbDataLockCommand(const char* command, char* reply, size_t capacity) {
  if (strcmp(command, "get usb.lock") == 0) {
    bool saved;
    if (readSetting(saved)) {
      snprintf(reply, capacity, "usb.lock %s (%s)%s", saved ? "on" : "off", scope(),
               saved == usbDataLocked() ? "" : " (pending reboot)");
    } else snprintf(reply, capacity, "Error: USB lock storage unavailable");
  } else if (strncmp(command, "set usb.lock ", 13) == 0 || strcmp(command, "set usb.lock") == 0) {
    const bool locked = strcmp(command, "set usb.lock on") == 0;
    if (!locked && strcmp(command, "set usb.lock off") != 0) {
      snprintf(reply, capacity, "Error: use set usb.lock on|off");
    } else if (!saveSetting(locked)) {
      snprintf(reply, capacity, "Error: USB lock save unconfirmed");
    } else {
      snprintf(reply, capacity, "OK - usb.lock %s (%s)%s", locked ? "on" : "off", scope(),
               locked == usbDataLocked() ? "" : " - reboot to apply");
    }
  } else return false;
  return true;
}
#if defined(ESP32) && defined(ARDUINO_USB_MODE) && ARDUINO_USB_MODE == 0
// May run before initArduino(). Defer to the normal startup recovery if
// NVS is unavailable and keep USB detached for this attempt.
extern "C" bool meshAllowNativeUsbStartup() {
  if (nvs_flash_init() != ESP_OK) return false;
  return !usbDataLocked();
}
#endif
}  // namespace mesh

#endif
