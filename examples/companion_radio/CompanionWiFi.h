#pragma once

#include <stdint.h>

#if defined(ESP32) && defined(WIFI_SSID)

enum class CompanionWiFiPowerSaveResult : uint8_t {
  Applied,
  SavedForNextConnection,
  InvalidMode,
  BluetoothConflict,
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
// The returned mode is always the effective mode: WiFi+BLE builds map a stale
// or default "none" value to "min" because modem sleep is required for radio
// coexistence.
uint8_t getCompanionWiFiPowerSave();
const char* getCompanionWiFiPowerSaveName();
const char* companionWiFiPowerSaveName(uint8_t mode);
CompanionWiFiPowerSaveResult setCompanionWiFiPowerSave(uint8_t mode);
void reloadCompanionWiFiPowerSave();
bool applyCompanionWiFiPowerSave();

#endif
