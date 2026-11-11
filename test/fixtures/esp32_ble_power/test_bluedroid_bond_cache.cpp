#include <helpers/esp32/SerialBLEInterface.h>
#include <cassert>
#include <cstdio>
#include <cstring>

using namespace fake_bt;

static unsigned staging_resets;
extern "C" void mesh_bl_narrow_reset_staging(void) { ++staging_resets; }
extern "C" void mesh_bl_narrow_sdk_guard(void) {}

static unsigned cache_changes() {
  return cache_creates + cache_starts + cache_deletes;
}

static void emit(esp_gatts_cb_event_t event, esp_ble_gatts_cb_param_t& param) {
  assert(BLEDevice::m_customGattsHandler != nullptr);
  in_callback = true;
  BLEDevice::m_customGattsHandler(event, 3, &param);
  in_callback = false;
}

static void connect(SerialBLEInterface& adapter, uint8_t peer = 6) {
  assert(!server->connected && adapter.isEnabled());
  esp_ble_gatts_cb_param_t event;
  event.connect.remote_bda[5] = peer;
  // The real Arduino wrapper updates its connected count after this callback.
  server->callbacks->onConnect(server, &event);
  server->connected = true;
  emit(ESP_GATTS_CONNECT_EVT, event);
  test_millis += 1000;
  adapter.servicePowerState();
}

static void authenticate(SerialBLEInterface& adapter, uint8_t peer = 6,
                         bool success = true) {
  esp_ble_auth_cmpl_t auth;
  auth.bd_addr[5] = peer;
  auth.success = success;
  BLEDevice::security->onAuthenticationComplete(auth);
  assert(adapter.isConnected() == success);
  if (success) adapter.servicePowerState();
}

static void write_nus() {
  auto* rx = server->service->children[1];
  uint8_t command[] = {22, 14};
  rx->setValue(command, sizeof(command));
  esp_ble_gatts_cb_param_t write;
  rx->callbacks->onWrite(rx, &write);
}

static void round_trip(SerialBLEInterface& adapter) {
  auto* tx = server->service->children[0];
  tx->descriptor->setNotifications(true);
  write_nus();
  uint8_t received[MAX_FRAME_SIZE] = {};
  assert(adapter.checkRecvFrame(received) == 2);
  assert(received[0] == 22 && received[1] == 14);
  uint8_t response[] = {13, 14, 1, 2};
  assert(adapter.writeFrame(response, sizeof(response)) == sizeof(response));
  test_millis += 1000;
  adapter.checkRecvFrame(received);
  assert(tx->value.size() == sizeof(response));
  assert(memcmp(tx->value.data(), response, sizeof(response)) == 0);
  // Let the production cache policy observe authenticated use of both handles.
  adapter.servicePowerState();
}

static void disconnect_and_rebuild(SerialBLEInterface& adapter) {
  const unsigned before = staging_resets;
  const unsigned before_stops = stops;
  esp_ble_gatts_cb_param_t event;
  server->callbacks->onDisconnect(server);
  server->callbacks->onDisconnect(server, &event);
  emit(ESP_GATTS_DISCONNECT_EVT, event);
  // The real wrapper retires its connection after invoking the callbacks.
  server->connected = false;
  assert(!adapter.isConnected());
  test_millis += 1000;
  adapter.servicePowerState();
  assert(staging_resets == before + 1 && stops > before_stops);
  assert(adapter.isEnabled() && !adapter.isPowerTransitionPending());
  assert(server->service->children[0]->value_handle == 0x002a);
  assert(server->service->children[0]->cccd_handle == 0x002b);
  assert(server->service->children[1]->value_handle == 0x002d);
  assert(bond_deletions == 0);
}

static void await_cache_fallback(SerialBLEInterface& adapter, unsigned before) {
  for (unsigned i = 0; i < 6 && cache_changes() == before; ++i) {
    test_millis += 1000;
    adapter.servicePowerState();
  }
  assert(cache_changes() > before);
}

static void cleanup(SerialBLEInterface& adapter) {
  if (server->connected) disconnect_and_rebuild(adapter);
  adapter.disable();
  test_millis += 1000;
  adapter.servicePowerState();
  assert(!adapter.isEnabled() && !adapter.isPowerTransitionPending());
  delete server;
  server = nullptr;
  delete fake_advertising;
  fake_advertising = nullptr;
  assert(servers == 0 && services == 0 && characteristics == 0 && descriptors == 0);
  assert(BLEDevice::m_customGattsHandler == nullptr && bond_deletions == 0);
}

