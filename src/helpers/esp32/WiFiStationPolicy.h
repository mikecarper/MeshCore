#pragma once

#include <limits.h>
#include <string.h>
#include <WiFi.h>
#include <helpers/esp32/WiFiRadioPolicy.h>

namespace mesh {
namespace wifi {

// Driver-managed reconnect is intentionally disabled for a primary ESP-NOW
// radio. ESP-IDF treats wifi_sta_config_t::channel as a scan-start hint, so an
// unconstrained reconnect could associate on another channel and silently
// drag ESP-NOW (and a setup AP) with it. Existing application retry timers call
// beginStation() and therefore retain the fixed-channel/BSSID constraint.
inline void setStationAutoReconnect(bool enabled) {
  WiFi.setAutoReconnect(enabled && !kPrimaryEspNowRadio);
}

inline wl_status_t beginStation(const char* ssid, const char* password) {
  if (!kPrimaryEspNowRadio) {
    return WiFi.begin(ssid, password);
  }

#if defined(MESH_PRIMARY_ESPNOW) && MESH_PRIMARY_ESPNOW
  if (!ssid || !ssid[0]) return WL_CONNECT_FAILED;
  setStationAutoReconnect(false);
  if (applyProtocolMask(WIFI_IF_STA) != ESP_OK
      || restoreEspNowChannel() != ESP_OK) {
    return WL_CONNECT_FAILED;
  }

  // Do not collide with WebConfig's asynchronous SSID scan. The caller's
  // normal retry path will try again after that scan completes.
  const int16_t prior_scan = WiFi.scanComplete();
  if (prior_scan == WIFI_SCAN_RUNNING) return WL_IDLE_STATUS;
  if (prior_scan >= 0) WiFi.scanDelete();

  const uint8_t channel = activeEspNowChannel();
  const int16_t count = WiFi.scanNetworks(
      false, true, false, 300, channel, ssid, nullptr);
  int best = -1;
  int32_t best_rssi = INT32_MIN;
  for (int i = 0; i < count; i++) {
    if (WiFi.channel(i) != channel || WiFi.SSID(i) != ssid) continue;
    const int32_t rssi = WiFi.RSSI(i);
    if (best < 0 || rssi > best_rssi) {
      best = i;
      best_rssi = rssi;
    }
  }

  if (best < 0) {
    WiFi.scanDelete();
    restoreEspNowChannel();
    return WL_NO_SSID_AVAIL;
  }

  const uint8_t* selected_bssid = WiFi.BSSID(best);
  if (!selected_bssid) {
    WiFi.scanDelete();
    restoreEspNowChannel();
    return WL_CONNECT_FAILED;
  }
  uint8_t bssid[6];
  memcpy(bssid, selected_bssid, sizeof(bssid));
  WiFi.scanDelete();
  return WiFi.begin(ssid, password, channel, bssid);
#else
  return WL_CONNECT_FAILED;
#endif
}

// Returns false after dropping an association which moved away from the mesh
// channel (for example after an AP channel-switch announcement). Callers then
// follow their existing disconnected/retry path.
inline bool enforceStationChannel() {
  if (!kPrimaryEspNowRadio) return true;
  // beginStation(), disconnect handling, and setup-AP startup already restore
  // the selected channel. Avoid polling the driver and attempting a channel
  // write from every WebConfig and Companion loop while it is disconnected.
  if (WiFi.status() != WL_CONNECTED) return true;
  if (WiFi.channel() == activeEspNowChannel()) return true;
  WiFi.disconnect(false, false);
  restoreEspNowChannel();
  return false;
}

} // namespace wifi
} // namespace mesh
