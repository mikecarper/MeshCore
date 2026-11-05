#pragma once

#include <limits.h>
#include <string.h>
#include <WiFi.h>
#include <helpers/esp32/WiFiRadioPolicy.h>
#if defined(WIFI_TX_POWER_DBM)
#include <helpers/esp32/WiFiStationPower.h>
#endif
#include <helpers/esp32/WiFiStationDiagnostics.h>
#include <helpers/esp32/WiFiStationSecurity.h>
#include <helpers/WirelessControl.h>
#if defined(MESH_SOAK_WIFI_KEY_OVERRIDE)
#include "../../../tools/hil/S3SoakWiFiKey.h"
#endif

namespace mesh {
namespace wifi {

// Driver-managed reconnect is intentionally disabled for a primary ESP-NOW
// radio. ESP-IDF treats wifi_sta_config_t::channel as a scan-start hint, so an
// unconstrained reconnect could associate on another channel and silently
// drag ESP-NOW (and a setup AP) with it. Existing application retry timers call
// beginStation() and therefore retain the fixed-channel/BSSID constraint.
inline void setStationAutoReconnect(bool enabled) {
  WiFi.setAutoReconnect(enabled && !espNowChannelConstrained());
}

inline wl_status_t beginStation(const char* ssid, const char* password) {
  beginStationDiagnostics(ssid);
  if (mesh::wireless::control().blocked(mesh::wireless::WiFi)) {
    noteStationDiagnosticPhase(ConnectionPhase::Blocked);
    return WL_CONNECT_FAILED;
  }
  if (!ssid || !ssid[0]) {
    noteStationDiagnosticPhase(ConnectionPhase::NoSSID);
    return WL_CONNECT_FAILED;
  }
#if defined(MESH_SOAK_WIFI_KEY_OVERRIDE)
  password = mesh::hil::soakStationPassword(password);
#endif
  // Reapply the saved manual setting or the board's automatic default before
  // association. A driver that has not started yet must not block joining.
#if defined(WIFI_TX_POWER_DBM)
  const esp_err_t tx_result = applyStationTxPower();
  if (tx_result != ESP_OK && tx_result != ESP_ERR_WIFI_NOT_STARTED
      && tx_result != ESP_ERR_WIFI_NOT_INIT) {
    noteStationDiagnosticPhase(ConnectionPhase::PolicyFailed);
  }
#endif
  if (!espNowChannelConstrained()) {
    return beginStationWithSecurity(ssid, password);
  }

#if (defined(MESH_PRIMARY_ESPNOW) && MESH_PRIMARY_ESPNOW) || defined(WITH_ESPNOW_BRIDGE)
  setStationAutoReconnect(false);
  if (applyProtocolMask(WIFI_IF_STA) != ESP_OK
      || restoreEspNowChannel() != ESP_OK) {
    noteStationDiagnosticPhase(ConnectionPhase::PolicyFailed);
    return WL_CONNECT_FAILED;
  }

  // Do not collide with WebConfig's asynchronous SSID scan. The caller's
  // normal retry path will try again after that scan completes.
  const int16_t prior_scan = WiFi.scanComplete();
  if (prior_scan == WIFI_SCAN_RUNNING) {
    noteStationDiagnosticPhase(ConnectionPhase::ScanBusy);
    return WL_IDLE_STATUS;
  }
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
    noteStationDiagnosticPhase(ConnectionPhase::APNotOnChannel);
    WiFi.scanDelete();
    restoreEspNowChannel();
    return WL_NO_SSID_AVAIL;
  }

  const uint8_t* selected_bssid = WiFi.BSSID(best);
  if (!selected_bssid) {
    noteStationDiagnosticPhase(ConnectionPhase::PolicyFailed);
    WiFi.scanDelete();
    restoreEspNowChannel();
    return WL_CONNECT_FAILED;
  }
  uint8_t bssid[6];
  memcpy(bssid, selected_bssid, sizeof(bssid));
  WiFi.scanDelete();
  return beginStationWithSecurity(ssid, password, channel, bssid);
#else
  noteStationDiagnosticPhase(ConnectionPhase::PolicyFailed);
  return WL_CONNECT_FAILED;
#endif
}

// Returns false after dropping an association which moved away from the mesh
// channel (for example after an AP channel-switch announcement). Callers then
// follow their existing disconnected/retry path.
inline bool enforceStationChannel() {
  if (!espNowChannelConstrained()) return true;
  // beginStation(), disconnect handling, and setup-AP startup already restore
  // the selected channel. Avoid polling the driver and attempting a channel
  // write from every WebConfig and Companion loop while it is disconnected.
  if (WiFi.status() != WL_CONNECTED) return true;
  if (WiFi.channel() == activeEspNowChannel()) return true;
  noteStationDiagnosticPhase(ConnectionPhase::PolicyFailed);
  WiFi.disconnect(false, false);
  restoreEspNowChannel();
  return false;
}

} // namespace wifi
} // namespace mesh
