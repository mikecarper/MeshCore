#pragma once

#include <WiFi.h>
#include <helpers/esp32/WiFiRadioPolicy.h>
#include <helpers/esp32/ManagementPassword.h>
#include <string.h>

namespace mesh {
namespace wifi {

inline bool accessPointStarted() {
#if ESP_ARDUINO_VERSION_MAJOR >= 3
  return WiFi.AP.started();
#else
  return (WiFi.getStatusBits() & AP_STARTED_BIT) != 0;
#endif
}

// Arduino setters and a configured netif IP can outlive the AP_START event.
// Read the live driver before telling an operator that the hotspot is ready.
inline bool accessPointReady(const char* ssid, int channel, const char* password) {
  wifi_mode_t mode = WIFI_MODE_NULL;
  wifi_config_t config = {};
  uint8_t protocol = 0;
  const size_t length = strlen(ssid);
  return accessPointStarted()
      && esp_wifi_get_mode(&mode) == ESP_OK && (mode & WIFI_MODE_AP)
      && esp_wifi_get_config(WIFI_IF_AP, &config) == ESP_OK
      && config.ap.ssid_len == length
      && memcmp(config.ap.ssid, ssid, length) == 0
      && config.ap.ssid_hidden == 0
      && config.ap.authmode == (password && password[0] ? WIFI_AUTH_WPA2_PSK : WIFI_AUTH_OPEN)
      && (!password || !password[0]
          || strncmp(reinterpret_cast<const char*>(config.ap.password), password,
                     sizeof(config.ap.password)) == 0)
      && config.ap.channel == channel
      && esp_wifi_get_protocol(WIFI_IF_AP, &protocol) == ESP_OK
      && protocol == kAccessPointProtocolMask
      && static_cast<uint32_t>(WiFi.softAPIP()) != 0;
}

inline bool openAccessPointReady(const char* ssid, int channel) {
  return accessPointReady(ssid, channel, nullptr);
}

// Physical join QR codes must describe the AP that is actually running. A
// freshly saved admin password can precede its deferred AP rekey by 750ms.
inline bool accessPointJoinPassword(const char* ssid, char* output, size_t capacity) {
  if (!output || !capacity) return false;
  output[0] = 0;
  if (!ssid || !ssid[0] || !accessPointStarted()) return false;
  wifi_config_t config = {};
  const size_t ssid_length = strlen(ssid);
  if (ssid_length > sizeof(config.ap.ssid)) return false;
  if (esp_wifi_get_config(WIFI_IF_AP, &config) != ESP_OK
      || config.ap.ssid_len != ssid_length
      || memcmp(config.ap.ssid, ssid, config.ap.ssid_len) != 0) return false;
  if (config.ap.authmode == WIFI_AUTH_OPEN) return true;
  if (config.ap.authmode != WIFI_AUTH_WPA2_PSK) return false;
  size_t length = 0;
  while (length < sizeof(config.ap.password) && config.ap.password[length]) ++length;
  if (length < 8 || length == sizeof(config.ap.password) || length >= capacity) return false;
  memcpy(output, config.ap.password, length);
  output[length] = 0;
  return true;
}

inline bool finishAccessPointScan() {
  // A failed Arduino scan can still have an active SDK scanner. scanDelete()
  // only releases results; cancel that scanner before enabling AP beacons.
  if (WiFi.scanComplete() < 0) {
    if (esp_wifi_scan_stop() != ESP_OK) return false;
    const uint32_t started = millis();
    while (WiFi.scanComplete() == WIFI_SCAN_RUNNING
           && millis() - started < 100) delay(20);
    WiFi.scanDelete();
  }
  return WiFi.scanComplete() != WIFI_SCAN_RUNNING;
}

inline bool scanBeforeAccessPoint() {
  // Use the same STA-only scan-first startup as WebConfig, including compact
  // OTA images without WebConfig. Shared ESP-NOW stays on its home channel.
  WiFi.scanDelete();
  WiFi.scanNetworks(true, false, false, 200, stationScanChannel());
  const uint32_t started = millis();
  while (WiFi.scanComplete() == WIFI_SCAN_RUNNING
         && millis() - started < 3600) delay(20);
  return finishAccessPointScan();
}

inline bool startAccessPoint(const char* ssid, bool reuse_existing, const char* password) {
  if (!accessPointCompatibleWithLongRange()) return false;
  const int channel = accessPointChannel();
  // Repeated OTA commands retain joined clients when the AP is already live.
  if (reuse_existing && applyAccessPointProtocolMask() == ESP_OK
      && accessPointReady(ssid, channel, password)) return true;

  const bool connected = WiFi.status() == WL_CONNECTED
      && static_cast<uint32_t>(WiFi.localIP()) != 0;
  // Removing only AP keeps a station association and any ESP-NOW owner. It
  // also initializes a cold, previously disabled radio through Arduino.
  if (!WiFi.mode(WIFI_STA)) return false;
  const uint32_t stopped = millis();
  while (accessPointStarted() && millis() - stopped < 300) delay(20);
  if (accessPointStarted() || applyProtocolMask(WIFI_IF_STA) != ESP_OK)
    return false;
  // Do not scan away from a live LAN connection. An offline radio needs the
  // scan-first preparation before its first AP, rather than AP-only startup.
  if (!(connected ? finishAccessPointScan() : scanBeforeAccessPoint()))
    return false;

  const IPAddress ip(192, 168, 4, 1);
  const IPAddress mask(255, 255, 255, 0);
  bool ready = WiFi.softAPConfig(ip, ip, mask)
      && WiFi.softAP(ssid, password, channel)
      && applyAccessPointProtocolMask() == ESP_OK;
  if (ready) {
    ready = false;
    const uint32_t started = millis();
    do {
      ready = accessPointReady(ssid, channel, password);
      if (!ready) delay(20);
    } while (!ready && millis() - started < 300);
  }
  if (!ready) WiFi.softAPdisconnect(true);
  return ready;
}

inline bool startOpenAccessPoint(const char* ssid, bool reuse_existing) {
  return startAccessPoint(ssid, reuse_existing, nullptr);
}

// Existing short/non-ASCII admin passwords retain HTTP authentication and an
// explicitly reported open-AP compatibility path; never invent a second secret.
inline bool startManagementAccessPoint(const char* ssid, bool reuse_existing) {
  mesh::esp32::managementPassword::Snapshot current;
  if (!mesh::esp32::managementPassword::snapshot(current)) return false;
  const char* password = mesh::esp32::managementPassword::apPasswordEligible(current.password)
      ? current.password : nullptr;
  return startAccessPoint(ssid, reuse_existing, password);
}

inline void stopTemporaryAccessPointRadio(bool started_radio) {
  // Removing AP releases its shared LR constraint. The station policy checks
  // the live SDK mode and cannot re-enable LR while AP is still present.
  if (espNowChannelConstrained()) {
    applyProtocolMask(WIFI_IF_STA);
    return;
  }
  // Only restore OFF when this AP initialized the radio. A station inherited
  // from WebConfig/MQTT belongs to the caller's service ownership policy.
  if (started_radio && !espNowChannelConstrained()
      && WiFi.status() != WL_CONNECTED) WiFi.mode(WIFI_OFF);
}

} // namespace wifi
} // namespace mesh
