#pragma once
#include <cstdint>
#include "esp_err.h"
using wifi_interface_t = int;
using wifi_mode_t = int;
using wifi_second_chan_t = int;
constexpr int WIFI_MODE_NULL = 0, WIFI_MODE_STA = 1, WIFI_MODE_AP = 2;
constexpr int WIFI_IF_STA = 0, WIFI_IF_AP = 1, WIFI_STORAGE_RAM = 0;
constexpr int WIFI_SECOND_CHAN_NONE = 0;
constexpr int WIFI_PROTOCOL_11B = 1, WIFI_PROTOCOL_11G = 2;
constexpr int WIFI_PROTOCOL_11N = 4, WIFI_PROTOCOL_LR = 8;
namespace fake_wifi {
inline wifi_mode_t mode = WIFI_MODE_STA;
inline uint8_t station = 7, access_point = 7;
inline int station_writes = 0, ap_writes = 0, storage_writes = 0, mode_reads = 0;
inline int mode_read_failure = 0;
inline bool mode_fails = false, station_fails = false, ap_fails = false;
inline bool ap_appears = false;
}
inline int esp_wifi_get_mode(wifi_mode_t* value) {
  ++fake_wifi::mode_reads;
  if (fake_wifi::mode_fails || fake_wifi::mode_read_failure == fake_wifi::mode_reads) {
    return ESP_ERR_WIFI_NOT_INIT;
  }
  *value = fake_wifi::mode; return ESP_OK;
}
inline int esp_wifi_set_protocol(wifi_interface_t interface, uint8_t mask) {
  if (interface == WIFI_IF_STA) {
    ++fake_wifi::station_writes;
    if (fake_wifi::station_fails) return -4;
    fake_wifi::station = mask;
    if (fake_wifi::ap_appears) fake_wifi::mode |= WIFI_MODE_AP;
  } else {
    ++fake_wifi::ap_writes;
    if (fake_wifi::ap_fails) return -5;
    fake_wifi::access_point = mask;
  }
  return ESP_OK;
}
inline int esp_wifi_get_protocol(wifi_interface_t interface, uint8_t* mask) {
  *mask = interface == WIFI_IF_STA ? fake_wifi::station : fake_wifi::access_point;
  return ESP_OK;
}
inline int esp_wifi_set_storage(int) { ++fake_wifi::storage_writes; return ESP_OK; }
inline int esp_wifi_get_channel(uint8_t* channel, wifi_second_chan_t* secondary) {
  *channel = 1; *secondary = 0; return ESP_OK;
}
inline int esp_wifi_set_channel(uint8_t, wifi_second_chan_t) { return ESP_OK; }
