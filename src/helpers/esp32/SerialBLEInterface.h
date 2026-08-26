#pragma once

#include "../BaseSerialInterface.h"
#include "../BleTxStallWatchdog.h"
#include "../UsbLogging.h"
#include <BLEDevice.h>
#include <BLEServer.h>
#include <BLEUtils.h>
#include <BLE2902.h>
#include <atomic>
#include <freertos/FreeRTOS.h>
#include <freertos/queue.h>

class SerialBLEInterface : public BaseSerialInterface, BLESecurityCallbacks, BLEServerCallbacks, BLECharacteristicCallbacks {
  BLEServer *pServer;
  BLEService *pService;
  BLECharacteristic * pTxCharacteristic;
  BLE2902 *pTxDescriptor;
  bool deviceConnected;
  bool oldDeviceConnected;
  bool notifySucceeded;
  std::atomic<bool> notificationsEnabled{false};
  bool _isEnabled;
  uint16_t last_conn_id;
  uint32_t _pin_code;
  uint32_t _last_write;
  uint32_t _adv_restart_started;
  bool _adv_restart_pending;
  mesh::BleTxStallWatchdog _tx_stall_watchdog;
  mesh::BleDisconnectRecovery _tx_disconnect_recovery;
  std::atomic<bool> _tx_reset_pending{false};
  std::atomic<bool> _pairingRequestPending{false};

  struct Frame {
    uint8_t len;
    uint8_t buf[MAX_FRAME_SIZE];
  };

  #define FRAME_QUEUE_SIZE  4
  StaticQueue_t recv_queue_state;
  uint8_t recv_queue_storage[FRAME_QUEUE_SIZE * sizeof(Frame)];
  QueueHandle_t recv_queue;
  int send_queue_len;
  Frame send_queue[FRAME_QUEUE_SIZE];

  void clearBuffers();
  void servicePendingTxReset();
  void scheduleAdvertisingRestart(uint32_t now);
  void recoverStalledTx(const char* cause);
  void serviceTxRecovery(uint32_t now);

protected:
  // BLESecurityCallbacks methods
  uint32_t onPassKeyRequest() override;
  void onPassKeyNotify(uint32_t pass_key) override;
  bool onConfirmPIN(uint32_t pass_key) override;
  bool onSecurityRequest() override;
  #if defined(CONFIG_NIMBLE_ENABLED)
  void onAuthenticationComplete(ble_gap_conn_desc* desc) override;
  #else
  void onAuthenticationComplete(esp_ble_auth_cmpl_t cmpl) override;
  #endif

  // BLEServerCallbacks methods
  void onConnect(BLEServer* pServer) override;
  #if defined(CONFIG_NIMBLE_ENABLED)
  void onConnect(BLEServer* pServer, ble_gap_conn_desc* desc) override;
  void onMtuChanged(BLEServer* pServer, ble_gap_conn_desc* desc, uint16_t mtu) override;
  #else
  void onConnect(BLEServer* pServer, esp_ble_gatts_cb_param_t *param) override;
  void onMtuChanged(BLEServer* pServer, esp_ble_gatts_cb_param_t* param) override;
  #endif
  void onDisconnect(BLEServer* pServer) override;

  // BLECharacteristicCallbacks methods
  #if defined(CONFIG_NIMBLE_ENABLED)
  void onWrite(BLECharacteristic* pCharacteristic, ble_gap_conn_desc* desc) override;
  void onSubscribe(BLECharacteristic* pCharacteristic, ble_gap_conn_desc* desc,
                   uint16_t subValue) override;
  #else
  void onWrite(BLECharacteristic* pCharacteristic, esp_ble_gatts_cb_param_t* param) override;
  #endif
  void onStatus(BLECharacteristic* pCharacteristic, Status status, uint32_t code) override;

public:
  SerialBLEInterface() {
    pServer = NULL;
    pService = NULL;
    pTxCharacteristic = NULL;
    pTxDescriptor = NULL;
    deviceConnected = false;
    oldDeviceConnected = false;
    notifySucceeded = false;
    notificationsEnabled.store(false, std::memory_order_relaxed);
    _adv_restart_started = 0;
    _adv_restart_pending = false;
    _isEnabled = false;
    _last_write = 0;
    last_conn_id = 0;
    recv_queue = xQueueCreateStatic(
      FRAME_QUEUE_SIZE, sizeof(Frame), recv_queue_storage, &recv_queue_state
    );
    send_queue_len = 0;
  }

  /**
   * init the BLE interface.
   * @param prefix   a prefix for the device name
   * @param name  a name for the device (combined with prefix); "@@MAC" uses the hardware address
   * @param pin_code   the BLE security pin
   */
  bool begin(const char* prefix, const char* name, uint32_t pin_code);

  // BaseSerialInterface methods
  void enable() override;
  void disable() override;
  bool isEnabled() const override { return _isEnabled; }

  bool isConnected() const override;

  bool isReadBusy() const override;
  bool isWriteBusy() const override;
  bool hasPendingIO() const override {
    return uxQueueMessagesWaiting(recv_queue) > 0 || send_queue_len > 0;
  }
  bool takePairingRequest() override {
    return _pairingRequestPending.exchange(false, std::memory_order_acq_rel);
  }
  size_t writeFrame(const uint8_t src[], size_t len) override;
  size_t checkRecvFrame(uint8_t dest[]) override;
};

#if BLE_DEBUG_LOGGING && ARDUINO
  #include <Arduino.h>
  #define BLE_DEBUG_PRINT(F, ...) do { if (mesh::isUsbLoggingEnabled()) { mesh::usbLoggingPort().printf("BLE: " F, ##__VA_ARGS__); } } while(0)
  #define BLE_DEBUG_PRINTLN(F, ...) do { if (mesh::isUsbLoggingEnabled()) { mesh::usbLoggingPort().printf("BLE: " F "\n", ##__VA_ARGS__); } } while(0)
#else
  #define BLE_DEBUG_PRINT(...) {}
  #define BLE_DEBUG_PRINTLN(...) {}
#endif
