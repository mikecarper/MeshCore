#pragma once

#include <cassert>
#include <cstddef>
#include <cstdint>
#include <functional>
#include <string>
#include <vector>

#define LOG_LV1(...) do {} while (0)
#define LOG_LV2(...) do {} while (0)

constexpr uint8_t BLE_GAP_ROLE_INVALID = 0;
constexpr uint8_t BLE_GAP_ROLE_PERIPH = 1;
constexpr uint8_t BLE_GAP_ROLE_CENTRAL = 2;
constexpr uint16_t BLE_CONN_HANDLE_INVALID = 0xffff;
enum {
  BLE_GAP_EVT_CONNECTED = 1,
  BLE_GAP_EVT_DISCONNECTED,
  BLE_GAP_EVT_RSSI_CHANGED,
  BLE_EVT_USER_MEM_REQUEST,
  BLE_EVT_USER_MEM_RELEASE,
  BLE_TEST_GENERIC_EVENT,
};

struct ble_gap_addr_t {
  uint8_t addr[6] = {}, addr_type = 0, addr_id_peer = 0;
};
struct ble_gap_conn_params_t {
  uint16_t max_conn_interval = 24, slave_latency = 4, conn_sup_timeout = 200;
};
struct ble_gap_evt_connected_t {
  ble_gap_addr_t peer_addr;
  ble_gap_conn_params_t conn_params;
  uint8_t role = BLE_GAP_ROLE_INVALID;
};
struct ble_gap_evt_disconnected_t { uint8_t reason = 0; };
struct ble_gap_evt_rssi_changed_t { int8_t rssi = 0; };
struct ble_evt_t {
  struct { uint16_t evt_id = 0; } header;
  struct {
    // The real SDK exposes the same handle storage through both views.
    union {
      struct { uint16_t conn_handle; } common_evt;
      struct {
        uint16_t conn_handle;
        struct {
          ble_gap_evt_connected_t connected;
          ble_gap_evt_disconnected_t disconnected;
          ble_gap_evt_rssi_changed_t rssi_changed;
        } params;
      } gap_evt;
    };
  } evt = {};
};

inline std::vector<std::string> steps;
inline std::vector<std::function<void()>> deferred;
inline unsigned deleted_connections = 0;
inline unsigned live_connections = 0;

template<typename Function, typename... Args>
bool ada_callback(void*, size_t, Function function, Args... args) {
  steps.push_back("defer");
  deferred.emplace_back([=] { function(args...); });
  return true;
}

inline void sd_ble_user_mem_reply(uint16_t, void*) {
  steps.push_back("memory_reply");
}

struct BLEConnection {
  uint8_t _role;
  bool _connected = true;
  BLEConnection(uint16_t, const ble_gap_evt_connected_t* connected,
                uint8_t, uint8_t) : _role(connected->role) {
    ++live_connections;
    steps.push_back("create");
  }
  ~BLEConnection() {
    --live_connections;
    ++deleted_connections;
    steps.push_back("delete");
  }
  // Read object storage so ASan checks the original dispatcher's freed access.
  __attribute__((noinline)) uint8_t getRole() { return _role; }
  void _eventHandler(ble_evt_t* event) {
    steps.push_back("connection");
    if (event->header.evt_id == BLE_GAP_EVT_DISCONNECTED) _connected = false;
  }
};

using ConnectCallback = void (*)(uint16_t);
using DisconnectCallback = void (*)(uint16_t, uint8_t);
struct RoleHandler {
  const char* name;
  ConnectCallback _connect_cb = nullptr;
  DisconnectCallback _disconnect_cb = nullptr;
  void _eventHandler(ble_evt_t*) { steps.push_back(name); }
};
struct Handler {
  const char* name;
  void _eventHandler(ble_evt_t*) { steps.push_back(name); }
};
struct DiscoveryHandler : Handler {
  bool enabled = true;
  bool begun() const { return enabled; }
};

class AdafruitBluefruit {
public:
  BLEConnection* _connection[4] = {};
  uint16_t _conn_hdl = BLE_CONN_HANDLE_INVALID;
  struct {
    struct { uint8_t hvn_qsize = 3, wrcmd_qsize = 1; } prph, central;
  } _sd_cfg;
  RoleHandler Periph{"peripheral"}, Central{"central"};
  Handler Security{"security"}, Advertising{"advertising"}, Scanner{"scanner"},
      Gatt{"gatt"};
  DiscoveryHandler Discovery{{"discovery"}};
  void (*_event_cb)(ble_evt_t*) = nullptr;
  void (*_rssi_cb)(uint16_t, int8_t) = nullptr;

  BLEConnection* Connection(uint16_t handle) {
    return handle < 4 ? _connection[handle] : nullptr;
  }
  unsigned connected() const {
    unsigned count = 0;
    for (auto* conn : _connection) count += conn && conn->_connected;
    return count;
  }
  void _stopConnLed() { steps.push_back("led_stop"); }
  void _setConnLed(bool enabled) {
    steps.push_back(enabled ? "led_on" : "led_off");
  }
  void _ble_handler(ble_evt_t*);
};
