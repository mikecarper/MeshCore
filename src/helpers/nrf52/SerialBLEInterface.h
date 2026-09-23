#pragma once

#include "../BaseSerialInterface.h"
#include "../BluetoothMac.h"
#include "../BleTxStallWatchdog.h"
#include "../UsbLogging.h"
#include "SecuritySessionTimer.h"
#include <bluefruit.h>
#include <atomic>
#if COMPANION_FEATURE_BLE_MOTA_SOURCE
#include "../BleMotaProtocol.h"
#include "../BleMotaStream.h"
#endif

#ifndef BLE_TX_POWER
#define BLE_TX_POWER 4
#endif

// Bluefruit's BANDWIDTH_MAX preset reserves a 100-unit connection event and
// three notification buffers. That is useful on boards with roomy RAM, but
// it exceeds the usable SoftDevice reservation of some bootloader layouts.
// Keep the existing preset as the default while allowing constrained boards
// to retain the 247-byte MTU with a smaller event/buffer reservation.
#ifndef COMPANION_BLE_PRPH_MTU
#define COMPANION_BLE_PRPH_MTU 247
#endif

#ifndef COMPANION_BLE_PRPH_EVENT_LENGTH
#define COMPANION_BLE_PRPH_EVENT_LENGTH 100
#endif

#ifndef COMPANION_BLE_PRPH_HVN_QUEUE
#define COMPANION_BLE_PRPH_HVN_QUEUE 3
#endif

#ifndef COMPANION_BLE_PRPH_WRCMD_QUEUE
#define COMPANION_BLE_PRPH_WRCMD_QUEUE 1
#endif

// Nordic UART is the required Companion service. The legacy Nordic DFU
// service is an optional extension that some constrained field combinations
// cannot register alongside the Companion GATT table.
#ifndef COMPANION_FEATURE_BLE_DFU
#define COMPANION_FEATURE_BLE_DFU 1
#endif

class SerialBLEInterface : public BaseSerialInterface {
#if COMPANION_FEATURE_BLE_DFU
  BLEDfu bledfu;
#endif
  BLEUart bleuart;
#if COMPANION_FEATURE_BLE_MOTA_SOURCE
  BLEService _mota_service = BLEService(mesh::ota::BLE_MOTA_SERVICE_UUID);
  BLECharacteristic _mota_request =
      BLECharacteristic(mesh::ota::BLE_MOTA_REQUEST_UUID);
  BLECharacteristic _mota_response =
      BLECharacteristic(mesh::ota::BLE_MOTA_RESPONSE_UUID);
  mesh::ota::BleMotaStream _mota_stream;
  // The normal Nordic UART service is the Companion transport.  mOTA is an
  // optional Full-Companion extension and must never take that transport down
  // if a fielded SoftDevice rejects one of its extra GATT attributes.
  bool _mota_available = false;
#endif
  bool _isEnabled;
  bool _begin_attempted = false;
  bool _begin_ready = false;
  char _begin_failure[96] = {};
  bool _isDeviceConnected;
  uint16_t _conn_handle;
  unsigned long _last_health_check;
  unsigned long _last_retry_attempt;
  ble_gap_addr_t _peer_address = {};
  bool _peer_address_valid;
  bool _bond_removed_for_connection;
  ble_gap_addr_t _successful_peer_address = {};
  bool _stealth_pair_once;
  bool _bonded_only;
  bool _bonded_only_configure_pending;
  mesh::companion::BluetoothPeerIdentity _pending_bonded_peer;
  std::atomic<bool> _advertisingSuppressed{false};
  std::atomic<bool> _bondedOnlyRecoveryPending{false};
  std::atomic<bool> _pairingRequestPending{false};
  std::atomic<bool> _companionDataSeen{false};
  std::atomic<bool> _successfulConnectionPending{false};
  std::atomic<uint32_t> _successfulConnectionStarted{0};
  SecuritySessionTimer _security_timer;
  SecuritySessionTimer _companion_start_timer;
  mesh::BleTxStallWatchdog _tx_stall_watchdog;
  mesh::BleDisconnectRecovery _tx_disconnect_recovery;

