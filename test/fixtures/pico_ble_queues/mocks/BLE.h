#pragma once

#include <btstack.h>
#include <array>

class BLEServer;
class BLECharacteristic;
class BLECharacteristicCallbacks {
public:
  virtual void onWrite(BLECharacteristic*) {}
};
class BLEServerCallbacks {
public:
  virtual void onConnect(BLEServer*) {}
  virtual void onDisconnect(BLEServer*) {}
};
struct BLEUUID {
  std::array<uint8_t, 16> bytes;
  explicit BLEUUID(const uint8_t* value) { std::copy(value, value + 16, bytes.begin()); }
};
constexpr unsigned BLERead = 2, BLEWrite = 8, BLENotify = 16;
constexpr unsigned BLESecurityJustWorks = 1;

class BLECharacteristic {
protected:
  uint16_t _valueHandle = 1, con_handle = 0;
  bool _notificationEnabled = false;
public:
  BLEUUID uuid;
  BLECharacteristicCallbacks* callbacks = nullptr;
  fake_pico::Bytes input;
  BLECharacteristic(BLEUUID value, unsigned, const char*, uint8_t, uint8_t) : uuid(value) {}
  void setCallbacks(BLECharacteristicCallbacks* value) { callbacks = value; }
  size_t valueLen() const { return input.size(); }
  const void* valueData() const { return input.data(); }
  void setConHandle(uint16_t value) { con_handle = value; }
  void disconnected() { _notificationEnabled = false; } // Real core retains con_handle.
  void setNotify(bool value) { _notificationEnabled = value; }
  void hostWrite(const fake_pico::Bytes& value) {
    ++fake_pico::callback_depth; input = value;
    if (callbacks) callbacks->onWrite(this);
    --fake_pico::callback_depth;
  }
};

class BLEService {
public:
  std::vector<BLECharacteristic*> characteristics;
  explicit BLEService(BLEUUID) {}
  void addCharacteristic(BLECharacteristic* value) { characteristics.push_back(value); }
};

namespace fake_pico {
inline bool advertising_requested = false, advertising_active = false;
inline unsigned advertising_starts = 0, advertising_stops = 0;
}
class BLEServer {
public:
  BLEServerCallbacks* callbacks = nullptr;
  std::vector<BLEService*> services;
  void setName(const char*) {}
  void addService(BLEService* value) { services.push_back(value); }
  bool setCallbacks(BLEServerCallbacks* value) { callbacks = value; return true; }
  void connect(uint16_t handle = 1) {
    fake_pico::connection_live = true; fake_pico::handle = handle;
    fake_pico::advertising_active = false;
    for (auto* service : services) for (auto* characteristic : service->characteristics)
      characteristic->setConHandle(handle);
    ++fake_pico::callback_depth;
    if (callbacks) callbacks->onConnect(this);
    --fake_pico::callback_depth;
  }
  void disconnectEvent() {
    fake_pico::connection_live = false; fake_pico::pending.clear();
    for (auto* service : services) for (auto* characteristic : service->characteristics)
      characteristic->disconnected();
    // Match BLEServer.cpp: callbacks see cleared CCCD but stale char handles;
    // server's own handle is not cleared until after onDisconnect returns.
    ++fake_pico::callback_depth;
    if (callbacks) callbacks->onDisconnect(this);
    --fake_pico::callback_depth;
    fake_pico::handle = 0;
    fake_pico::advertising_active = fake_pico::advertising_requested;
  }
};
class BLEClass {
  BLEServer server_;
public:
  void setSecurity(unsigned) {}
  void begin(const char*) {}
  BLEServer* server() { return &server_; }
  void startAdvertising() {
    assert(!fake_pico::connection_live
        && "advertising and ATT rebuild must wait until the old link closes");
    ++fake_pico::advertising_starts;
    fake_pico::advertising_requested = fake_pico::advertising_active = true;
  }
  void stopAdvertising() {
    ++fake_pico::advertising_stops;
    fake_pico::advertising_requested = fake_pico::advertising_active = false;
  }
};
inline BLEClass BLE;
