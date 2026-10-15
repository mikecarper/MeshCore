#pragma once

#include "../BaseSerialInterface.h"
#include "../BluetoothMac.h"
#include "../BleTxStallWatchdog.h"
#include "../UsbLogging.h"
#if defined(MESH_USE_NIMBLE_ARDUINO)
  #include <NimBLEDevice.h>
#else
  #include <BLEDevice.h>
  #include <BLEServer.h>
  #include <BLEUtils.h>
  #include <BLE2902.h>
#endif
#if defined(MESH_USE_NIMBLE_ARDUINO) || defined(CONFIG_NIMBLE_ENABLED)
  #define MESH_BLE_USES_NIMBLE 1
#else
  #define MESH_BLE_USES_NIMBLE 0
#endif
#include <atomic>
#include <freertos/FreeRTOS.h>
#include <freertos/queue.h>

class SerialBLEInterface : public BaseSerialInterface,
#if !defined(MESH_USE_NIMBLE_ARDUINO)
    public BLESecurityCallbacks,
#endif
    public BLEServerCallbacks, public BLECharacteristicCallbacks {
  BLEServer *pServer;
  BLEService *pService;
  BLECharacteristic * pTxCharacteristic;
#if !defined(MESH_USE_NIMBLE_ARDUINO)
  BLE2902 *pTxDescriptor;
#endif
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
  std::atomic<bool> _successfulConnectionPending{false};
  std::atomic<uint32_t> _successfulConnectionStarted{0};
  mesh::companion::BluetoothPeerIdentity _successfulPeer;
  bool _stealth_pair_once;
  bool _bonded_only;
  std::atomic<bool> _advertisingSuppressed{false};
  std::atomic<bool> _bondedOnlyRecoveryPending{false};

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
  bool hasRequiredReplyCapacity() const;
  void servicePendingTxReset();
  void scheduleAdvertisingRestart(uint32_t now);
  void recoverStalledTx(const char* cause);
  void serviceTxRecovery(uint32_t now);
  void noteSuccessfulConnection(
      const mesh::companion::BluetoothPeerIdentity& peer);
  bool resolveSuccessfulPeer(
      mesh::companion::BluetoothPeerIdentity& peer) const;
  bool configureBondedOnlyAdvertising(
      const mesh::companion::BluetoothPeerIdentity& peer,
      bool require_stored_bond);
  void requestBondedOnlyRecovery(const char* cause);
  bool advertisingAllowed() const;

protected:
#if defined(MESH_USE_NIMBLE_ARDUINO)
  uint32_t onPassKeyDisplay() override;
  void onAuthenticationComplete(NimBLEConnInfo& info) override;
  void onConnect(BLEServer* server, NimBLEConnInfo& info) override;
  void onDisconnect(BLEServer* server, NimBLEConnInfo& info, int reason) override;
  void onMTUChange(uint16_t mtu, NimBLEConnInfo& info) override;
  void onWrite(BLECharacteristic* characteristic, NimBLEConnInfo& info) override;
  void onSubscribe(BLECharacteristic* characteristic, NimBLEConnInfo& info,
                   uint16_t value) override;
#else
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
#endif

public:
  SerialBLEInterface() {
    pServer = NULL;
    pService = NULL;
    pTxCharacteristic = NULL;
#if !defined(MESH_USE_NIMBLE_ARDUINO)
    pTxDescriptor = NULL;
#endif
    deviceConnected = false;
    oldDeviceConnected = false;
    notifySucceeded = false;
    notificationsEnabled.store(false, std::memory_order_relaxed);
    _adv_restart_started = 0;
    _adv_restart_pending = false;
    _isEnabled = false;
    _stealth_pair_once = false;
    _bonded_only = false;
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
   * @param custom_address optional human-order BLE random-static address
   * @param clear_bonds clear saved peer bonds before accepting connections
   * @param stealth_pair_once suppress general advertising after first pairing
   * @param bonded_only_peer optional peer allowed to reconnect in stealth mode
   */
  bool begin(const char* prefix, const char* name, uint32_t pin_code,
             const uint8_t* custom_address = nullptr,
             bool clear_bonds = false, bool stealth_pair_once = false,
             const mesh::companion::BluetoothPeerIdentity*
                 bonded_only_peer = nullptr);

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
  bool takeSuccessfulConnection(
      mesh::companion::BluetoothPeerIdentity* peer = nullptr);
  bool enableBondedOnlyAdvertising(
      const mesh::companion::BluetoothPeerIdentity& peer);
  void cancelStealthPairingTransition();
  bool takeBondedOnlyRecovery() {
    return _bondedOnlyRecoveryPending.exchange(
        false, std::memory_order_acq_rel);
  }
  size_t writeFrame(const uint8_t src[], size_t len) override;
  size_t checkRecvFrame(uint8_t dest[]) override;
};

#if BLE_DEBUG_LOGGING && ARDUINO
  #include <Arduino.h>
  #define BLE_DEBUG_PRINT(F, ...) do { if (mesh::isUsbDebugLoggingEnabled()) { mesh::usbLoggingPort().printf("BLE: " F, ##__VA_ARGS__); } } while(0)
  #define BLE_DEBUG_PRINTLN(F, ...) do { if (mesh::isUsbDebugLoggingEnabled()) { mesh::usbLoggingPort().printf("BLE: " F "\n", ##__VA_ARGS__); } } while(0)
#else
  #define BLE_DEBUG_PRINT(...) {}
  #define BLE_DEBUG_PRINTLN(...) {}
#endif