  struct Frame {
    uint8_t len;
    uint8_t buf[MAX_FRAME_SIZE];
  };

  #define FRAME_QUEUE_SIZE  12
  
  uint8_t send_queue_len;
  Frame send_queue[FRAME_QUEUE_SIZE];
  
  uint8_t recv_queue_len;
  Frame recv_queue[FRAME_QUEUE_SIZE];

  void clearBuffers();
  void shiftSendQueueLeft();
  void shiftRecvQueueLeft();
  size_t writeBleUartFrame(const Frame& frame);
  void recoverStalledTx(const char* cause);
  void serviceTxRecovery(uint32_t now);
  bool removeStoredBondForPeer(const char* cause);
  void noteSuccessfulConnection(const ble_gap_addr_t& peer_address);
  bool resolveSuccessfulPeer(
      mesh::companion::BluetoothPeerIdentity& peer) const;
  bool configureBondedOnlyAdvertising(
      const mesh::companion::BluetoothPeerIdentity& peer,
      bool require_stored_bond);
  void serviceBondedOnlyTransition();
  void requestBondedOnlyRecovery(const char* cause);
  bool advertisingAllowed() const;
  bool startAdvertising(const char* failure_cause);
  bool isValidConnection(uint16_t handle, bool requireWaitingForSecurity = false) const;
  bool isAdvertising() const;
  static void onConnect(uint16_t connection_handle);
  static void onDisconnect(uint16_t connection_handle, uint8_t reason);
  static void onSecured(uint16_t connection_handle);
  static bool onPairingPasskey(uint16_t connection_handle, uint8_t const passkey[6], bool match_request);
  static void onPairingComplete(uint16_t connection_handle, uint8_t auth_status);
  static void onBLEEvent(ble_evt_t* evt);
  static void onBleUartRX(uint16_t conn_handle);
#if COMPANION_FEATURE_BLE_MOTA_SOURCE
  static void onMotaResponse(uint16_t conn_handle,
                             BLECharacteristic* characteristic,
                             uint8_t* data, uint16_t length);
  static size_t sendMotaRequest(void* context, const uint8_t* data,
                                size_t length);
#endif

public:
  SerialBLEInterface() {
    _isEnabled = false;
    _isDeviceConnected = false;
    _conn_handle = BLE_CONN_HANDLE_INVALID;
    _last_health_check = 0;
    _last_retry_attempt = 0;
    _peer_address_valid = false;
    _bond_removed_for_connection = false;
    _stealth_pair_once = false;
    _bonded_only = false;
    _bonded_only_configure_pending = false;
    send_queue_len = 0;
    recv_queue_len = 0;
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
  // Kept after a failed begin so a normal Companion terminal can report the
  // cause even when early USB logging was not available.
  const char* beginFailure() const { return _begin_failure; }

  void disconnect();
  void loop() override;
  void enable() override;
  void disable() override;
  bool isEnabled() const override { return _isEnabled; }
  bool isConnected() const override;
  bool isReadBusy() const override;
  bool isWriteBusy() const override;
  bool hasPendingIO() const override;
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
#if COMPANION_FEATURE_BLE_MOTA_SOURCE
  Stream& motaStream() { return _mota_stream; }
  bool isMotaChannelReady();
  bool isMotaStreamActive() const { return _mota_stream.isActive(); }
  void setMotaStreamActive(bool active) {
    _mota_stream.setActive(active);
    if (active) {
      _companionDataSeen.store(true, std::memory_order_release);
      _companion_start_timer.cancel();
    }
  }
#endif
};

#if BLE_DEBUG_LOGGING && ARDUINO
  #include <Arduino.h>
  #define BLE_DEBUG_PRINT(F, ...) do { if (mesh::isUsbLoggingEnabled()) { mesh::usbLoggingPort().printf("BLE: " F, ##__VA_ARGS__); } } while(0)
  #define BLE_DEBUG_PRINTLN(F, ...) do { if (mesh::isUsbLoggingEnabled()) { mesh::usbLoggingPort().printf("BLE: " F "\n", ##__VA_ARGS__); } } while(0)
#else
  #define BLE_DEBUG_PRINT(...) {}
  #define BLE_DEBUG_PRINTLN(...) {}
#endif
