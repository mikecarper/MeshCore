#include <helpers/esp32/SerialBLEInterface.h>
#include <cassert>

// This fixture tests ordinary GATT connect/disconnect bookkeeping only.
extern "C" void mesh_bl_narrow_reset_staging(void) {}
extern "C" void mesh_bl_narrow_sdk_guard(void) {}

int main() {
  using namespace fake_bt;
  SerialBLEInterface adapter;
  assert(adapter.begin("MeshCore-", "test", 123456, nullptr));
  adapter.enable();
  assert(server != nullptr);

  esp_ble_gatts_cb_param_t accepted;
  accepted.connect.conn_id = 7;
  server->callbacks->onConnect(server, &accepted);
  server->connected = true;  // Real wrapper increments after the callback.

  esp_ble_gatts_cb_param_t rejected;
  rejected.connect.conn_id = 8;
  server->callbacks->onConnect(server, &rejected);
  assert(server->disconnect_ids.back() == 8);

  server->disconnect_ids.clear();
  adapter.disable();
  assert(server->disconnect_ids.size() == 2);
  assert(server->disconnect_ids[0] == 7);
  assert(server->disconnect_ids[1] == 8);

  esp_ble_gatts_cb_param_t disconnected;
  disconnected.disconnect.conn_id = 8;
  server->callbacks->onDisconnect(server, &disconnected);
  test_millis += 1000;
  server->disconnect_ids.clear();
  adapter.servicePowerState();
  assert(server->disconnect_ids.size() == 1);
  assert(server->disconnect_ids[0] == 7);

  disconnected.disconnect.conn_id = 7;
  server->callbacks->onDisconnect(server, &disconnected);
  server->connected = false;
  test_millis += 1000;
  adapter.servicePowerState();
  assert(!adapter.isPowerTransitionPending());

  // Retire an ordinary disconnected session before accepting a reused ID.
  adapter.enable();
  server->callbacks->onConnect(server, &accepted);
  server->connected = true;
  BLEDevice::security->onAuthenticationComplete(esp_ble_auth_cmpl_t{});
  assert(adapter.isConnected());
  server->callbacks->onDisconnect(server, &disconnected);
  assert(!adapter.isConnected());
  server->connected = false;
  test_millis += 1000;
  adapter.servicePowerState();
  assert(!adapter.isPowerTransitionPending());
  server->callbacks->onConnect(server, &accepted);
  server->connected = true;
  BLEDevice::security->onAuthenticationComplete(esp_ble_auth_cmpl_t{});
  assert(adapter.isConnected());
}
