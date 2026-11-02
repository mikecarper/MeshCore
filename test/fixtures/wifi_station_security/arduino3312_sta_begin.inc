// Exact excerpts from Arduino-ESP32 3.3.12 (94afccf35fb1e401facddbcf9e13bcf7c76a31d8).
// STA.cpp: Apache-2.0, Copyright 2023 Espressif Systems (Shanghai) CO LTD.
// WiFiSTA.cpp: LGPL-2.1-or-later, Copyright 2014 Ivan Grokhotkov;
// reworked 2015 by Markus Sattler. Original license notices follow.
// Sources: https://raw.githubusercontent.com/espressif/arduino-esp32/94afccf35fb1e401facddbcf9e13bcf7c76a31d8/libraries/WiFi/src/STA.cpp
//          https://raw.githubusercontent.com/espressif/arduino-esp32/94afccf35fb1e401facddbcf9e13bcf7c76a31d8/libraries/WiFi/src/WiFiSTA.cpp


/*
 * SPDX-FileCopyrightText: 2023 Espressif Systems (Shanghai) CO LTD
 *
 * SPDX-License-Identifier: Apache-2.0
 */


/*
 WiFiSTA.cpp - WiFi library for esp32

 Copyright (c) 2014 Ivan Grokhotkov. All rights reserved.
 This file is part of the esp8266 core for Arduino environment.

 This library is free software; you can redistribute it and/or
 modify it under the terms of the GNU Lesser General Public
 License as published by the Free Software Foundation; either
 version 2.1 of the License, or (at your option) any later version.

 This library is distributed in the hope that it will be useful,
 but WITHOUT ANY WARRANTY; without even the implied warranty of
 MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the GNU
 Lesser General Public License for more details.

 You should have received a copy of the GNU Lesser General Public
 License along with this library; if not, write to the Free Software
 Foundation, Inc., 51 Franklin St, Fifth Floor, Boston, MA  02110-1301  USA

 Reworked on 28 Dec 2015 by Markus Sattler

 */



static size_t _wifi_strncpy(char *dst, const char *src, size_t dst_len) {
  if (!dst || !src || !dst_len) {
    return 0;
  }
  size_t src_len = strlen(src);
  if (src_len >= dst_len) {
    src_len = dst_len;
  } else {
    src_len += 1;
  }
  memcpy(dst, src, src_len);
  return src_len;
}

bool STAClass::begin(bool tryConnect) {
  if (!WiFi.enableSTA(true)) {
    log_e("STA enable failed!");
    return false;
  }
  if (!waitStatusBits(ESP_NETIF_STARTED_BIT, 1000)) {
    log_e("Failed to start STA!");
    return false;
  }
  if (tryConnect) {
    return connect();
  }
  return true;
}

bool STAClass::connect() {
  if (_esp_netif == NULL) {
    log_e("STA not started! You must call begin() first.");
    return false;
  }

  if (connected()) {
    log_w("STA already connected.");
    return true;
  }

  wifi_config_t current_conf;
  if (esp_wifi_get_config(WIFI_IF_STA, &current_conf) != ESP_OK || esp_wifi_set_config(WIFI_IF_STA, &current_conf) != ESP_OK) {
    log_e("STA config failed");
    return false;
  }

  if ((getStatusBits() & ESP_NETIF_HAS_STATIC_IP_BIT) == 0 && !config()) {
    log_e("STA failed to configure dynamic IP!");
    return false;
  }

  esp_err_t err = esp_wifi_connect();
  if (err) {
    log_e("STA connect failed! 0x%x: %s", err, esp_err_to_name(err));
    return false;
  }
  return true;
}

bool STAClass::connect(const char *ssid, const char *passphrase, int32_t channel, const uint8_t *bssid, bool tryConnect) {
  if (_esp_netif == NULL) {
    log_e("STA not started! You must call begin() first.");
    return false;
  }

  if (connected()) {
    log_w("STA currently connected. Disconnecting...");
    if (!disconnect(true, 1000)) {
      return false;
    }
  }

  if (!ssid || *ssid == 0x00 || strlen(ssid) > 32) {
    log_e("SSID too long or missing!");
    return false;
  }

  if (passphrase && strlen(passphrase) > 64) {
    log_e("passphrase too long!");
    return false;
  }

  wifi_config_t conf;
  memset(&conf, 0, sizeof(wifi_config_t));
  conf.sta.channel = channel;
  conf.sta.scan_method = _scanMethod;
  conf.sta.sort_method = _sortMethod;
  conf.sta.threshold.rssi = -127;
  conf.sta.pmf_cfg.capable = true;
  if (ssid != NULL && ssid[0] != 0) {
    _wifi_strncpy((char *)conf.sta.ssid, ssid, 32);
    if (passphrase != NULL && passphrase[0] != 0) {
      conf.sta.threshold.authmode = _minSecurity;
      _wifi_strncpy((char *)conf.sta.password, passphrase, 64);
    }
    if (bssid != NULL) {
      conf.sta.bssid_set = 1;
      memcpy(conf.sta.bssid, bssid, 6);
    }
  }

  esp_err_t err = esp_wifi_set_config(WIFI_IF_STA, &conf);
  if (err != ESP_OK) {
    log_e("STA clear config failed! 0x%x: %s", err, esp_err_to_name(err));
    return false;
  }

  if ((getStatusBits() & ESP_NETIF_HAS_STATIC_IP_BIT) == 0 && !config()) {
    log_e("STA failed to configure dynamic IP!");
    return false;
  }

  if (tryConnect) {
    esp_err_t err = esp_wifi_connect();
    if (err) {
      log_e("STA connect failed! 0x%x: %s", err, esp_err_to_name(err));
      return false;
    }
  }
  return true;
}

bool STAClass::disconnect(bool eraseap, unsigned long timeout) {
  esp_err_t err = esp_wifi_disconnect();
  if (err != ESP_OK) {
    log_e("STA disconnect failed! 0x%x: %s", err, esp_err_to_name(err));
    return false;
  }

  if (timeout) {
    const unsigned long start = millis();
    while (connected() && ((millis() - start) < timeout)) {
      delay(5);
    }
    if (connected()) {
      return false;
    }
  }

  if (eraseap) {
    if (!started()) {
      log_e("STA not started! You must call begin first.");
      return false;
    }
    wifi_config_t conf;
    memset(&conf, 0, sizeof(wifi_config_t));
    esp_err_t err = esp_wifi_set_config(WIFI_IF_STA, &conf);
    if (err != ESP_OK) {
      log_e("STA clear config failed! 0x%x: %s", err, esp_err_to_name(err));
      return false;
    }
  }

  return true;
}

bool STAClass::reconnect() {
  if (connected()) {
    if (esp_wifi_disconnect() != ESP_OK) {
      return false;
    }
  }
  return esp_wifi_connect() == ESP_OK;
}

wl_status_t WiFiSTAClass::begin(const char *ssid, const char *passphrase, int32_t channel, const uint8_t *bssid, bool tryConnect) {
  if (!STA.begin()) {
    return WL_CONNECT_FAILED;
  }

  if (!STA.connect(ssid, passphrase, channel, bssid, tryConnect)) {
    return WL_CONNECT_FAILED;
  }

  return STA.status();
}

wl_status_t WiFiSTAClass::begin() {
  if (!STA.begin(true)) {
    return WL_CONNECT_FAILED;
  }

  return STA.status();
}

bool WiFiSTAClass::reconnect() {
  return STA.reconnect();
}
