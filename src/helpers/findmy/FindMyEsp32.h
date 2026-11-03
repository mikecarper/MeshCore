#pragma once

// This backend owns the ESP32 Bluetooth controller while it is active. It is
// intentionally unavailable on NimBLE-only builds and refuses to take over an
// existing Bluetooth stack. The public address and Apple manufacturer payload
// are prepared by the platform-independent beacon encoder.
#if defined(MESH_SENSOR_FINDMY) && MESH_SENSOR_FINDMY && defined(ESP32_PLATFORM)

#include <stdint.h>
#include <string.h>
#include <sdkconfig.h>

#if defined(CONFIG_BT_ENABLED) && CONFIG_BT_ENABLED \
    && defined(CONFIG_BLUEDROID_ENABLED) && CONFIG_BLUEDROID_ENABLED
#include <Arduino.h>
#include <atomic>
#include <esp_bt.h>
#include <esp_bt_main.h>
#include <esp_gap_ble_api.h>
#define MESH_FINDMY_ESP32_BLUEDROID 1
#else
#define MESH_FINDMY_ESP32_BLUEDROID 0
#endif

namespace mesh {
namespace findmy {

class Esp32Beacon {
 public:
  Esp32Beacon() = default;
  Esp32Beacon(const Esp32Beacon&) = delete;
  Esp32Beacon& operator=(const Esp32Beacon&) = delete;
  ~Esp32Beacon() { stop(); }

  // addr is in displayed byte order, with its static-random bits already set;
  // payload includes the two-byte company identifier but no AD length/type.
  // Returns true only after the controller reports advertising started.
  bool start(const uint8_t addr[6], const uint8_t payload[29]) {
#if MESH_FINDMY_ESP32_BLUEDROID
    if (!addr || !payload || (addr[0] & 0xc0u) != 0xc0u) return false;
    State& s = state();
    if (s.owner || esp_bt_controller_get_status() != ESP_BT_CONTROLLER_STATUS_IDLE
        || esp_bluedroid_get_status() != ESP_BLUEDROID_STATUS_UNINITIALIZED)
      return false;

    s.owner = this;
    esp_bt_controller_config_t config = BT_CONTROLLER_INIT_CONFIG_DEFAULT();
    if (esp_bt_controller_init(&config) != ESP_OK) {
      s.owner = nullptr;
      return false;
    }
    s.controller_owned = true;
    if (esp_bt_controller_enable(ESP_BT_MODE_BLE) != ESP_OK
        || esp_bluedroid_init() != ESP_OK) {
      stop();
      return false;
    }
    s.host_owned = true;
    if (esp_bluedroid_enable() != ESP_OK
        || esp_ble_gap_register_callback(gapCallback) != ESP_OK) {
      stop();
      return false;
    }

    uint8_t random_address[6];
    memcpy(random_address, addr, sizeof(random_address));
    s.address_result.store(kPending, std::memory_order_release);
    if (esp_ble_gap_set_rand_addr(random_address) != ESP_OK
        || !waitFor(s.address_result)) {
      stop();
      return false;
    }

    s.raw[0] = 30; // type byte plus 29-byte manufacturer payload
    s.raw[1] = 0xff; // Bluetooth manufacturer-specific AD type
    memcpy(s.raw + 2, payload, 29);
    s.config_result.store(kPending, std::memory_order_release);
    if (esp_ble_gap_config_adv_data_raw(s.raw, sizeof(s.raw)) != ESP_OK
        || !waitFor(s.config_result)) {
      stop();
      return false;
    }

    esp_ble_adv_params_t params = {};
    // 1.28 seconds. Longer intervals save power at the cost of discovery.
    params.adv_int_min = 2048;
    params.adv_int_max = 2048;
    params.adv_type = ADV_TYPE_NONCONN_IND;
    params.own_addr_type = BLE_ADDR_TYPE_RANDOM;
    params.channel_map = ADV_CHNL_ALL;
    params.adv_filter_policy = ADV_FILTER_ALLOW_SCAN_ANY_CON_ANY;
    s.start_result.store(kPending, std::memory_order_release);
    if (esp_ble_gap_start_advertising(&params) != ESP_OK) {
      stop();
      return false;
    }
    s.start_requested = true;
    if (!waitFor(s.start_result)) {
      stop();
      return false;
    }
    return true;
#else
    (void)addr;
    (void)payload;
    return false;
#endif
  }

