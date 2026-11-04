#pragma once

#include <WiFi.h>
#include <esp_wifi.h>

#include <helpers/esp32/WiFiTxPowerPolicy.h>

namespace mesh {
namespace wifi {

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

// Restore normal transmit power when modem sleep is disabled, including after
// a live settings change or reconnect. The SDK enforces its radio limits.
//
// esp_wifi_set_max_tx_power() replaces the ceiling rather than raising it, so
// the modem-sleep value must never exceed a profile's WIFI_TX_POWER_DBM cap.
// Without the clamp, every association and every power-save change silently
// reopens the ceiling the profile deliberately closed: a board capped at 13 dBm
// that runs with power save off gets 19.5 dBm back on the next connect event.
inline int8_t stationPowerSaveTxPower(wifi_ps_type_t mode) {
  int8_t power = static_cast<int8_t>(stationTransmitPower(mode));
#if defined(WIFI_TX_POWER_DBM)
  const int8_t cap = configuredTxPowerUnits();
  if (power > cap) power = cap;
#endif
  return power;
}

inline esp_err_t applyStationPowerSave(wifi_ps_type_t mode) {
  const esp_err_t result = esp_wifi_set_ps(mode);
  if (result != ESP_OK) return result;
  const int8_t power = stationPowerSaveTxPower(mode);
  const esp_err_t tx_result = esp_wifi_set_max_tx_power(power);
  if (tx_result != ESP_OK) return tx_result;
#if defined(WIFI_TX_POWER_DBM)
  // Read back the ceiling: a driver that silently ignores the write would leave
  // the profile's cap open. The SDK stores the raw request and floors the PHY to
  // its own table, so "no higher than requested" is the ceiling in effect.
  int8_t applied = 0;
  if (esp_wifi_get_max_tx_power(&applied) != ESP_OK) return ESP_ERR_INVALID_STATE;
  return mesh::wifi::txCeilingSatisfied(applied, power) ? ESP_OK : ESP_ERR_INVALID_STATE;
#endif
  return ESP_OK;
}

}  // namespace wifi
}  // namespace mesh
