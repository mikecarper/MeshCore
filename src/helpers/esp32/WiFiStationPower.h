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
  return esp_wifi_set_max_tx_power(stationPowerSaveTxPower(mode));
}

}  // namespace wifi
}  // namespace mesh