static void normal_handshake_precedes_cache_recovery() {
  SerialBLEInterface adapter;
  assert(adapter.begin("MeshCore-", "reconnect", 246810));
  adapter.enable();
  connect(adapter);
  authenticate(adapter);
  round_trip(adapter);
  const unsigned before = cache_changes();
  disconnect_and_rebuild(adapter);
  connect(adapter);
  authenticate(adapter);
  // Every authenticated connection gets time to use its unchanged NUS handles,
  // including a same-peer reconnect after the full host was rebuilt.
  assert(cache_changes() == before);
  round_trip(adapter);
  for (unsigned i = 0; i < 6; ++i) {
    test_millis += 1000;
    adapter.servicePowerState();
  }
  assert(cache_changes() == before && "valid current-link NUS handshake triggered cache recovery");

  // Old proof cannot hide a stale cache on a new connection, even for the same
  // peer. If this link never completes NUS setup, retain bounded recovery.
  disconnect_and_rebuild(adapter);
  connect(adapter);
  authenticate(adapter);
  assert(cache_changes() == before);
  await_cache_fallback(adapter, before);

  // A different authenticated peer also retains recovery.
  disconnect_and_rebuild(adapter);
  connect(adapter, 7);
  authenticate(adapter, 7);
  const unsigned before_other_peer = cache_changes();
  await_cache_fallback(adapter, before_other_peer);
  cleanup(adapter);
}

static void unproven_peer_keeps_cache_recovery(bool authenticate_first) {
  SerialBLEInterface adapter;
  assert(adapter.begin("MeshCore-", "reconnect", 246810));
  adapter.enable();
  connect(adapter);
  if (authenticate_first) authenticate(adapter);
  else server->service->children[0]->descriptor->setNotifications(true);
  // Authenticated RX alone cannot prove that TX is subscribed. An unauthenticated
  // link must not earn proof even when the descriptor appears subscribed.
  write_nus();
  adapter.servicePowerState();
  if (!authenticate_first) {
    uint8_t received[MAX_FRAME_SIZE] = {};
    assert(adapter.checkRecvFrame(received) == 0);
    assert(adapter.writeFrame(received, 1) == 0);
    const unsigned before = cache_changes();
    test_millis += 6000;
    adapter.servicePowerState();
    assert(cache_changes() == before);
  }
  disconnect_and_rebuild(adapter);
  connect(adapter);
  authenticate(adapter);
  const unsigned before = cache_changes();
  await_cache_fallback(adapter, before);
  cleanup(adapter);
}

static void failed_authentication_cannot_prove_nus() {
  SerialBLEInterface adapter;
  assert(adapter.begin("MeshCore-", "reconnect", 246810));
  adapter.enable();
  connect(adapter);
  authenticate(adapter, 6, false);
  server->service->children[0]->descriptor->setNotifications(true);
  write_nus();
  uint8_t received[MAX_FRAME_SIZE] = {};
  assert(adapter.checkRecvFrame(received) == 0);
  assert(adapter.writeFrame(received, 1) == 0);
  disconnect_and_rebuild(adapter);
  connect(adapter);
  authenticate(adapter);
  const unsigned before = cache_changes();
  await_cache_fallback(adapter, before);
  cleanup(adapter);
}

static void fixed_authenticated_grace_deadline(bool wrap) {
  SerialBLEInterface adapter;
  assert(adapter.begin("MeshCore-", "deadline", 246810));
  adapter.enable();
  if (wrap) test_millis = UINT32_MAX - 2500;
  connect(adapter);
  authenticate(adapter);
  const unsigned before = cache_changes();
  // Repeated successful authentication is not another five-second lease.
  test_millis += 3000;
  authenticate(adapter);
  assert(cache_changes() == before);
  test_millis += 1999;
  adapter.servicePowerState();
  assert(cache_changes() == before);
  ++test_millis;
  adapter.servicePowerState();
  assert(cache_changes() > before);
  cleanup(adapter);
}

static void disconnect_starts_a_fresh_grace() {
  SerialBLEInterface adapter;
  assert(adapter.begin("MeshCore-", "new-session", 246810));
  adapter.enable();
  connect(adapter);
  authenticate(adapter);
  const unsigned before = cache_changes();
  test_millis += 4000;
  adapter.servicePowerState();
  assert(cache_changes() == before);
  disconnect_and_rebuild(adapter);
  connect(adapter);
  authenticate(adapter);
  // The old connection's deadline has elapsed, but the new one's has not.
  assert(cache_changes() == before);
  test_millis += 4999;
  adapter.servicePowerState();
  assert(cache_changes() == before);
  ++test_millis;
  adapter.servicePowerState();
  assert(cache_changes() > before);
  cleanup(adapter);
}

int main() {
  normal_handshake_precedes_cache_recovery();
  unproven_peer_keeps_cache_recovery(false);
  unproven_peer_keeps_cache_recovery(true);
  failed_authentication_cannot_prove_nus();
  fixed_authenticated_grace_deadline(false);
  fixed_authenticated_grace_deadline(true);
  disconnect_starts_a_fresh_grace();
  puts("7 lifecycle suites passed: combined bond recovery and GATT cache policy");
}
