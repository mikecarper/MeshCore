#pragma once
#include <atomic>
#include <cstdint>
#include <cstring>

#define MESH_SENSOR_FINDMY 1
#define NRF52_PLATFORM 1
#define NRF_SUCCESS 0u
#define ERROR_NONE 0u
#define BLE_CONN_HANDLE_INVALID 0xffffu
#define BLE_GATT_ATT_MTU_DEFAULT 23
#define BLE_GAP_EVENT_LENGTH_MIN 2
#define BLE_GAP_ADV_TYPE_NONCONNECTABLE_NONSCANNABLE_UNDIRECTED 5
#define BLE_GAP_ADV_TYPE_CONNECTABLE_SCANNABLE_UNDIRECTED 1
#define BLE_GAP_ADDR_TYPE_RANDOM_STATIC 1
#define BLE_GAP_AD_TYPE_MANUFACTURER_SPECIFIC_DATA 255
#define BLE_GAP_AD_TYPE_COMPLETE_LOCAL_NAME 9
#define BLE_GAP_ADV_FLAGS_LE_ONLY_GENERAL_DISC_MODE 6
#define BLE_HCI_REMOTE_USER_TERMINATED_CONNECTION 19
#define MESH_DEBUG_PRINTLN(...) do {} while (false)

struct ble_gap_addr_t { uint8_t addr_type; uint8_t addr[6]; };
static ble_gap_addr_t current_address = { 0, { 1, 2, 3, 4, 5, 6 } };
static bool softdevice_enabled = false;
static bool fail_stop = false, fail_set_addr = false, fail_disconnect = false;
static bool fail_tx_power = false;
static unsigned begin_count = 0, disconnect_count = 0;
static int tx_power_dbm = -100;
static uint8_t captured_payload[29] = {};

struct BLEDfu { uint32_t begin() { return ERROR_NONE; } };
struct FakeAdvertising {
  bool running = false, restart = false;
  uint8_t type = BLE_GAP_ADV_TYPE_CONNECTABLE_SCANNABLE_UNDIRECTED;
  uint16_t interval = 0;
  bool isRunning() const { return running; }
  void restartOnDisconnect(bool enabled) { restart = enabled; }
  bool stop() { if (fail_stop) return false; running = false; return true; }
  bool start(uint16_t) { running = true; return true; }
  void clearData() {}
  void setType(uint8_t value) { type = value; }
  void setInterval(uint16_t value, uint16_t) { interval = value; }
  void setFastTimeout(uint16_t) {}
  bool addFlags(uint8_t) { return true; }
  bool addTxPower() { return true; }
  bool addData(uint8_t kind, const void* data, uint8_t len) {
    if (kind == BLE_GAP_AD_TYPE_MANUFACTURER_SPECIFIC_DATA && len == 29)
      memcpy(captured_payload, data, len);
    return true;
  }
};
struct FakePeriph {
  bool connected_ = false;
  uint8_t connected() const { return connected_ ? 1 : 0; }
  void setConnectCallback(void (*)(uint16_t)) {}
  void setDisconnectCallback(void (*)(uint16_t, uint8_t)) {}
};
struct FakeScanner { bool isRunning() const { return false; } };
struct FakeResponse { void clearData() {} bool addService(BLEDfu&) { return true; } };
struct FakeBluefruit {
  FakeAdvertising Advertising;
  FakePeriph Periph;
  FakeScanner Scanner;
  FakeResponse ScanResponse;
  void configPrphConn(int, int, int, int) {}
  bool begin(int, int) { ++begin_count; softdevice_enabled = true; return true; }
  void autoConnLed(bool) {}
  bool setTxPower(int value) { if (fail_tx_power) return false; tx_power_dbm = value; return true; }
  void setName(const char*) {}
};
static FakeBluefruit Bluefruit;
static BLEDfu bledfu;
static std::atomic<uint16_t> ota_conn_handle(BLE_CONN_HANDLE_INVALID);
static bool ota_active = false;
static bool ota_ble_started = false;

static uint32_t sd_ble_gap_addr_get(ble_gap_addr_t* out) { *out = current_address; return NRF_SUCCESS; }
static uint32_t sd_ble_gap_addr_set(const ble_gap_addr_t* value) {
  if (fail_set_addr || Bluefruit.Advertising.running || Bluefruit.Periph.connected_) return 1;
  current_address = *value; return NRF_SUCCESS;
}
static uint32_t sd_ble_gap_disconnect(uint16_t, uint8_t) {
  if (fail_disconnect) return 1;
  ++disconnect_count; return NRF_SUCCESS;
}
namespace mesh_nrf52 {
static uint32_t softdeviceIsEnabled(uint8_t& out) { out = softdevice_enabled; return NRF_SUCCESS; }
struct SleepState { void invalidate() {} };
static SleepState& sleepCycleState() { static SleepState state; return state; }
static void applyBluetoothLedPolicy() {}
static void bluetoothLedStarted() {}
}
class NRF52Board {
  char ota_name_storage_[8] = "test";
 public:
  char* ota_name = ota_name_storage_;
  bool isOTAUpdateRunning() const;
  bool startOTAUpdate(const char*, char[], bool);
  bool stopOTAUpdate(char[]);
};
static void format_ota_reply(char* reply) { strcpy(reply, "OTA ready"); }
