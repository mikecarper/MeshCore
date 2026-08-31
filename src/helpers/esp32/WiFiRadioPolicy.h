#pragma once

#include <stdint.h>
#include <esp_err.h>
#include <esp_wifi.h>
#include <helpers/WiFiChannelPolicy.h>

#if defined(MESH_PRIMARY_ESPNOW) && MESH_PRIMARY_ESPNOW
  #include <Preferences.h>
#endif

namespace mesh {
namespace wifi {

#if defined(MESH_PRIMARY_ESPNOW) && MESH_PRIMARY_ESPNOW
  #ifndef MESH_ESPNOW_CHANNEL
    #define MESH_ESPNOW_CHANNEL 1
  #endif
static_assert(MESH_ESPNOW_CHANNEL >= kEspNowChannelMin
                  && MESH_ESPNOW_CHANNEL <= kEspNowChannelMax,
              "MESH_ESPNOW_CHANNEL must be a 2.4 GHz WiFi channel");
static constexpr bool kPrimaryEspNowRadio = true;
static constexpr bool kKeepEspNowRadioRunning = true;
static constexpr uint8_t kDefaultEspNowChannel = MESH_ESPNOW_CHANNEL;
#else
static constexpr bool kPrimaryEspNowRadio = false;
static constexpr bool kKeepEspNowRadioRunning = false;
static constexpr uint8_t kDefaultEspNowChannel = 1;
#endif

#if defined(MESH_ESPNOW_RADIO) && MESH_ESPNOW_RADIO
static constexpr uint8_t kProtocolMask = WIFI_PROTOCOL_11B
    | WIFI_PROTOCOL_11G | WIFI_PROTOCOL_11N | WIFI_PROTOCOL_LR;
#elif defined(MESH_PRIMARY_ESPNOW) && MESH_PRIMARY_ESPNOW
// A primary ESP-NOW image without conventional WiFi retains its original
// LR-only protocol until a coexistence-capable recipe explicitly opts in.
static constexpr uint8_t kProtocolMask = WIFI_PROTOCOL_LR;
#else
static constexpr uint8_t kProtocolMask = WIFI_PROTOCOL_11B
    | WIFI_PROTOCOL_11G | WIFI_PROTOCOL_11N;
#endif

// LR is private to ESP-NOW on the station interface. A setup SoftAP must keep
// an ordinary b/g/n protocol bitmap so phones and laptops can discover it.
static constexpr uint8_t kAccessPointProtocolMask = WIFI_PROTOCOL_11B
    | WIFI_PROTOCOL_11G | WIFI_PROTOCOL_11N;

#if defined(MESH_PRIMARY_ESPNOW) && MESH_PRIMARY_ESPNOW
struct EspNowBootChannelState {
  bool loaded;
  uint8_t active;
};

inline uint8_t loadConfiguredEspNowChannel() {
  uint8_t channel = kDefaultEspNowChannel;
  Preferences prefs;
  // A missing namespace is the normal first-boot state. Read-write creates it
  // without Arduino Preferences emitting ESP_ERR_NVS_NOT_FOUND on Serial.
  if (prefs.begin("mesh-wifi", false)) {
    if (prefs.isKey("espnow_ch")) {
      channel = prefs.getUChar("espnow_ch", kDefaultEspNowChannel);
    }
    prefs.end();
  }
  return validEspNowChannelOrDefault(channel, kDefaultEspNowChannel);
}

// This cache intentionally represents the boot channel. Saving a new setting
// must not mutate it: reconnect/AP helpers continue using the old channel until
// reboot, so ESP-NOW, STA, and SoftAP cannot be moved at different times.
inline EspNowBootChannelState& espNowBootChannelState() {
  static EspNowBootChannelState state = {false, kDefaultEspNowChannel};
  return state;
}

inline uint8_t activeEspNowChannel() {
  EspNowBootChannelState& state = espNowBootChannelState();
  if (!state.loaded) {
    state.active = loadConfiguredEspNowChannel();
    state.loaded = true;
  }
  return state.active;
}

inline bool saveConfiguredEspNowChannel(uint8_t channel) {
  if (!isValidEspNowChannel(channel)) return false;
  Preferences prefs;
  if (!prefs.begin("mesh-wifi", false)) return false;
  const bool saved = prefs.putUChar("espnow_ch", channel) == sizeof(uint8_t)
      && prefs.getUChar("espnow_ch", 0) == channel;
  prefs.end();
  return saved;
}
#else
inline uint8_t loadConfiguredEspNowChannel() {
  return kDefaultEspNowChannel;
}
inline uint8_t activeEspNowChannel() {
  return kDefaultEspNowChannel;
}
inline bool saveConfiguredEspNowChannel(uint8_t) {
  return false;
}
#endif

inline esp_err_t applyProtocolMask(wifi_interface_t interface_id) {
  return esp_wifi_set_protocol(interface_id, kProtocolMask);
}

inline esp_err_t applyAccessPointProtocolMask() {
  return esp_wifi_set_protocol(WIFI_IF_AP, kAccessPointProtocolMask);
}

// An ESP-NOW radio and an associated station cannot occupy different channels.
// Full Companion therefore keeps both its setup AP and any infrastructure-WiFi
// association on the mesh channel. Passing zero on ordinary targets preserves
// Arduino's normal all-channel scan behavior.
inline int32_t stationChannelHint() {
  return kPrimaryEspNowRadio
      ? static_cast<int32_t>(activeEspNowChannel()) : 0;
}

inline int accessPointChannel() {
  return kPrimaryEspNowRadio
      ? static_cast<int>(activeEspNowChannel()) : 1;
}

inline uint8_t stationScanChannel() {
  return kPrimaryEspNowRadio ? activeEspNowChannel() : 0;
}

inline esp_err_t restoreEspNowChannel() {
  if (!kKeepEspNowRadioRunning) return ESP_OK;
  const uint8_t target = activeEspNowChannel();
  uint8_t current = 0;
  wifi_second_chan_t secondary = WIFI_SECOND_CHAN_NONE;
  if (esp_wifi_get_channel(&current, &secondary) == ESP_OK
      && !espNowChannelRestoreRequired(current, target)) {
    return ESP_OK;
  }
  // Fail closed instead of silently moving to the build default. A failure can
  // be transient (scan/connect in progress), and changing the cached boot
  // channel here would split this node from peers which still use the selected
  // channel. Callers retry through their normal reconnect/tick paths.
  return esp_wifi_set_channel(target, WIFI_SECOND_CHAN_NONE);
}

} // namespace wifi
} // namespace mesh
