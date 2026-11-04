#pragma once

#include <stdio.h>
#include <esp_wifi.h>
#include "WiFiStationPower.h"
#include "WiFiTxPowerValue.h"

namespace mesh { namespace wifi {

inline void formatTxPowerValue(uint8_t quarters, char* out, size_t len) {
  if (!quarters) { snprintf(out, len, "auto"); return; }
  if (!(quarters % 4)) snprintf(out, len, "%u", unsigned(quarters / 4));
  else snprintf(out, len, "%u.%02u", unsigned(quarters / 4), unsigned(quarters % 4) * 25);
}

inline void formatTxPowerStatus(char* reply, size_t len) {
  uint8_t setting;
  if (!readStationTxPower(setting)) {
    snprintf(reply, len, "Error: cannot read WiFi TX power setting"); return;
  }
  char requested[12]; formatTxPowerValue(setting, requested, sizeof(requested));
  int8_t driver = 0;
  if (esp_wifi_get_max_tx_power(&driver) == ESP_OK && driver >= 0) {
    char actual[12]; formatTxPowerValue(uint8_t(driver), actual, sizeof(actual));
    snprintf(reply, len, "> %s%s; driver limit %s dBm", requested, setting ? " dBm" : "", actual);
  } else {
    snprintf(reply, len, "> %s%s; WiFi inactive", requested, setting ? " dBm" : "");
  }
}

inline void handleTxPowerSet(const char* value, char* reply, size_t len) {
  uint8_t setting;
  if (!parseTxPowerValue(value, setting)) {
    snprintf(reply, len, "Error: use set wifi.tx auto|2..21 dBm (0.25 steps)"); return;
  }
  if (!saveStationTxPower(setting)) {
    snprintf(reply, len, "Error: WiFi TX power save failed; reboot may differ"); return;
  }
  wifi_mode_t wifi_mode;
  const esp_err_t mode_result = esp_wifi_get_mode(&wifi_mode);
  if (mode_result == ESP_ERR_WIFI_NOT_INIT || mode_result == ESP_ERR_WIFI_NOT_STARTED
      || (mode_result == ESP_OK && wifi_mode == WIFI_MODE_NULL)) {
    snprintf(reply, len, "OK - saved; applies when WiFi starts"); return;
  }
  if (mode_result != ESP_OK) {
    snprintf(reply, len, "Error: saved, but WiFi status failed (%d)", int(mode_result)); return;
  }
  const esp_err_t result = applyStationTxPower();
  if (result == ESP_ERR_WIFI_NOT_INIT || result == ESP_ERR_WIFI_NOT_STARTED) {
    snprintf(reply, len, "OK - saved; applies when WiFi starts"); return;
  }
  if (result != ESP_OK) {
    snprintf(reply, len, "Error: saved, but WiFi TX power apply failed (%d)", int(result)); return;
  }
  char status[100]; formatTxPowerStatus(status, sizeof(status));
  snprintf(reply, len, "OK - %s", status[0] == '>' && status[1] == ' ' ? status + 2 : status);
}

}} // namespace mesh::wifi
