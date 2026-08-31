#pragma once

#include <stdint.h>

#if defined(COMPANION_EXCLUSIVE_WIFI_BLE) \
    && !(defined(ESP32) && defined(WIFI_SSID) && defined(BLE_PIN_CODE))
#error "COMPANION_EXCLUSIVE_WIFI_BLE requires ESP32, WIFI_SSID, and BLE_PIN_CODE"
#endif

#if defined(ESP32) && defined(WIFI_SSID)

#if defined(COMPANION_EXCLUSIVE_WIFI_BLE)
enum class CompanionTransportMode : uint8_t {
  Bluetooth,
  WiFi,
};

// The selected transport is persisted immediately but is deliberately applied
// only on the next boot. This keeps Bluetooth memory release irreversible only
// within the boot that selected exclusive WiFi.
CompanionTransportMode getCompanionTransportMode();
bool selectCompanionTransportMode(CompanionTransportMode mode);
#endif

enum class CompanionWiFiPowerSaveResult : uint8_t {
  Applied,
  SavedForNextConnection,
  InvalidMode,
  BluetoothConflict,
  PrimaryEspNowConflict,
  StorageError,
};

// The requested state is persisted immediately. Network services transition
// from the main loop so a button callback never tears down an active server.
bool toggleCompanionWiFi();
bool isCompanionWiFiEnabled();

// Reload saved credentials after a text-terminal update. The reconnect is
// deferred so the command reply can leave USB/TCP before WiFi is restarted.
void scheduleCompanionWiFiCredentialReload();

// Companion WiFi power save is stored in the shared mesh-wifi NVS namespace.
// The returned mode is always the effective mode. WiFi+BLE builds map a stale
// or default "none" value to "min" because modem sleep is required for radio
// coexistence. Primary ESP-NOW builds also map stale "max" to "min" so the
// receiver does not sleep through unbuffered ESP-NOW broadcasts.
uint8_t getCompanionWiFiPowerSave();
const char* getCompanionWiFiPowerSaveName();
const char* companionWiFiPowerSaveName(uint8_t mode);
CompanionWiFiPowerSaveResult setCompanionWiFiPowerSave(uint8_t mode);
void reloadCompanionWiFiPowerSave();
bool applyCompanionWiFiPowerSave();

#endif
