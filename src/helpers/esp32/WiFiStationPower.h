#pragma once

#include <WiFi.h>
#include <atomic>
#include <esp_wifi.h>
#include <nvs.h>
#include "WiFiTxPowerPolicy.h"

namespace mesh {
namespace wifi {

// Zero means the existing automatic policy; other values are in quarter dBm,
// as accepted by esp_wifi_set_max_tx_power(). The driver and country policy
// may quantize or limit the actual value, which can be read back separately.
static constexpr uint8_t kWifiTxPowerAuto = 0;
static constexpr uint8_t kWifiTxPowerMin = 8;
static constexpr uint8_t kWifiTxPowerMax = 84;

inline bool validStationTxPower(uint8_t value) {
  return value == kWifiTxPowerAuto || (value >= kWifiTxPowerMin && value <= kWifiTxPowerMax);
}

// This separate namespace works for canonical Wi-Fi, older MQTT credentials,
// setup APs and ESP-NOW without altering their existing serialized formats.
inline bool readStationTxPower(uint8_t& value) {
  nvs_handle_t handle;
  const esp_err_t open = nvs_open("mesh-wifi-tx", NVS_READONLY, &handle);
  if (open == ESP_ERR_NVS_NOT_FOUND) { value = kWifiTxPowerAuto; return true; }
  if (open != ESP_OK) return false;
  uint8_t saved = kWifiTxPowerAuto;
  const esp_err_t read = nvs_get_u8(handle, "power", &saved);
  nvs_close(handle);
  if (read != ESP_OK && read != ESP_ERR_NVS_NOT_FOUND) return false;
  if (!validStationTxPower(saved)) return false;
  value = saved;
  return true;
}

inline bool saveStationTxPower(uint8_t value) {
  if (!validStationTxPower(value)) return false;
  nvs_handle_t handle;
  if (nvs_open("mesh-wifi-tx", NVS_READWRITE, &handle) != ESP_OK) return false;
  uint8_t verify = 255;
  const bool ok = nvs_set_u8(handle, "power", value) == ESP_OK
      && nvs_commit(handle) == ESP_OK && nvs_get_u8(handle, "power", &verify) == ESP_OK && verify == value;
  nvs_close(handle);
  return ok;
}

// A primary ESP-NOW radio has its own saved `set tx` value. Remember it while
// active so changing wifi.tx back to auto can immediately restore that value.
inline std::atomic<int8_t>& primaryEspNowAutomaticTxPower() {
  static std::atomic<int8_t> value{-1};
  return value;
}

inline void setPrimaryEspNowAutomaticTxPower(int8_t value) {
  primaryEspNowAutomaticTxPower().store(value);
}

// A saved manual setting is a radio-wide ceiling, including ESP-NOW. In auto
// mode retain the requesting subsystem's existing power-selection policy.
inline esp_err_t applyRequestedStationTxPower(int8_t automatic_power) {
  uint8_t saved = kWifiTxPowerAuto;
  if (!readStationTxPower(saved)) return ESP_FAIL;
  return esp_wifi_set_max_tx_power(saved == kWifiTxPowerAuto ? automatic_power : static_cast<int8_t>(saved));
}

// AP startup did not previously select station power in automatic mode. Only
// apply a user override here so a fresh setup AP keeps its former defaults.
inline esp_err_t applyManualStationTxPower() {
  uint8_t saved = kWifiTxPowerAuto;
  if (!readStationTxPower(saved)) return ESP_FAIL;
  return saved == kWifiTxPowerAuto ? ESP_OK : esp_wifi_set_max_tx_power(static_cast<int8_t>(saved));
}

inline wifi_power_t stationTransmitPower(wifi_ps_type_t mode) {
  if (mode != WIFI_PS_NONE) {
#ifdef MQTT_WIFI_POWER_SAVE_TX_POWER
    return MQTT_WIFI_POWER_SAVE_TX_POWER;
#else
    return WIFI_POWER_11dBm;
#endif
  }
#ifdef MQTT_WIFI_TX_POWER
  return MQTT_WIFI_TX_POWER;
#else
  return WIFI_POWER_19_5dBm;
#endif
}

// The board profile is an automatic default. An explicit `wifi.tx` setting
// remains authoritative, including when it is higher than the default.
inline int8_t stationPowerSaveTxPower(wifi_ps_type_t mode) {
  int8_t value = static_cast<int8_t>(stationTransmitPower(mode));
#if defined(WIFI_TX_POWER_DBM)
  const int8_t cap = configuredTxPowerUnits();
  if (value > cap) value = cap;
#endif
  return value;
}

// Restore normal transmit power when modem sleep is disabled, including after
// a live settings change or reconnect. The SDK enforces its radio limits.
inline esp_err_t applyStationPowerSave(wifi_ps_type_t mode) {
  const esp_err_t result = esp_wifi_set_ps(mode);
  if (result != ESP_OK) return result;
  return applyRequestedStationTxPower(stationPowerSaveTxPower(mode));
}

// Apply a new preference without changing the current power-saving mode.
// The caller can save it for later if the Wi-Fi driver has not started yet.
inline esp_err_t applyStationTxPower() {
  wifi_ps_type_t mode;
  const esp_err_t result = esp_wifi_get_ps(&mode);
  if (result != ESP_OK) return result;
  const int8_t espnow = primaryEspNowAutomaticTxPower().load();
  const int8_t fallback = espnow >= 0 ? espnow : stationPowerSaveTxPower(mode);
  return applyRequestedStationTxPower(fallback);
}

}  // namespace wifi
}  // namespace mesh
