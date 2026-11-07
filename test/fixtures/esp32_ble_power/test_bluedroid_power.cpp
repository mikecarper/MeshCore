#include <helpers/esp32/SerialBLEInterface.h>
#include <cassert>
#include <new>
using namespace fake_bt;
static const uint8_t custom[]={0xc2,1,2,3,4,5};
static unsigned suites;
void* operator new(std::size_t size, const std::nothrow_t&) noexcept {
  return failure == "characteristics" ? nullptr : ::operator new(size);
}
void operator delete(void* ptr, const std::nothrow_t&) noexcept { ::operator delete(ptr); }
static void pointer_map_reproduces_cached_cccd_failure() {
  // Reversed heap addresses register RX before TX even when TX was added
  // first, exactly reproducing the physical2B->2D cached-handle failure.
  struct Reversed {
    BLECharacteristic rx{"6E400002", BLECharacteristic::PROPERTY_WRITE};
    BLECharacteristic tx{"6E400003", BLECharacteristic::PROPERTY_NOTIFY};
  } reversed;
  registerPointerOrderedCharacteristics({&reversed.tx, &reversed.rx});
  assert(reversed.rx.value_handle == 0x002a);
  assert(reversed.tx.cccd_handle == 0x002d);
  assert(reversed.tx.cccd_handle != 0x002b); // Old cached write is invalid.
  ++suites;
}
static void retire_global() {
  delete server; server=nullptr;
  delete fake_advertising; fake_advertising=nullptr;
  uuid_adds=0;
}
static void stopped(SerialBLEInterface& a) {
  a.disable(); test_millis+=1000; a.servicePowerState();
  assert(!a.isEnabled() && !a.isPowerTransitionPending());
  assert(controller==ESP_BT_CONTROLLER_STATUS_IDLE);
  assert(host==ESP_BLUEDROID_STATUS_UNINITIALIZED);
  assert(services==0 && characteristics==0 && descriptors==0);
#ifdef TEST_BLE_OWNED_SERVER
  assert(servers==0);
#else
  assert(servers==1); // Valid static library callback target, retired on next init.
#endif
}
static void good(SerialBLEInterface& a) {
  assert(a.isEnabled() && !a.isPowerTransitionPending());
  assert(servers==1 && services==1 && characteristics==2 && descriptors==1);
  assert(name=="MeshCore-legacy" && pin==246810);
  assert(memcmp(address,custom,6)==0 && fake_advertising->active);
  auto* tx = server->service->children[0];
  auto* rx = server->service->children[1];
  assert(tx->uuid == "6E400003-B5A3-F393-E0A9-E50E24DCCA9E");
  assert(rx->uuid == "6E400002-B5A3-F393-E0A9-E50E24DCCA9E");
  assert(reinterpret_cast<uintptr_t>(tx) < reinterpret_cast<uintptr_t>(rx));
  assert(tx->value_handle == 0x002a && tx->cccd_handle == 0x002b);
  assert(rx->value_handle == 0x002d); // Saved clients never chase heap order.
}
static void live_pin() {
  SerialBLEInterface a;
  assert(a.begin("MeshCore-", "legacy", 246810, custom));
  a.enable();
  const auto deletions = bond_deletions;
  const auto disconnect_count = disconnects;
  auto* original=server; original->connected=true;
  esp_ble_gatts_cb_param_t connection;
  original->callbacks->onConnect(original,&connection);
  BLEDevice::security->onAuthenticationComplete(esp_ble_auth_cmpl_t{});
  assert(a.isConnected());
  assert(a.setPin(135790));
  assert(pin==135790 && BLEDevice::security->onPassKeyRequest()==135790);
  assert(a.isConnected() && disconnects==disconnect_count && bond_deletions==deletions);
  failure="pin-update";
  assert(!a.setPin(246801));
  assert(pin==135790 && BLEDevice::security->onPassKeyRequest()==135790);
  failure.clear();
  assert(!a.setPin(1000000));
  original->connected=false;
  original->callbacks->onDisconnect(original);
  stopped(a);
  const auto before=pin_updates;
  assert(a.setPin(864209));
  assert(pin_updates==before); // Stack is stopped; next start applies it.
  a.enable();
  assert(pin==864209 && BLEDevice::security->onPassKeyRequest()==864209);
  assert(bond_deletions==deletions);
  stopped(a); retire_global(); ++suites;
}
static void repeated_cycles() {
  SerialBLEInterface a; assert(a.begin("MeshCore-","legacy",246810,custom));
  // Test initial saved-off as well as all subsequent live toggles.
  stopped(a);
  for(unsigned i=0;i<100;++i) {
    a.enable(); good(a); assert(bond_deletions==0); stopped(a);
#ifndef TEST_BLE_OWNED_SERVER
    assert(uuid_adds==1); // Reused static advertiser must not grow forever.
#endif
  }
  retire_global(); ++suites;
}
static void partial_stop_failures_and_late_callbacks() {
  for(const char* fault : {"stop-host","deinit-host","stop-controller","deinit-controller"}) {
    SerialBLEInterface a; assert(a.begin("MeshCore-","legacy",246810,custom)); a.enable(); good(a);
    auto* original=server; original->connected=true;
    esp_ble_gatts_cb_param_t connection;
    original->callbacks->onConnect(original,&connection);
    BLEDevice::security->onAuthenticationComplete(esp_ble_auth_cmpl_t{});
    assert(a.isConnected());
    a.disable(); assert(!a.isEnabled() && a.isPowerTransitionPending());
    assert(server==original && services==1 && characteristics==2);
    original->connected=false;
    failure=fault; test_millis+=1000; a.servicePowerState();
    assert(a.isPowerTransitionPending() && server==original && services==1);
    esp_ble_auth_cmpl_t failed; failed.success=false;
    BLEDevice::security->onAuthenticationComplete(failed);
    assert(bond_deletions==0);
    a.enable(); assert(!a.isEnabled() && a.isPowerTransitionPending());
    failure.clear(); test_millis+=1000; a.servicePowerState(); good(a); stopped(a); retire_global();
  }
  ++suites;
}
static void startup_failures() {
  for(const char* fault : {"init","server","service","characteristics"}) {
    SerialBLEInterface a; assert(a.begin("MeshCore-","legacy",246810,custom)); a.enable(); stopped(a);
    failure=fault; a.enable();
    assert(!a.isEnabled() && !a.isConnected()); a.servicePowerState();
    assert(!a.isPowerTransitionPending());
    assert(services==0 && characteristics==0 && descriptors==0);
    failure.clear(); a.enable(); good(a); stopped(a); retire_global();
  }
  ++suites;
}
#if MESH_BLE_AUTO_CACHE_REFRESH
static unsigned chained_events;
static void prior_handler(esp_gatts_cb_event_t, esp_gatt_if_t, esp_ble_gatts_cb_param_t*) {
  ++chained_events;
}
static void tick(SerialBLEInterface& a) { test_millis += 1000; a.servicePowerState(); }
static void emit(esp_gatts_cb_event_t event, esp_ble_gatts_cb_param_t& param, esp_gatt_if_t id=3) {
  assert(BLEDevice::m_customGattsHandler);
  in_callback=true;
  BLEDevice::m_customGattsHandler(event,id,&param);
  in_callback=false;
}
static void connect_cache(SerialBLEInterface& a, uint8_t peer=6) {
  server->connected=true;
  esp_ble_gatts_cb_param_t param; param.connect.remote_bda[5]=peer;
  server->callbacks->onConnect(server,&param);
  emit(ESP_GATTS_CONNECT_EVT,param);
  tick(a); // Requests MITM without relying on a possibly stale NUS handle.
}
static void auth_cache(uint8_t peer=6) {
  esp_ble_auth_cmpl_t auth; auth.bd_addr[5]=peer;
  BLEDevice::security->onAuthenticationComplete(auth);
}
static void disconnect_cache() {
  server->connected=false;
  server->callbacks->onDisconnect(server);
  esp_ble_gatts_cb_param_t param; emit(ESP_GATTS_DISCONNECT_EVT,param);
}
static void created_cache(int status=ESP_GATT_OK, esp_gatt_if_t id=3, bool correct=true, uint8_t instance=0) {
  esp_ble_gatts_cb_param_t param; uint16_t handle=marker_handle;
  param.add_attr_tab.status=status;
  memcpy(param.add_attr_tab.svc_uuid.uuid.uuid128,marker_attributes->att_desc.value,16);
  if(!correct) param.add_attr_tab.svc_uuid.uuid.uuid128[0]^=1;
  param.add_attr_tab.svc_inst_id=instance;
  param.add_attr_tab.handles=&handle;
  emit(ESP_GATTS_CREAT_ATTR_TAB_EVT,param,id);
  handle=0; // The backend must have copied the ephemeral callback array.
}
static void changed_cache(bool starting, int status=ESP_GATT_OK, uint16_t handle=0) {
  esp_ble_gatts_cb_param_t param;
  auto& change=starting ? param.start : param.del;
  change.status=status; change.service_handle=handle ? handle : marker_handle;
  emit(starting ? ESP_GATTS_START_EVT : ESP_GATTS_DELETE_EVT,param);
}
static void reset_cache_stats() {
  encryptions=cache_creates=cache_starts=cache_deletes=chained_events=0;
  failure.clear(); marker_attributes=nullptr;
  BLEDevice::setCustomGattsHandler(prior_handler);
}
static void verify_nus(SerialBLEInterface& a) {
  auto* tx=server->service->children[0]; auto* rx=server->service->children[1];
  uint8_t frame[]={22}; rx->setValue(frame,sizeof(frame));
  // A cached CCCD hitting RX is insufficient while actual TX is unsubscribed.
  esp_ble_gatts_cb_param_t param;
  rx->callbacks->onWrite(rx,&param); tick(a);
  tx->descriptor->setNotifications(true);
  rx->callbacks->onWrite(rx,&param); tick(a);
}
static void cache_async_migration_and_real_nus_proof() {
  reset_cache_stats();
  SerialBLEInterface a; assert(a.begin("MeshCore-","legacy",246810,custom)); a.enable();
  connect_cache(a); assert(encryptions==1 && cache_creates==0);
  auth_cache(7); tick(a); assert(cache_creates==0); // Stale auth from another peer.
  auth_cache(); tick(a); assert(cache_creates==1 && cache_starts==0);
  assert(marker_attributes->att_desc.value[0]==0x91); // Static queued data remains alive.
  created_cache(0,4); created_cache(0,3,false); created_cache(0,3,true,1);
  tick(a); assert(cache_starts==0);
  created_cache(); created_cache(); tick(a); assert(cache_starts==1);
  changed_cache(true,0,marker_handle+1); tick(a); assert(cache_deletes==0);
  changed_cache(true); changed_cache(true); tick(a);
  uint8_t stale_cccd[]={1,0};
  auto* rx=server->service->children[1]; rx->setValue(stale_cccd,sizeof(stale_cccd));
  esp_ble_gatts_cb_param_t stale_write;
  rx->callbacks->onWrite(rx,&stale_write); tick(a); // Old CCCD2D is now RX, actual TX remains off.
  disconnect_cache(); connect_cache(a); auth_cache(); tick(a);
  assert(cache_deletes==1); // Local START was not proof that this peer repaired its cache.
  changed_cache(false,1); a.servicePowerState(); assert(cache_deletes==1);
  test_millis+=999; a.servicePowerState(); assert(cache_deletes==1);
  ++test_millis; a.servicePowerState(); assert(cache_deletes==2); // Known failure retry.
  changed_cache(false); tick(a);
  verify_nus(a);
  disconnect_cache(); connect_cache(a); auth_cache(); tick(a);
  assert(cache_creates==1 && cache_deletes==2); // Real handle proof permits fast reconnect.
  disconnect_cache(); connect_cache(a,7); auth_cache(7); tick(a);
  assert(cache_creates==2); // A different peer still needs migration.
  created_cache(); tick(a); changed_cache(true); tick(a);
  good(a); disconnect_cache(); stopped(a);
  assert(BLEDevice::m_customGattsHandler==prior_handler && bond_deletions==0);
  assert(chained_events>0); retire_global(); BLEDevice::setCustomGattsHandler(nullptr); ++suites;
}
static void cache_disconnect_pending_timeout_and_teardown() {
  for(const char* stage : {"create","start","delete"}) {
    reset_cache_stats();
    SerialBLEInterface a; assert(a.begin("MeshCore-","legacy",246810,custom)); a.enable();
    connect_cache(a); auth_cache(); tick(a); assert(cache_creates==1);
    if(std::string(stage)!="create") {
      created_cache(); tick(a); assert(cache_starts==1);
      if(std::string(stage)=="delete") {
        // Completion while disconnected must not mark its peer cache repaired.
        disconnect_cache(); changed_cache(true); tick(a);
        connect_cache(a); auth_cache(); tick(a); assert(cache_deletes==1);
      }
    }
    test_millis+=6000; a.servicePowerState();
    const unsigned before=cache_creates+cache_starts+cache_deletes;
    tick(a); assert(cache_creates+cache_starts+cache_deletes==before); // No orphan retry.
    disconnect_cache(); connect_cache(a,7); auth_cache(7); tick(a);
    assert(cache_creates+cache_starts+cache_deletes==before);
    disconnect_cache(); stopped(a);
    assert(BLEDevice::m_customGattsHandler==prior_handler);
    esp_ble_gatts_cb_param_t late; emit(ESP_GATTS_START_EVT,late); // Restored hook only.
    a.enable(); good(a); stopped(a); retire_global();
    BLEDevice::setCustomGattsHandler(nullptr);
  }
  ++suites;
}
static void cache_success_progresses_without_timer_delay() {
  reset_cache_stats();
  SerialBLEInterface a; assert(a.begin("MeshCore-","legacy",246810,custom)); a.enable();
  connect_cache(a); const uint32_t connected_at=test_millis;
  assert(encryptions==1 && cache_creates==0);
  auth_cache(); a.servicePowerState(); assert(cache_creates==1);
  created_cache(); a.servicePowerState(); assert(cache_starts==1);
  changed_cache(true); a.servicePowerState();
  assert(test_millis==connected_at); // No artificial wait between successful phases.
  disconnect_cache(); stopped(a); retire_global();
  BLEDevice::setCustomGattsHandler(nullptr);

  for(const char* fault : {"encrypt","cache-create","cache-start"}) {
    reset_cache_stats();
    SerialBLEInterface b; assert(b.begin("MeshCore-","legacy",246810,custom)); b.enable();
    if(std::string(fault)=="encrypt") failure=fault;
    connect_cache(b);
    if(std::string(fault)!="encrypt") {
      auth_cache();
      if(std::string(fault)=="cache-create") failure=fault;
      b.servicePowerState();
      if(std::string(fault)=="cache-start") {
        created_cache(); failure=fault; b.servicePowerState();
      }
    }
    const unsigned attempts=encryptions+cache_creates+cache_starts;
    b.servicePowerState(); assert(encryptions+cache_creates+cache_starts==attempts);
    test_millis+=999; b.servicePowerState(); assert(encryptions+cache_creates+cache_starts==attempts);
    ++test_millis; b.servicePowerState(); assert(encryptions+cache_creates+cache_starts==attempts+1);
    failure.clear(); disconnect_cache(); stopped(b); retire_global();
    BLEDevice::setCustomGattsHandler(nullptr);
  }
  ++suites;
}
static void cache_api_and_async_failures_are_bounded() {
  for(const char* fault : {"encrypt","cache-create","async-create","malformed-create","cache-start"}) {
    reset_cache_stats();
    SerialBLEInterface a; assert(a.begin("MeshCore-","legacy",246810,custom)); a.enable();
    if(std::string(fault)=="encrypt") failure=fault;
    connect_cache(a); auth_cache();
    if(std::string(fault)=="cache-create") failure=fault;
    tick(a);
    if(std::string(fault)=="async-create") {
      for(unsigned i=0;i<3;++i) { created_cache(1); a.servicePowerState(); tick(a); }
    }
    if(std::string(fault)=="malformed-create") {
      esp_ble_gatts_cb_param_t param;
      memcpy(param.add_attr_tab.svc_uuid.uuid.uuid128,marker_attributes->att_desc.value,16);
      param.add_attr_tab.num_handle=2; // An unknown successful allocation cannot be retried safely.
      emit(ESP_GATTS_CREAT_ATTR_TAB_EVT,param);
    }
    if(std::string(fault)=="cache-start") { created_cache(); failure=fault; }
    tick(a); tick(a); tick(a); tick(a);
    const unsigned attempts=encryptions+cache_creates+cache_starts;
    tick(a); assert(encryptions+cache_creates+cache_starts==attempts);
    assert(encryptions<=3 && cache_creates<=3 && cache_starts<=3);
    failure.clear(); disconnect_cache(); stopped(a); retire_global();
    BLEDevice::setCustomGattsHandler(nullptr);
  }
  ++suites;
}
#endif
int main() {
  live_pin();
#if MESH_BLE_AUTO_CACHE_REFRESH
  cache_async_migration_and_real_nus_proof();
  cache_disconnect_pending_timeout_and_teardown();
  cache_api_and_async_failures_are_bounded();
  cache_success_progresses_without_timer_delay();
#endif
  pointer_map_reproduces_cached_cccd_failure();
  repeated_cycles(); partial_stop_failures_and_late_callbacks(); startup_failures();
  // The legacy library deliberately retains its static server/advertiser
  // until replacement. Release the final objects after the host has stopped.
  delete server; server=nullptr;
  delete fake_advertising; fake_advertising=nullptr;
  assert(servers==0 && services==0 && characteristics==0 && descriptors==0);
  printf("%u lifecycle suites passed\n",suites);
}
