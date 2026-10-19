#include "bluefruit.h"

#include <algorithm>

static unsigned peripheral_connects = 0, central_connects = 0;
static unsigned peripheral_disconnects = 0, central_disconnects = 0;
static uint16_t callback_handle = BLE_CONN_HANDLE_INVALID;
static uint8_t callback_reason = 0;
static AdafruitBluefruit* active = nullptr;

static void peripheralConnect(uint16_t handle) {
  ++peripheral_connects;
  callback_handle = handle;
}
static void centralConnect(uint16_t handle) {
  ++central_connects;
  callback_handle = handle;
}
static void peripheralDisconnect(uint16_t handle, uint8_t reason) {
  assert(active->Connection(handle) == nullptr);
  ++peripheral_disconnects;
  callback_handle = handle;
  callback_reason = reason;
}
static void centralDisconnect(uint16_t handle, uint8_t reason) {
  assert(active->Connection(handle) == nullptr);
  ++central_disconnects;
  callback_handle = handle;
  callback_reason = reason;
}
static void userEvent(ble_evt_t* event) {
  if (event->header.evt_id == BLE_GAP_EVT_DISCONNECTED) {
    assert(active->Connection(event->evt.common_evt.conn_handle) == nullptr);
  }
  steps.push_back("user");
}
static ble_evt_t event(uint16_t id, uint16_t handle) {
  ble_evt_t result = {};
  result.header.evt_id = id;
  result.evt.common_evt.conn_handle = handle;
  return result;
}
static void runDeferred() {
  for (const auto& callback : deferred) callback();
  deferred.clear();
}
static void checkSteps(std::initializer_list<const char*> expected) {
  assert(steps.size() == expected.size());
  assert(std::equal(steps.begin(), steps.end(), expected.begin()));
  steps.clear();
}

static void connect(AdafruitBluefruit& bluefruit, uint8_t role, uint16_t handle) {
  auto connected = event(BLE_GAP_EVT_CONNECTED, handle);
  connected.evt.gap_evt.params.connected.role = role;
  bluefruit._ble_handler(&connected);
  assert(live_connections == 1);
  assert(bluefruit.Connection(handle)->getRole() == role);
  assert(bluefruit._conn_hdl == (role == BLE_GAP_ROLE_PERIPH
      ? handle : BLE_CONN_HANDLE_INVALID));
  assert(deferred.size() == 1);
  assert(steps[0] == "led_stop");
  assert(steps[1] == "led_on");
  assert(steps[2] == "create");
  assert(steps[3] == "defer");
  assert(steps[4] == "advertising");
  assert(steps[5] == "scanner");
  assert(steps[6] == (role == BLE_GAP_ROLE_PERIPH ? "peripheral" : "central"));
  assert(steps[7] == "discovery" && steps[8] == "gatt" && steps[9] == "user");
  assert(steps.size() == 10);
  steps.clear();
  runDeferred();
  assert(callback_handle == handle);
}

static void disconnect(AdafruitBluefruit& bluefruit, uint8_t role,
                       uint16_t handle, uint8_t reason) {
  const unsigned old_deleted = deleted_connections;
  const unsigned old_peripheral = peripheral_disconnects;
  const unsigned old_central = central_disconnects;
  auto disconnected = event(BLE_GAP_EVT_DISCONNECTED, handle);
  disconnected.evt.gap_evt.params.disconnected.reason = reason;
  bluefruit._ble_handler(&disconnected);
  assert(live_connections == 0 && deleted_connections == old_deleted + 1);
  assert(bluefruit.Connection(handle) == nullptr);
  assert(bluefruit._conn_hdl == BLE_CONN_HANDLE_INVALID);
  assert(deferred.size() == 1);
  assert(peripheral_disconnects == old_peripheral);
  assert(central_disconnects == old_central);
  if (role == BLE_GAP_ROLE_PERIPH) {
    checkSteps({"connection", "security", "led_off", "defer", "delete",
                "advertising", "scanner", "peripheral", "discovery", "gatt", "user"});
  } else {
    checkSteps({"connection", "security", "led_off", "defer", "delete",
                "advertising", "scanner", "central", "discovery", "gatt", "user"});
  }
  runDeferred();
  assert(callback_handle == handle && callback_reason == reason);
  assert(peripheral_disconnects == old_peripheral + (role == BLE_GAP_ROLE_PERIPH));
  assert(central_disconnects == old_central + (role == BLE_GAP_ROLE_CENTRAL));
}

int main() {
  AdafruitBluefruit bluefruit;
  active = &bluefruit;
  bluefruit.Periph._connect_cb = peripheralConnect;
  bluefruit.Central._connect_cb = centralConnect;
  bluefruit.Periph._disconnect_cb = peripheralDisconnect;
  bluefruit.Central._disconnect_cb = centralDisconnect;
  bluefruit._event_cb = userEvent;

  // Exercise peripheral and central roles, handle reuse, and both local and
  // supervision-timeout disconnect reasons without relying on real hardware.
  for (unsigned i = 0; i < 20; ++i) {
    const uint8_t role = (i & 1) ? BLE_GAP_ROLE_CENTRAL : BLE_GAP_ROLE_PERIPH;
    const uint16_t handle = i % 4;
    connect(bluefruit, role, handle);
    auto ordinary = event(BLE_TEST_GENERIC_EVENT, handle);
    bluefruit._ble_handler(&ordinary);
    if (role == BLE_GAP_ROLE_PERIPH) {
      checkSteps({"connection", "security", "advertising", "scanner", "peripheral",
                  "discovery", "gatt", "user"});
    } else {
      checkSteps({"connection", "security", "advertising", "scanner", "central",
                  "discovery", "gatt", "user"});
    }
    disconnect(bluefruit, role, handle, (i & 2) ? 0x08 : 0x16);
  }
  assert(peripheral_connects == 10 && central_connects == 10);
  assert(peripheral_disconnects == 10 && central_disconnects == 10);

  // Unknown-handle generic events still reach both role handlers.
  auto generic = event(BLE_EVT_USER_MEM_REQUEST, BLE_CONN_HANDLE_INVALID);
  bluefruit._ble_handler(&generic);
  checkSteps({"memory_reply", "advertising", "scanner", "peripheral", "central",
              "discovery", "gatt", "user"});

  // CONNECTED replaces an already occupied handle in the pinned framework.
  // The routing snapshot must be refreshed for the newly allocated role.
  connect(bluefruit, BLE_GAP_ROLE_PERIPH, 2);
  const unsigned old_deleted = deleted_connections;
  auto replacement = event(BLE_GAP_EVT_CONNECTED, 2);
  replacement.evt.gap_evt.params.connected.role = BLE_GAP_ROLE_CENTRAL;
  bluefruit._ble_handler(&replacement);
  assert(deleted_connections == old_deleted + 1 && live_connections == 1);
  checkSteps({"connection", "security", "led_stop", "led_on", "delete", "create",
              "defer", "advertising", "scanner", "central", "discovery", "gatt", "user"});
  runDeferred();
  disconnect(bluefruit, BLE_GAP_ROLE_CENTRAL, 2, 0x13);
  assert(deleted_connections == 22 && live_connections == 0);
}
