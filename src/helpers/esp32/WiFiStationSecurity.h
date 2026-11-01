#pragma once

#include <string.h>
#include <WiFi.h>
#include <helpers/esp32/WiFiStationDiagnostics.h>

#if (defined(CONFIG_ESP32_WIFI_ENABLE_WPA3_SAE) && CONFIG_ESP32_WIFI_ENABLE_WPA3_SAE) \
    || (defined(CONFIG_ESP_WIFI_ENABLE_WPA3_SAE) && CONFIG_ESP_WIFI_ENABLE_WPA3_SAE)
#include <esp_idf_version.h>
#include <esp_wifi.h>
// These releases corrupt SAE during the configuration read/write round trip
// used by Arduino's no-argument begin() (Espressif advisory AR2026-003).
#if ESP_IDF_VERSION >= ESP_IDF_VERSION_VAL(4, 4, 3) \
    && ESP_IDF_VERSION != ESP_IDF_VERSION_VAL(4, 4, 5) \
    && ESP_IDF_VERSION != ESP_IDF_VERSION_VAL(5, 0, 2) \
    && ESP_IDF_VERSION != ESP_IDF_VERSION_VAL(5, 0, 3) \
    && ESP_IDF_VERSION != ESP_IDF_VERSION_VAL(5, 1, 0)
#define MESH_WIFI_HAS_SAE_PWE 1
#endif
#endif

namespace mesh {
namespace wifi {

#if defined(MESH_WIFI_HAS_SAE_PWE)
namespace station_security_detail {

inline bool fieldMatches(const uint8_t* field, size_t capacity,
                         const char* value, size_t length) {
  return length <= capacity && strnlen(reinterpret_cast<const char*>(field), capacity) == length
      && (length == 0 || memcmp(field, value, length) == 0);
}

inline bool configurationMatches(const wifi_sta_config_t& config,
                                 const char* ssid, size_t ssid_length,
                                 const char* password, size_t password_length,
                                 int32_t channel, const uint8_t* bssid) {
  return fieldMatches(config.ssid, sizeof(config.ssid), ssid, ssid_length)
      && fieldMatches(config.password, sizeof(config.password), password, password_length)
      // An unconstrained join accepts a live channel remembered by the SDK.
      && (!channel || config.channel == channel) && bool(config.bssid_set) == bool(bssid)
      && (!bssid || memcmp(config.bssid, bssid, sizeof(config.bssid)) == 0);
}

inline bool saeConfigured(const wifi_sta_config_t& config) {
  if (config.sae_pwe_h2e != WPA3_SAE_PWE_BOTH) return false;
#if defined(SAE_H2E_IDENTIFIER_LEN)
  // MeshCore has no password-identifier setting. Do not reuse an identifier
  // left in NVS by an older SDK (Espressif advisory AR2026-003).
  for (const auto byte : config.sae_h2e_identifier) if (byte != 0) return false;
#endif
  return true;
}

inline wl_status_t failed() {
  noteStationDiagnosticPhase(ConnectionPhase::PolicyFailed);
  return WL_CONNECT_FAILED;
}

} // namespace station_security_detail
#endif

inline wl_status_t beginStationWithSecurity(const char* ssid, const char* password,
                                           int32_t channel = 0,
                                           const uint8_t* bssid = nullptr) {
#if defined(MESH_WIFI_HAS_SAE_PWE)
  using namespace station_security_detail;
  const size_t ssid_length = ssid ? strnlen(ssid, 33) : 0;
  const size_t password_length = password ? strnlen(password, 65) : 0;
  if (!ssid_length || ssid_length > 32 || password_length > 64
      || channel < 0 || channel > 14) return failed();

  wifi_config_t config = {};
  if (WiFi.status() == WL_CONNECTED
      && esp_wifi_get_config(WIFI_IF_STA, &config) == ESP_OK
      && configurationMatches(config.sta, ssid, ssid_length, password, password_length,
                              channel, bssid)
      && saeConfigured(config.sta)) {
    wifi_ap_record_t access_point = {};
    if (esp_wifi_sta_get_ap_info(&access_point) == ESP_OK
        && fieldMatches(access_point.ssid, sizeof(access_point.ssid), ssid, ssid_length)
        && (!channel || access_point.primary == channel)
        && (!bssid || memcmp(access_point.bssid, bssid, sizeof(access_point.bssid)) == 0)) {
      return WL_CONNECTED;
    }
  }

  // Arduino's credential overload zeroes the SAE setting on every call. Stage
  // its normal credentials, PMF, scan and IP settings without starting a join.
  // Its return value is cached status, even after successful staging, so verify
  // the resulting configuration instead of treating that value as success.
  (void)WiFi.begin(ssid, password, channel, bssid, false);
  if (esp_wifi_get_config(WIFI_IF_STA, &config) != ESP_OK
      || !configurationMatches(config.sta, ssid, ssid_length, password, password_length,
                               channel, bssid)) return failed();

  // Explicitly allow H2E and Hunt-and-Peck before the first authentication
  // frame. The SDK default excludes H2E-only APs on IDF 4.4.8 (AR2026-003).
  config.sta.sae_pwe_h2e = WPA3_SAE_PWE_BOTH;
#if defined(SAE_H2E_IDENTIFIER_LEN)
  memset(config.sta.sae_h2e_identifier, 0, sizeof(config.sta.sae_h2e_identifier));
#endif
  if (esp_wifi_set_config(WIFI_IF_STA, &config) != ESP_OK) return failed();

  // This overload preserves the configured SAE method and Arduino's DHCP /
  // static-IP handling. A queued disconnect can leave its cached status at
  // CONNECTED, in which case it skips connecting; explicitly restart that join.
  const wl_status_t result = WiFi.begin();
  if (result == WL_CONNECTED) {
    if (!WiFi.reconnect()) return failed();
    return WL_IDLE_STATUS;
  }
  return result;
#else
  return WiFi.begin(ssid, password, channel, bssid);
#endif
}

} // namespace wifi
} // namespace mesh

#undef MESH_WIFI_HAS_SAE_PWE
