#pragma once

#include <WiFi.h>
#include <esp_wifi.h>

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
inline esp_err_t applyStationPowerSave(wifi_ps_type_t mode) {
  const esp_err_t result = esp_wifi_set_ps(mode);
  if (result != ESP_OK) return result;
  return esp_wifi_set_max_tx_power(static_cast<int8_t>(stationTransmitPower(mode)));
}

}  // namespace wifi
}  // namespace mesh
