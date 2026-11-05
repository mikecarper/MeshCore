#pragma once

#include <atomic>
#include <stdint.h>

namespace mesh {
namespace wifi {

enum class StationOwner : uint8_t {
  None       = 0,
  MqttBridge = 1u << 0,
  UdpBridge  = 1u << 1,
  WebConfig  = 1u << 2,
  Companion  = 1u << 3,
  Ota        = 1u << 4,
};

enum class AccessPointOwner : uint8_t {
  None           = 0,
  WebConfigSetup = 1u << 0,
  OtaSetup       = 1u << 1,
  CompanionSetup = 1u << 2,
};

enum class EspNowOwner : uint8_t {
  None         = 0,
  PrimaryRadio = 1u << 0,
  Bridge       = 1u << 1,
};

// Process-wide coordination for ESP32 2.4 GHz WiFi owners. Not every OTA AP,
// Companion AP, or ESP-NOW owner publishes a lease yet; shutdown paths must
// retain their direct hardware/service ownership checks as well.
class Coordinator {
public:
  void setStationLease(StationOwner owner, bool active) {
    const uint8_t bit = static_cast<uint8_t>(owner);
    if (active) {
      _station_owners.fetch_or(bit, std::memory_order_acq_rel);
    } else {
      _station_owners.fetch_and(static_cast<uint8_t>(~bit), std::memory_order_acq_rel);
    }
  }

  bool hasStationLease(StationOwner owner) const {
    return (_station_owners.load(std::memory_order_acquire) & static_cast<uint8_t>(owner)) != 0;
  }

  uint8_t stationLeases() const {
    return _station_owners.load(std::memory_order_acquire);
  }

  bool anyStationLease() const {
    return stationLeases() != 0;
  }

  bool hasAnyStationLease() const {
    return anyStationLease();
  }

  bool hasExternalStationOwner(StationOwner self) const {
    return (stationLeases() & static_cast<uint8_t>(~static_cast<uint8_t>(self))) != 0;
  }

  void setAccessPointLease(AccessPointOwner owner, bool active) {
    const uint8_t bit = static_cast<uint8_t>(owner);
    if (active) {
      _ap_owners.fetch_or(bit, std::memory_order_acq_rel);
    } else {
      _ap_owners.fetch_and(static_cast<uint8_t>(~bit), std::memory_order_acq_rel);
    }
  }

  bool hasAccessPointLease(AccessPointOwner owner) const {
    return (_ap_owners.load(std::memory_order_acquire) & static_cast<uint8_t>(owner)) != 0;
  }

  uint8_t accessPointLeases() const {
    return _ap_owners.load(std::memory_order_acquire);
  }

  bool anyAccessPointLease() const {
    return accessPointLeases() != 0;
  }

  bool hasAnyAccessPointLease() const {
    return anyAccessPointLease();
  }

  void setEspNowLease(EspNowOwner owner, bool active) {
    const uint8_t bit = static_cast<uint8_t>(owner);
    if (active) {
      _espnow_owners.fetch_or(bit, std::memory_order_acq_rel);
    } else {
      _espnow_owners.fetch_and(static_cast<uint8_t>(~bit), std::memory_order_acq_rel);
    }
  }

  bool hasEspNowLease(EspNowOwner owner) const {
    return (_espnow_owners.load(std::memory_order_acquire) & static_cast<uint8_t>(owner)) != 0;
  }

  uint8_t espNowLeases() const {
    return _espnow_owners.load(std::memory_order_acquire);
  }

  bool anyEspNowLease() const {
    return espNowLeases() != 0;
  }

  bool hasAnyEspNowLease() const {
    return anyEspNowLease();
  }

  bool anyLease() const {
    return anyStationLease() || anyAccessPointLease() || anyEspNowLease();
  }

  bool hasAnyLease() const {
    return anyLease();
  }

  bool canDeinitIdleDriver() const {
    return !anyLease();
  }

  void reset() {
    _station_owners.store(0, std::memory_order_release);
    _ap_owners.store(0, std::memory_order_release);
    _espnow_owners.store(0, std::memory_order_release);
  }

private:
  std::atomic<uint8_t> _station_owners{0};
  std::atomic<uint8_t> _ap_owners{0};
  std::atomic<uint8_t> _espnow_owners{0};
};

inline Coordinator& coordinator() {
  static Coordinator instance;
  return instance;
}

template <typename WiFiT, typename ModeT, typename ErrT, typename GetModeFn,
          typename StopFn, typename DeinitFn>
inline bool deinitIdleWiFiDriver(WiFiT& wifi, ModeT wifi_off, ModeT wifi_sta,
                                 ModeT sdk_null, ErrT esp_ok,
                                 GetModeFn get_mode, StopFn stop_fn,
                                 DeinitFn deinit_fn) {
  if (wifi.getMode() == wifi_off && wifi.channel() > 0
      && !wifi.mode(wifi_sta)) return false;
  ModeT sdk_mode = sdk_null;
  if (wifi.getMode() == wifi_off && get_mode(&sdk_mode) != esp_ok) return false;
  wifi.setAutoReconnect(false);
  wifi.disconnect(false, false);
  if (!wifi.mode(wifi_off)) return false;
  if (get_mode(&sdk_mode) == esp_ok) {
    stop_fn();
    deinit_fn();
  }
  return true;
}

template <typename WiFiT, typename ModeT, typename ErrT, typename GetModeFn,
          typename StopFn, typename DeinitFn>
inline bool deinitIdleWiFiDriver(WiFiT& wifi, ModeT wifi_off, ModeT wifi_sta,
                                 ErrT esp_ok, GetModeFn get_mode,
                                 StopFn stop_fn, DeinitFn deinit_fn) {
  return deinitIdleWiFiDriver(wifi, wifi_off, wifi_sta, wifi_off, esp_ok,
                              get_mode, stop_fn, deinit_fn);
}

}  // namespace wifi
}  // namespace mesh
