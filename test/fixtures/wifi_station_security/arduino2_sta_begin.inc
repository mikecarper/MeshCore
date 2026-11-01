// Unchanged function definitions from Arduino-ESP32 2.0.17, retained for cold offline CI.
// Upstream commit: 5e19e086c43d0fa5e5a596497ff8f11a0a43f6c2
// Source: https://github.com/espressif/arduino-esp32/blob/5e19e086c43d0fa5e5a596497ff8f11a0a43f6c2/libraries/WiFi/src/WiFiSTA.cpp
// Complete upstream source SHA-256: 4af6b9dac33bd33c7011d672b3677e2f17e040c24313e7ebf473b20c948dc2a3
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

static size_t _wifi_strncpy(char * dst, const char * src, size_t dst_len){
    if(!dst || !src || !dst_len){
        return 0;
    }
    size_t src_len = strlen(src);
    if(src_len >= dst_len){
        src_len = dst_len;
    } else {
        src_len += 1;
    }
    memcpy(dst, src, src_len);
    return src_len;
}

static bool sta_config_equal(const wifi_config_t& lhs, const wifi_config_t& rhs)
{
    if(memcmp(&lhs, &rhs, sizeof(wifi_config_t)) != 0) {
        return false;
    }
    return true;
}

static void wifi_sta_config(wifi_config_t * wifi_config, const char * ssid=NULL, const char * password=NULL, const uint8_t * bssid=NULL, uint8_t channel=0, wifi_auth_mode_t min_security=WIFI_AUTH_WPA2_PSK, wifi_scan_method_t scan_method=WIFI_ALL_CHANNEL_SCAN, wifi_sort_method_t sort_method=WIFI_CONNECT_AP_BY_SIGNAL, uint16_t listen_interval=0, bool pmf_required=false){
    wifi_config->sta.channel = channel;
    wifi_config->sta.listen_interval = listen_interval;
    wifi_config->sta.scan_method = scan_method;//WIFI_ALL_CHANNEL_SCAN or WIFI_FAST_SCAN
    wifi_config->sta.sort_method = sort_method;//WIFI_CONNECT_AP_BY_SIGNAL or WIFI_CONNECT_AP_BY_SECURITY
    wifi_config->sta.threshold.rssi = -127;
    wifi_config->sta.pmf_cfg.capable = true;
    wifi_config->sta.pmf_cfg.required = pmf_required;
    wifi_config->sta.bssid_set = 0;
    memset(wifi_config->sta.bssid, 0, 6);
    wifi_config->sta.threshold.authmode = WIFI_AUTH_OPEN;
    wifi_config->sta.ssid[0] = 0;
    wifi_config->sta.password[0] = 0;
    if(ssid != NULL && ssid[0] != 0){
        _wifi_strncpy((char*)wifi_config->sta.ssid, ssid, 32);
        if(password != NULL && password[0] != 0){
            wifi_config->sta.threshold.authmode = min_security;
            _wifi_strncpy((char*)wifi_config->sta.password, password, 64);
        }
        if(bssid != NULL){
            wifi_config->sta.bssid_set = 1;
            memcpy(wifi_config->sta.bssid, bssid, 6);
        }
    }
}

wl_status_t WiFiSTAClass::begin(const char* ssid, const char *passphrase, int32_t channel, const uint8_t* bssid, bool connect)
{

    if(!WiFi.enableSTA(true)) {
        log_e("STA enable failed!");
        return WL_CONNECT_FAILED;
    }

    if(!ssid || *ssid == 0x00 || strlen(ssid) > 32) {
        log_e("SSID too long or missing!");
        return WL_CONNECT_FAILED;
    }

    if(passphrase && strlen(passphrase) > 64) {
        log_e("passphrase too long!");
        return WL_CONNECT_FAILED;
    }

    wifi_config_t conf;
    memset(&conf, 0, sizeof(wifi_config_t));

    wifi_sta_config(&conf, ssid, passphrase, bssid, channel, _minSecurity, _scanMethod, _sortMethod);

    wifi_config_t current_conf;
    if(esp_wifi_get_config((wifi_interface_t)ESP_IF_WIFI_STA, &current_conf) != ESP_OK){
        log_e("get current config failed!");
        return WL_CONNECT_FAILED;
    }
    if(!sta_config_equal(current_conf, conf)) {
        if(esp_wifi_disconnect()){
            log_e("disconnect failed!");
            return WL_CONNECT_FAILED;
        }

        if(esp_wifi_set_config((wifi_interface_t)ESP_IF_WIFI_STA, &conf) != ESP_OK){
            log_e("set config failed!");
            return WL_CONNECT_FAILED;
        }
    } else if(status() == WL_CONNECTED){
        return WL_CONNECTED;
    } else {
        if(esp_wifi_set_config((wifi_interface_t)ESP_IF_WIFI_STA, &conf) != ESP_OK){
            log_e("set config failed!");
            return WL_CONNECT_FAILED;
        }
    }

    if(!_useStaticIp){
        if(set_esp_interface_ip(ESP_IF_WIFI_STA) != ESP_OK) {
            return WL_CONNECT_FAILED;
        }
    }

    if(connect){
        if(esp_wifi_connect() != ESP_OK) {
            log_e("connect failed!");
            return WL_CONNECT_FAILED;
        }
    }

    return status();
}

wl_status_t WiFiSTAClass::begin()
{

    if(!WiFi.enableSTA(true)) {
        log_e("STA enable failed!");
        return WL_CONNECT_FAILED;
    }

    wifi_config_t current_conf;
    if(esp_wifi_get_config((wifi_interface_t)ESP_IF_WIFI_STA, &current_conf) != ESP_OK || esp_wifi_set_config((wifi_interface_t)ESP_IF_WIFI_STA, &current_conf) != ESP_OK) {
        log_e("config failed");
        return WL_CONNECT_FAILED;
    }

    if(!_useStaticIp && set_esp_interface_ip(ESP_IF_WIFI_STA) != ESP_OK) {
        log_e("set ip failed!");
        return WL_CONNECT_FAILED;
    }

    if(status() != WL_CONNECTED){
        esp_err_t err = esp_wifi_connect();
        if(err){
            log_e("connect failed! 0x%x", err);
            return WL_CONNECT_FAILED;
        }
    }

    return status();
}

bool WiFiSTAClass::reconnect()
{
    if(WiFi.getMode() & WIFI_MODE_STA) {
        if(esp_wifi_disconnect() == ESP_OK) {
            return esp_wifi_connect() == ESP_OK;
        }
    }
    return false;
}
