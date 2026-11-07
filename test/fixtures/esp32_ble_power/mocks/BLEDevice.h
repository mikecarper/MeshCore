#pragma once
#include <Arduino.h>
#include <string>
#include <vector>
#include <map>
#include <cstring>
#include <cassert>
using esp_err_t = int;
constexpr esp_err_t ESP_OK = 0;
using esp_gatt_if_t = uint8_t;
constexpr esp_gatt_if_t ESP_GATT_IF_NONE = 0xff;
constexpr int ESP_GATT_OK=0, ESP_UUID_LEN_16=2, ESP_UUID_LEN_128=16;
constexpr int ESP_GATT_UUID_PRI_SERVICE=0x2800, ESP_GATT_AUTO_RSP=1, ESP_GATT_PERM_READ=1;
constexpr int ESP_BLE_SEC_ENCRYPT_MITM=3;
enum esp_gatts_cb_event_t { ESP_GATTS_CONNECT_EVT, ESP_GATTS_DISCONNECT_EVT,
  ESP_GATTS_CREAT_ATTR_TAB_EVT, ESP_GATTS_START_EVT, ESP_GATTS_DELETE_EVT };
struct esp_bt_uuid_t { uint16_t len=16; union { uint16_t uuid16; uint8_t uuid128[16]; } uuid{}; };
struct esp_gatts_attr_db_t {
  struct { uint8_t auto_rsp; } attr_control;
  struct { uint16_t uuid_length; uint8_t* uuid_p; uint16_t perm,max_length,length; uint8_t* value; } att_desc;
};
enum esp_bt_controller_status_t { ESP_BT_CONTROLLER_STATUS_IDLE, ESP_BT_CONTROLLER_STATUS_INITED, ESP_BT_CONTROLLER_STATUS_ENABLED };
enum esp_bluedroid_status_t { ESP_BLUEDROID_STATUS_UNINITIALIZED, ESP_BLUEDROID_STATUS_INITIALIZED, ESP_BLUEDROID_STATUS_ENABLED };
using esp_bd_addr_t = uint8_t[6];
using esp_ble_wl_addr_type_t = int;
using esp_gatt_perm_t = int;
constexpr int BLE_ADDR_TYPE_PUBLIC=0, BLE_ADDR_TYPE_RANDOM=1, BLE_WL_ADDR_TYPE_PUBLIC=0, BLE_WL_ADDR_TYPE_RANDOM=1;
constexpr int ESP_LE_KEY_PID=1, ESP_BLE_ADV_FLAG_BREDR_NOT_SPT=4;
constexpr int ESP_GATT_PERM_READ_ENC_MITM=1, ESP_GATT_PERM_WRITE_ENC_MITM=2;
constexpr int ESP_IO_CAP_OUT=0, ESP_LE_AUTH_REQ_SC_MITM_BOND=15;
constexpr int ESP_BLE_SM_SET_STATIC_PASSKEY=20;
struct esp_ble_auth_cmpl_t { bool success=true; int addr_type=0, fail_reason=0; uint8_t bd_addr[6]={1,2,3,4,5,6}; };
struct esp_ble_gatts_cb_param_t {
  struct { uint16_t conn_id=7; uint8_t remote_bda[6]={1,2,3,4,5,6}; } connect, mtu;
  struct { int status=0; esp_bt_uuid_t svc_uuid; uint8_t svc_inst_id=0; uint16_t num_handle=1; uint16_t* handles=nullptr; } add_attr_tab;
  struct { int status=0; uint16_t service_handle=0; } start, del;
};
using gatts_event_handler=void(*)(esp_gatts_cb_event_t,esp_gatt_if_t,esp_ble_gatts_cb_param_t*);
struct esp_ble_bond_dev_t {
  uint8_t bd_addr[6]={1,2,3,4,5,6};
  struct { unsigned key_mask=0; struct { uint8_t static_addr[6]={}; int addr_type=0; } pid_key; } bond_key;
};
class BLEServer;
class BLECharacteristic;
namespace fake_bt {
inline esp_bt_controller_status_t controller=ESP_BT_CONTROLLER_STATUS_IDLE;
inline esp_bluedroid_status_t host=ESP_BLUEDROID_STATUS_UNINITIALIZED;
inline std::string failure, name;
inline bool initialized=false, persisted=true;
inline unsigned servers=0, services=0, characteristics=0, descriptors=0;
inline unsigned bond_deletions=0, disconnects=0, stops=0, uuid_adds=0;
inline uint8_t address[6]={};
inline uint32_t pin=0;
inline unsigned pin_updates=0;
inline unsigned encryptions=0, cache_creates=0, cache_starts=0, cache_deletes=0;
inline bool in_callback=false;
inline const esp_gatts_attr_db_t* marker_attributes=nullptr;
inline esp_gatt_if_t marker_if=0;
inline uint16_t marker_handle=0x40;
inline int esp_ble_set_encryption_result=0;
inline BLEServer* server=nullptr;
}
inline esp_bt_controller_status_t esp_bt_controller_get_status() { return fake_bt::controller; }
inline esp_bluedroid_status_t esp_bluedroid_get_status() { return fake_bt::host; }
inline int esp_bluedroid_disable() {
  ++fake_bt::stops;
  if(fake_bt::failure=="stop-host") return 1;
  fake_bt::host=ESP_BLUEDROID_STATUS_INITIALIZED; return ESP_OK;
}
inline int esp_bluedroid_deinit() {
  if(fake_bt::failure=="deinit-host") return 1;
  fake_bt::host=ESP_BLUEDROID_STATUS_UNINITIALIZED; return ESP_OK;
}
inline int esp_bt_controller_disable() {
  if(fake_bt::failure=="stop-controller") return 1;
  fake_bt::controller=ESP_BT_CONTROLLER_STATUS_INITED; return ESP_OK;
}
inline int esp_bt_controller_deinit() {
  if(fake_bt::failure=="deinit-controller") return 1;
  fake_bt::controller=ESP_BT_CONTROLLER_STATUS_IDLE; return ESP_OK;
}
inline int esp_ble_get_bond_device_num() { return fake_bt::persisted ? 1 : 0; }
inline int esp_ble_gap_set_security_param(int type, void* value, uint8_t length) {
  assert(type==ESP_BLE_SM_SET_STATIC_PASSKEY && length==sizeof(uint32_t));
  ++fake_bt::pin_updates;
  if (fake_bt::failure=="pin-update") return 1;
  memcpy(&fake_bt::pin, value, sizeof(fake_bt::pin));
  return ESP_OK;
}
inline int esp_ble_get_bond_device_list(int*, esp_ble_bond_dev_t* dest) { *dest=esp_ble_bond_dev_t{}; return ESP_OK; }
inline int esp_ble_remove_bond_device(uint8_t*) { ++fake_bt::bond_deletions; return ESP_OK; }
inline int esp_ble_gap_clear_whitelist() { return ESP_OK; }
inline int esp_ble_gap_update_whitelist(bool, esp_bd_addr_t, esp_ble_wl_addr_type_t) { return ESP_OK; }
inline int esp_ble_set_encryption(uint8_t*, int mode) {
  assert(!fake_bt::in_callback && mode==ESP_BLE_SEC_ENCRYPT_MITM);
  ++fake_bt::encryptions;
  return fake_bt::failure=="encrypt" ? 1 : ESP_OK;
}
inline int esp_ble_gatts_create_attr_tab(const esp_gatts_attr_db_t* db, esp_gatt_if_t id,
                                        uint16_t count, uint8_t instance) {
  assert(!fake_bt::in_callback && count==1 && instance==0);
  assert(db->attr_control.auto_rsp==ESP_GATT_AUTO_RSP);
  assert(*reinterpret_cast<uint16_t*>(db->att_desc.uuid_p)==ESP_GATT_UUID_PRI_SERVICE);
  assert(db->att_desc.length==16);
  ++fake_bt::cache_creates; fake_bt::marker_attributes=db; fake_bt::marker_if=id;
  return fake_bt::failure=="cache-create" ? 1 : ESP_OK;
}
inline int esp_ble_gatts_start_service(uint16_t handle) {
  assert(!fake_bt::in_callback && handle==fake_bt::marker_handle);
  ++fake_bt::cache_starts;
  return fake_bt::failure=="cache-start" ? 1 : ESP_OK;
}
inline int esp_ble_gatts_delete_service(uint16_t handle) {
  assert(!fake_bt::in_callback && handle==fake_bt::marker_handle);
  ++fake_bt::cache_deletes;
  return fake_bt::failure=="cache-delete" ? 1 : ESP_OK;
}
class BLESecurityCallbacks {
public:
  virtual ~BLESecurityCallbacks()=default;
  virtual uint32_t onPassKeyRequest() { return 0; }
  virtual void onPassKeyNotify(uint32_t) {}
  virtual bool onConfirmPIN(uint32_t) { return false; }
  virtual bool onSecurityRequest() { return false; }
  virtual void onAuthenticationComplete(esp_ble_auth_cmpl_t) {}
};
class BLEServerCallbacks {
public:
  virtual ~BLEServerCallbacks()=default;
  virtual void onConnect(BLEServer*) {}
  virtual void onConnect(BLEServer*, esp_ble_gatts_cb_param_t*) {}
  virtual void onDisconnect(BLEServer*) {}
  virtual void onMtuChanged(BLEServer*, esp_ble_gatts_cb_param_t*) {}
};
class BLECharacteristicCallbacks {
public:
  enum Status { SUCCESS_NOTIFY, ERROR_GATT };
  virtual ~BLECharacteristicCallbacks()=default;
  virtual void onWrite(BLECharacteristic*, esp_ble_gatts_cb_param_t*) {}
  virtual void onStatus(BLECharacteristic*, Status, uint32_t) {}
};
class BLESecurity {
public:
  void setStaticPIN(uint32_t pin) { fake_bt::pin=pin; }
  void setCapability(int) {}
  void setAuthenticationMode(int) {}
};
class BLE2902 {
  bool subscribed=false;
public:
  BLE2902() { ++fake_bt::descriptors; }
  ~BLE2902() { --fake_bt::descriptors; }
  void setNotifications(bool v) { subscribed=v; }
  bool getNotifications() const { return subscribed; }
  void setAccessPermissions(int) {}
};
class BLEAdvertisementData { public: void setFlags(int) {} };
class BLEAdvertising {
public:
  bool active=false, filtered=false;
  void start() { active=true; }
  void stop() { active=false; }
  void addServiceUUID(const char*) { ++fake_bt::uuid_adds; }
  void setDeviceAddress(esp_bd_addr_t addr, int) { memcpy(fake_bt::address,addr,6); }
  void setScanResponse(bool) {}
  void setScanFilter(bool a,bool b) { filtered=a&&b; }
  void setAdvertisementData(BLEAdvertisementData&) {}
};
class BLECharacteristic {
public:
  static constexpr uint32_t PROPERTY_READ=1, PROPERTY_NOTIFY=2, PROPERTY_WRITE=4;
  BLECharacteristicCallbacks* callbacks=nullptr;
  std::vector<uint8_t> value;
  std::string uuid;
  uint32_t properties;
  uint16_t value_handle = 0, cccd_handle = 0;
  BLE2902* descriptor=nullptr;
  BLECharacteristic(const char* id = "", uint32_t props = 0)
      : uuid(id), properties(props) { ++fake_bt::characteristics; }
  ~BLECharacteristic() { --fake_bt::characteristics; }
  void setCallbacks(BLECharacteristicCallbacks* cb) { callbacks=cb; }
  void setAccessPermissions(int) {}
  void addDescriptor(BLE2902* value) { descriptor=value; }
  void setValue(uint8_t* data,size_t len) { value.assign(data,data+len); }
  uint8_t* getData() { return value.data(); }
  size_t getLength() { return value.size(); }
  void notify() { callbacks->onStatus(this,BLECharacteristicCallbacks::SUCCESS_NOTIFY,0); }
};
// Exact legacy SDK behavior: registration follows a std::map keyed by
// object address, rather than UUID or characteristic creation order.
inline void registerPointerOrderedCharacteristics(
    const std::vector<BLECharacteristic*>& characteristics) {
  std::map<BLECharacteristic*, bool> pointer_map;
  for (auto* characteristic : characteristics) pointer_map[characteristic] = true;
  uint16_t next = 0x0029;
  for (const auto& entry : pointer_map) {
    auto* characteristic = entry.first;
    characteristic->value_handle = next + 1;
    next += 2;
    characteristic->cccd_handle = 0;
    if (characteristic->properties & BLECharacteristic::PROPERTY_NOTIFY)
      characteristic->cccd_handle = next++;
  }
}
class BLEService {
public:
  BLEService() { ++fake_bt::services; }
#ifdef TEST_BLE_OWNED_SERVER
private:
  friend class BLEDevice;
  ~BLEService() { --fake_bt::services; }
public:
#else
  ~BLEService() { --fake_bt::services; }
#endif
  std::vector<BLECharacteristic*> children;
  unsigned made=0;
  void addCharacteristic(BLECharacteristic* child) { children.push_back(child); }
  BLECharacteristic* createCharacteristic(const char* id,uint32_t properties) {
    const char* phase = made++ ? "rx" : "tx";
    if (fake_bt::failure==phase) return nullptr;
    auto* child = new BLECharacteristic(id, properties);
    addCharacteristic(child);
    return child;
  }
  void start() { registerPointerOrderedCharacteristics(children); }
};
inline BLEAdvertising* fake_advertising=nullptr;
class BLEServer {
public:
  bool connected=false;
  BLEServerCallbacks* callbacks=nullptr;
  BLEServer() { ++fake_bt::servers; }
  ~BLEServer() {
    assert(fake_bt::host!=ESP_BLUEDROID_STATUS_ENABLED || fake_bt::server!=this);
    --fake_bt::servers;
  }
  BLEService* service = nullptr;
  BLEService* createService(const char*) {
    return service = fake_bt::failure=="service" ? nullptr : new BLEService;
  }
  BLEAdvertising* getAdvertising() { if(!fake_advertising) fake_advertising=new BLEAdvertising; return fake_advertising; }
  void setCallbacks(BLEServerCallbacks* cb) { callbacks=cb; }
  unsigned getConnectedCount() const { return connected; }
  void disconnect(uint16_t) { ++fake_bt::disconnects; }
  uint16_t getConnId() { return 7; }
  uint16_t getPeerMTU(uint16_t) { return 179; }
};
class BLEDevice {
public:
  inline static BLESecurityCallbacks* security=nullptr;
  inline static gatts_event_handler m_customGattsHandler=nullptr;
  static void setCustomGattsHandler(gatts_event_handler handler) { m_customGattsHandler=handler; }
  static void init(const std::string& name) {
    fake_bt::name=name;
    if(fake_bt::failure=="init") return;
    fake_bt::initialized=true;
    fake_bt::host=ESP_BLUEDROID_STATUS_ENABLED;
    fake_bt::controller=ESP_BT_CONTROLLER_STATUS_ENABLED;
  }
  static BLEServer* createServer() {
    fake_bt::server=fake_bt::failure=="server" ? nullptr : new BLEServer;
    return fake_bt::server;
  }
#ifdef TEST_BLE_OWNED_SERVER
  static BLEServer* getServer() { return fake_bt::server; }
#endif
  static void deinit(bool release) {
    assert(!release); // BLE memory release is irreversible until reboot.
    if(!fake_bt::initialized) return;
#ifdef TEST_BLE_OWNED_SERVER
    assert(fake_bt::host!=ESP_BLUEDROID_STATUS_ENABLED);
    if (fake_bt::server) delete fake_bt::server->service;
    delete fake_bt::server; fake_bt::server=nullptr;
    delete fake_advertising; fake_advertising=nullptr;
#endif
    fake_bt::host=ESP_BLUEDROID_STATUS_UNINITIALIZED;
    fake_bt::controller=ESP_BT_CONTROLLER_STATUS_IDLE;
    fake_bt::initialized=false;
  }
  static void setSecurityCallbacks(BLESecurityCallbacks* cb) { security=cb; }
  static void setMTU(uint16_t) {}
};