  // False means SDK teardown was incomplete and this instance still owns the
  // controller. The caller can retry; another owner must not start meanwhile.
  bool stop() {
#if MESH_FINDMY_ESP32_BLUEDROID
    State& s = state();
    if (s.owner != this) return true;
    // Stop advertising before disabling the host. A timed-out start may
    // still complete, so also attempt stop when the request was submitted.
    if (s.start_requested
        && esp_bluedroid_get_status() == ESP_BLUEDROID_STATUS_ENABLED) {
      s.stop_result.store(kPending, std::memory_order_release);
      if (esp_ble_gap_stop_advertising() == ESP_OK) (void)waitFor(s.stop_result);
    }
    // Never release controller memory: it must remain reusable.
    if (s.host_owned) {
      if (esp_bluedroid_get_status() == ESP_BLUEDROID_STATUS_ENABLED
          && esp_bluedroid_disable() != ESP_OK) return false;
      const esp_bluedroid_status_t host_status = esp_bluedroid_get_status();
      if (host_status != ESP_BLUEDROID_STATUS_INITIALIZED
          && host_status != ESP_BLUEDROID_STATUS_UNINITIALIZED) return false;
      // Once the host is disabled no advertisement can remain active, even
      // if its stop callback failed or the following deinitialization fails.
      s.active.store(false, std::memory_order_release);
      if (host_status == ESP_BLUEDROID_STATUS_INITIALIZED
          && esp_bluedroid_deinit() != ESP_OK) return false;
      if (esp_bluedroid_get_status() != ESP_BLUEDROID_STATUS_UNINITIALIZED) return false;
      s.host_owned = false;
      s.start_requested = false;
    }
    if (s.controller_owned) {
      if (esp_bt_controller_get_status() == ESP_BT_CONTROLLER_STATUS_ENABLED
          && esp_bt_controller_disable() != ESP_OK) return false;
      if (esp_bt_controller_get_status() == ESP_BT_CONTROLLER_STATUS_INITED
          && esp_bt_controller_deinit() != ESP_OK) return false;
      if (esp_bt_controller_get_status() != ESP_BT_CONTROLLER_STATUS_IDLE) return false;
      s.controller_owned = false;
    }
    s.owner = nullptr;
#endif
    return true;
  }

  bool isActive() const {
#if MESH_FINDMY_ESP32_BLUEDROID
    const State& s = state();
    return s.owner == this && s.active.load(std::memory_order_acquire);
#else
    return false;
#endif
  }

 private:
#if MESH_FINDMY_ESP32_BLUEDROID
  enum { kPending = -1, kFailed = 0, kSucceeded = 1 };
  struct State {
    Esp32Beacon* owner = nullptr;
    bool controller_owned = false;
    bool host_owned = false;
    bool start_requested = false;
    uint8_t raw[31] = {};
    std::atomic<int> address_result{kPending};
    std::atomic<int> config_result{kPending};
    std::atomic<int> start_result{kPending};
    std::atomic<int> stop_result{kPending};
    std::atomic<bool> active{false};
  };

  static State& state() {
    // Callback state has process lifetime even if SDK shutdown fails.
    static State s;
    return s;
  }

  static bool waitFor(std::atomic<int>& result) {
    const uint32_t started = millis();
    while (result.load(std::memory_order_acquire) == kPending
           && static_cast<uint32_t>(millis() - started) < 2000u) delay(5);
    return result.load(std::memory_order_acquire) == kSucceeded;
  }

  static void gapCallback(esp_gap_ble_cb_event_t event,
                          esp_ble_gap_cb_param_t* param) {
    if (!param) return;
    State& s = state();
    if (event == ESP_GAP_BLE_SET_STATIC_RAND_ADDR_EVT) {
      s.address_result.store(param->set_rand_addr_cmpl.status == ESP_BT_STATUS_SUCCESS
                                 ? kSucceeded : kFailed, std::memory_order_release);
    } else if (event == ESP_GAP_BLE_ADV_DATA_RAW_SET_COMPLETE_EVT) {
      s.config_result.store(param->adv_data_raw_cmpl.status == ESP_BT_STATUS_SUCCESS
                                ? kSucceeded : kFailed, std::memory_order_release);
    } else if (event == ESP_GAP_BLE_ADV_START_COMPLETE_EVT) {
      const bool ok = param->adv_start_cmpl.status == ESP_BT_STATUS_SUCCESS;
      s.active.store(ok, std::memory_order_release);
      s.start_result.store(ok ? kSucceeded : kFailed, std::memory_order_release);
    } else if (event == ESP_GAP_BLE_ADV_STOP_COMPLETE_EVT) {
      const bool ok = param->adv_stop_cmpl.status == ESP_BT_STATUS_SUCCESS;
      if (ok) s.active.store(false, std::memory_order_release);
      s.stop_result.store(ok ? kSucceeded : kFailed, std::memory_order_release);
    }
  }
#endif
};

} // namespace findmy
} // namespace mesh

#endif // MESH_SENSOR_FINDMY && ESP32_PLATFORM
