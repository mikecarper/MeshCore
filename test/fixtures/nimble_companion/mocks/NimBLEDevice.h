#pragma once
// Test doubles for the pinned NimBLE-Arduino 2.5.1 API. Firmware builds check
// the real headers/library; this harness injects controller and bonding faults
// into the actual SerialBLEInterface implementation.
#include <stdint.h>
#include <cstring>
#include <string>
#include <vector>
#include <memory>
#include <cassert>

constexpr uint8_t BLE_ADDR_PUBLIC = 0, BLE_ADDR_RANDOM = 1;
constexpr uint8_t BLE_OWN_ADDR_PUBLIC = 0, BLE_OWN_ADDR_RANDOM = 1;
constexpr uint8_t BLE_HS_IO_DISPLAY_ONLY = 0, BLE_HS_ADV_F_BREDR_UNSUP = 4;
struct ble_addr_t { uint8_t type = 0; uint8_t val[6] = {}; };
namespace NIMBLE_PROPERTY {
constexpr uint32_t READ = 1, NOTIFY = 2, READ_AUTHEN = 4, WRITE = 8, WRITE_AUTHEN = 16;
}
struct NimBLEAddress {
  ble_addr_t value;
  explicit NimBLEAddress(const ble_addr_t& address) : value(address) {}
  const ble_addr_t* getBase() const { return &value; }
};
struct NimBLEConnInfo {
  ble_addr_t identity = {BLE_ADDR_PUBLIC, {0x8e, 0x6e, 0xf9, 0xeb, 0x27, 0xb8}};
  bool encrypted = true, authenticated = true, bonded = true;
  uint16_t handle = 7;
  bool isEncrypted() const { return encrypted; }
  bool isAuthenticated() const { return authenticated; }
  bool isBonded() const { return bonded; }
  uint16_t getConnHandle() const { return handle; }
  NimBLEAddress getIdAddress() const { return NimBLEAddress(identity); }
};
class NimBLEServer;
class NimBLECharacteristic;
struct NimBLEServerCallbacks {
  virtual ~NimBLEServerCallbacks() = default;
  virtual uint32_t onPassKeyDisplay() { return 0; }
  virtual void onAuthenticationComplete(NimBLEConnInfo&) {}
  virtual void onConnect(NimBLEServer*, NimBLEConnInfo&) {}
  virtual void onDisconnect(NimBLEServer*, NimBLEConnInfo&, int) {}
  virtual void onMTUChange(uint16_t, NimBLEConnInfo&) {}
};
struct NimBLECharacteristicCallbacks {
  virtual ~NimBLECharacteristicCallbacks() = default;
  virtual void onWrite(NimBLECharacteristic*, NimBLEConnInfo&) {}
  virtual void onSubscribe(NimBLECharacteristic*, NimBLEConnInfo&, uint16_t) {}
};
struct NimBLEAdvertisementData {
  uint8_t flags = 0;
  bool setFlags(uint8_t value) { flags = value; return true; }
};
namespace fake_nimble {
inline bool initialized = false, persisted = true, accept_notification = true;
inline std::string failure;
inline std::vector<std::string> calls;
inline uint8_t address[6] = {}, own_type = BLE_OWN_ADDR_PUBLIC, io_cap = 255;
inline ble_addr_t allowlisted;
inline uint16_t mtu = 179;
inline uint32_t pin = 0;
inline bool bonding = false, mitm = false, sc = false;
inline std::vector<std::vector<uint8_t>> transmitted;
inline unsigned disconnects = 0;
inline unsigned notification_attempts = 0;
}
inline int ble_gap_wl_set(const ble_addr_t* peers, uint8_t count) {
  assert(count == 1);
  fake_nimble::allowlisted = *peers;
  return fake_nimble::failure == "allowlist" ? 1 : 0;
}
struct NimBLEAdvertising {
  bool active = false, scan_response = false, scan_filter = false, connect_filter = false;
  std::string name, service;
  bool start(uint32_t = 0, const NimBLEAddress* = nullptr) {
    fake_nimble::calls.push_back("advertise"); active = true; return true;
  }
  bool stop() { active = false; return true; }
  void enableScanResponse(bool value) { scan_response = value; }
  void setScanFilter(bool scan, bool connect) { scan_filter = scan; connect_filter = connect; }
  bool setAdvertisementData(const NimBLEAdvertisementData&) { name.clear(); service.clear(); return true; }
  bool setName(const std::string& value) { name = value; return true; }
  bool addServiceUUID(const char* value) { service = value; return true; }
};
struct NimBLECharacteristic {
  std::string uuid;
  uint32_t properties;
  NimBLECharacteristicCallbacks* callbacks = nullptr;
  std::vector<uint8_t> value;
  NimBLECharacteristic(const char* id, uint32_t props) : uuid(id), properties(props) {}
  void setCallbacks(NimBLECharacteristicCallbacks* cb) { callbacks = cb; }
  const std::vector<uint8_t> getValue() const { return value; }
  void setValue(const uint8_t* data, size_t size) { value.assign(data, data + size); }
  bool notify(uint16_t handle = 0xffff) const {
    assert(handle == 7);
    ++fake_nimble::notification_attempts;
    if (!fake_nimble::accept_notification) return false;
    fake_nimble::transmitted.push_back(value);
    return true;
  }
};
struct NimBLEService {
  std::vector<std::unique_ptr<NimBLECharacteristic>> characteristics;
  NimBLECharacteristic* createCharacteristic(const char* id, uint32_t props) {
    if (fake_nimble::failure == (characteristics.empty() ? "tx" : "rx")) return nullptr;
    characteristics.emplace_back(new NimBLECharacteristic(id, props));
    return characteristics.back().get();
  }
};
struct NimBLEServer {
  NimBLEAdvertising advertising;
  std::unique_ptr<NimBLEService> service;
  NimBLEServerCallbacks* callbacks = nullptr;
  bool owns_callbacks = true, auto_advertise = true, connected = false;
  ~NimBLEServer() { if (owns_callbacks) delete callbacks; }
  void setCallbacks(NimBLEServerCallbacks* cb, bool take_ownership = true) {
    callbacks = cb; owns_callbacks = take_ownership;
  }
  void advertiseOnDisconnect(bool value) { auto_advertise = value; }
  uint32_t displayPairingPasskey() {
    // NimBLE-Arduino 2.5.1 only invokes the display callback when the
    // configured static passkey is its 123456 sentinel.
    return fake_nimble::pin == 123456
        ? callbacks->onPassKeyDisplay() : fake_nimble::pin;
  }
  bool start() { return true; }
  uint8_t getConnectedCount() const { return connected; }
  uint16_t getPeerMTU(uint16_t) const { return fake_nimble::mtu; }
  bool disconnect(uint16_t) const { ++fake_nimble::disconnects; return true; }
  NimBLEAdvertising* getAdvertising() { return &advertising; }
  NimBLEService* createService(const char*) {
    if (fake_nimble::failure == "service") return nullptr;
    service.reset(new NimBLEService()); return service.get();
  }
};
struct NimBLEDevice {
  inline static std::unique_ptr<NimBLEServer> server;
  static bool init(const std::string&) {
    fake_nimble::calls.push_back("init");
    return fake_nimble::initialized = fake_nimble::failure != "init";
  }
  static bool deinit(bool clear = false) {
    fake_nimble::initialized = false;
    if (clear) server.reset();
    return true;
  }
  static bool deleteAllBonds() { fake_nimble::calls.push_back("clear-bonds"); return fake_nimble::failure != "clear-bonds"; }
  static bool deleteBond(const NimBLEAddress&) { return true; }
  static bool isBonded(const NimBLEAddress&) { return fake_nimble::persisted; }
  static bool setOwnAddr(const uint8_t* address) {
    fake_nimble::calls.push_back("address");
    memcpy(fake_nimble::address, address, 6);
    return fake_nimble::failure != "address";
  }
  static bool setOwnAddrType(uint8_t type) {
    fake_nimble::calls.push_back("address-type"); fake_nimble::own_type = type;
    return fake_nimble::failure != "address-type";
  }
  static bool setMTU(uint16_t mtu) { fake_nimble::mtu = mtu; return true; }
  static void setSecurityPasskey(uint32_t pin) { fake_nimble::pin = pin; }
  static void setSecurityIOCap(uint8_t value) { fake_nimble::io_cap = value; }
  static void setSecurityAuth(bool bond, bool mitm, bool sc) {
    fake_nimble::bonding = bond; fake_nimble::mitm = mitm; fake_nimble::sc = sc;
  }
  static NimBLEServer* createServer() {
    if (fake_nimble::failure == "server") return nullptr;
    if (!server) server.reset(new NimBLEServer());
    return server.get();
  }
};
#define BLEDevice NimBLEDevice
#define BLEServer NimBLEServer
#define BLEService NimBLEService
#define BLECharacteristic NimBLECharacteristic
#define BLEServerCallbacks NimBLEServerCallbacks
#define BLECharacteristicCallbacks NimBLECharacteristicCallbacks
#define BLEAddress NimBLEAddress
#define BLEAdvertising NimBLEAdvertising
#define BLEAdvertisementData NimBLEAdvertisementData
