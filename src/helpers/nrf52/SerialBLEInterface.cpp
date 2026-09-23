#include "SerialBLEInterface.h"
#include "../BluetoothMac.h"
#include "../CompanionFrameQueue.h"
#include "SoftDeviceState.h"
#include <stdio.h>
#include <string.h>
#include "ble_gap.h"
#include "ble_hci.h"
#include <utility/bonding.h>

// Magic numbers came from actual testing
#define BLE_HEALTH_CHECK_INTERVAL  10000  // Advertising watchdog check every 10 seconds
#define BLE_RETRY_THROTTLE_MS      250    // Throttle retries to 250ms when queue buildup detected
#define BLE_BOND_PERSIST_TIMEOUT_MS 15000 // Bound first-pair transition recovery

// Connection parameters (units: interval=1.25ms, timeout=10ms)
#define BLE_MIN_CONN_INTERVAL      12     // 15ms
#define BLE_MAX_CONN_INTERVAL      24     // 30ms
#define BLE_SLAVE_LATENCY          4
#define BLE_CONN_SUP_TIMEOUT       200    // 2000ms

// Advertising parameters
#define BLE_ADV_INTERVAL_MIN       32     // 20ms (units: 0.625ms)
#define BLE_ADV_INTERVAL_MAX       244    // 152.5ms (units: 0.625ms)
#define BLE_ADV_FAST_TIMEOUT       30     // seconds

// RX drain buffer size for overflow protection
#define BLE_RX_DRAIN_BUF_SIZE      32

static SerialBLEInterface* instance = nullptr;

#if defined(COMPANION_RADIO_FULL) && COMPANION_RADIO_FULL
// Bluefruit's bonded CCCD flash write resets some nRF52 Full Companions.
// Retain the small SoftDevice system-attribute image in RAM for reconnects
// during this boot. A reboot intentionally discards it; the client can then
// subscribe normally without a risky flash write.
static constexpr uint16_t CCCD_RAM_CACHE_MAX = 128;
static constexpr uint32_t CCCD_SYS_ATTR_FLAGS =
    BLE_GATTS_SYS_ATTR_FLAG_SYS_SRVCS | BLE_GATTS_SYS_ATTR_FLAG_USR_SRVCS;
struct CccdRamCache {
  uint8_t attrs[CCCD_RAM_CACHE_MAX] = {};
  uint16_t length = 0;
  ble_gap_addr_t peer = {};
  std::atomic<bool> valid{false};
};
static CccdRamCache cccd_ram_cache;
static bool cccd_written_this_connection = false;

static bool cccdPeersMatch(const ble_gap_addr_t& a,
                           const ble_gap_addr_t& b) {
  if (a.addr_type == b.addr_type
      && memcmp(a.addr, b.addr, sizeof(a.addr)) == 0) return true;

  // iPhones may reconnect from a different resolvable private address. The
  // stored bond maps both addresses to the same stable peer identity.
  ble_gap_addr_t a_lookup = a;
  ble_gap_addr_t b_lookup = b;
  bond_keys_t a_keys;
  bond_keys_t b_keys;
  return bond_load_keys(BLE_GAP_ROLE_PERIPH, &a_lookup, &a_keys)
      && bond_load_keys(BLE_GAP_ROLE_PERIPH, &b_lookup, &b_keys)
      && a_keys.peer_id.id_addr_info.addr_type
             == b_keys.peer_id.id_addr_info.addr_type
      && memcmp(a_keys.peer_id.id_addr_info.addr,
                b_keys.peer_id.id_addr_info.addr,
                sizeof(a_keys.peer_id.id_addr_info.addr)) == 0;
}

bool mesh_nrf52_restore_ram_cccd(uint16_t handle,
                                  const ble_gap_addr_t* peer) {
  if (!cccd_ram_cache.valid.load(std::memory_order_acquire)
      || cccd_written_this_connection || !peer
      || !cccdPeersMatch(cccd_ram_cache.peer, *peer)) {
    BLE_DEBUG_PRINTLN("CCCD RAM not restored at initialization: valid=%u written=%u",
                      (unsigned)cccd_ram_cache.valid.load(std::memory_order_relaxed),
                      (unsigned)cccd_written_this_connection);
    return false;
  }
  // The first sys_attr_set on a connection must contain the saved CCCD.
  // Bluefruit's no-file fallback initializes everything to off otherwise.
  const uint32_t status = sd_ble_gatts_sys_attr_set(
      handle, cccd_ram_cache.attrs, cccd_ram_cache.length,
      CCCD_SYS_ATTR_FLAGS);
  if (status != NRF_SUCCESS) {
    BLE_DEBUG_PRINTLN("CCCD RAM initialization failed: %lu",
                      (unsigned long)status);
    cccd_ram_cache.valid.store(false, std::memory_order_release);
    return false;
  }
  BLE_DEBUG_PRINTLN("CCCD RAM initialized: len=%u",
                    (unsigned)cccd_ram_cache.length);
  return true;
}

static void captureCccdInRam(uint16_t handle, const ble_gap_addr_t& peer) {
  cccd_ram_cache.valid.store(false, std::memory_order_release);
  uint16_t length = sizeof(cccd_ram_cache.attrs);
  const uint32_t status = sd_ble_gatts_sys_attr_get(
      handle, cccd_ram_cache.attrs, &length, CCCD_SYS_ATTR_FLAGS);
  // The SoftDevice SVC writes through this pointer, but its GCC wrapper has
  // no memory clobber. Force LTO to reload the returned length from memory.
  __asm__ __volatile__("" ::: "memory");
  if (status != NRF_SUCCESS || length == 0
      || length > CCCD_RAM_CACHE_MAX) {
    BLE_DEBUG_PRINTLN("CCCD RAM capture: status=%lu len=%u",
                      (unsigned long)status, (unsigned)length);
    return;
  }
  cccd_ram_cache.length = length;
  cccd_ram_cache.peer = peer;
  cccd_ram_cache.valid.store(true, std::memory_order_release);
  BLE_DEBUG_PRINTLN("CCCD RAM captured: len=%u", (unsigned)length);
}

static void captureCccdDeferred(uint16_t handle, ble_gap_addr_t* peer) {
  if (peer) captureCccdInRam(handle, *peer);
}
#endif

static bool isBondAuthenticationFailure(uint8_t reason) {
  return reason == BLE_HCI_AUTHENTICATION_FAILURE ||
         reason == BLE_HCI_STATUS_CODE_PIN_OR_KEY_MISSING ||
         reason == BLE_HCI_CONN_TERMINATED_DUE_TO_MIC_FAILURE;
}

void SerialBLEInterface::onConnect(uint16_t connection_handle) {
  BLE_DEBUG_PRINTLN("SerialBLEInterface: connected handle=0x%04X", connection_handle);
  if (instance) {
    instance->_pairingRequestPending.store(false, std::memory_order_release);
    instance->_conn_handle = connection_handle;
    instance->_isDeviceConnected = false;
    instance->_security_timer.start(millis());
    instance->clearBuffers();
#if COMPANION_FEATURE_BLE_MOTA_SOURCE
    instance->setMotaStreamActive(false);
#endif
  }
}

void SerialBLEInterface::onDisconnect(uint16_t connection_handle, uint8_t reason) {
  BLE_DEBUG_PRINTLN("SerialBLEInterface: disconnected handle=0x%04X reason=%u", connection_handle, reason);
  if (instance) {
    if (instance->_conn_handle == connection_handle) {
      instance->_pairingRequestPending.store(false, std::memory_order_release);
      instance->_conn_handle = BLE_CONN_HANDLE_INVALID;
      instance->_isDeviceConnected = false;
      instance->_security_timer.cancel();
      instance->clearBuffers();
#if COMPANION_FEATURE_BLE_MOTA_SOURCE
      instance->setMotaStreamActive(false);
#endif
    }
  }
}

#if COMPANION_FEATURE_BLE_MOTA_SOURCE
void SerialBLEInterface::onMotaResponse(
    uint16_t conn_handle, BLECharacteristic* characteristic,
    uint8_t* data, uint16_t length) {
  if (!instance || characteristic != &instance->_mota_response
      || instance->_conn_handle != conn_handle || !instance->isConnected()
      || !instance->_mota_stream.isActive()) {
    return;
  }

  if (length == 0 || length > mesh::ota::BLE_MOTA_RESPONSE_MAX
      || !instance->_mota_stream.pushRx(data, length)) {
    // A response that cannot fit intact would make the byte stream ambiguous.
    // Disable the link so the current transaction times out and the main loop
    // detaches it instead of consuming a partial or injected frame.
    instance->setMotaStreamActive(false);
  }
}

size_t SerialBLEInterface::sendMotaRequest(void* context,
                                           const uint8_t* data,
                                           size_t length) {
  SerialBLEInterface* self = static_cast<SerialBLEInterface*>(context);
  if (!self || !data || length == 0
      || length > mesh::ota::BLE_MOTA_REQUEST_MAX
      || !self->isMotaChannelReady() || !self->_mota_stream.isActive()) {
    return 0;
  }
  return self->_mota_request.notify(self->_conn_handle, data, length)
             ? length : 0;
}
#endif

void SerialBLEInterface::onSecured(uint16_t connection_handle) {
  BLE_DEBUG_PRINTLN("SerialBLEInterface: onSecured handle=0x%04X", connection_handle);
  if (instance) {
    if (instance->isValidConnection(connection_handle, true)) {
      BLEConnection* conn = Bluefruit.Connection(connection_handle);
      if (conn == nullptr || !conn->secured()) {
        BLE_DEBUG_PRINTLN("SerialBLEInterface: security update did not secure the link");
        instance->_isDeviceConnected = false;
        instance->_security_timer.cancel();
        if (conn != nullptr && conn->bonded()) {
          instance->removeStoredBondForPeer("unsecured link");
        }
        if (instance->_bonded_only) {
          instance->requestBondedOnlyRecovery("unsecured bonded-only link");
        }
        instance->disconnect();
        return;
      }

      instance->_isDeviceConnected = true;
      if (conn->bonded()) {
        instance->noteSuccessfulConnection(conn->getPeerAddr());
      } else {
        BLE_DEBUG_PRINTLN(
            "SerialBLEInterface: secured connection was not bonded");
      }
      instance->_security_timer.cancel();
      
      // Connection interval units: 1.25ms, supervision timeout units: 10ms
      // Apple: "The product will not read or use the parameters in the Peripheral Preferred Connection Parameters characteristic."
      // So we explicitly set it here to make Android & Apple match
      ble_gap_conn_params_t conn_params;
      conn_params.min_conn_interval = BLE_MIN_CONN_INTERVAL;
      conn_params.max_conn_interval = BLE_MAX_CONN_INTERVAL;
      conn_params.slave_latency = BLE_SLAVE_LATENCY;
      conn_params.conn_sup_timeout = BLE_CONN_SUP_TIMEOUT;
      
      uint32_t err_code = sd_ble_gap_conn_param_update(connection_handle, &conn_params);
      if (err_code == NRF_SUCCESS) {
        BLE_DEBUG_PRINTLN("Connection parameter update requested: %u-%ums interval, latency=%u, %ums timeout",
                         conn_params.min_conn_interval * 5 / 4,  // convert to ms (1.25ms units)
                         conn_params.max_conn_interval * 5 / 4,
                         conn_params.slave_latency,
                         conn_params.conn_sup_timeout * 10);  // convert to ms (10ms units)
      } else {
        BLE_DEBUG_PRINTLN("Failed to request connection parameter update: %lu", err_code);
      }
    } else {
      BLE_DEBUG_PRINTLN("onSecured: ignoring stale/duplicate callback");
    }
  }
}

bool SerialBLEInterface::onPairingPasskey(uint16_t connection_handle, uint8_t const passkey[6], bool match_request) {
  (void)passkey;
  BLE_DEBUG_PRINTLN("SerialBLEInterface: pairing passkey request match=%d", match_request);
  if (instance && instance->isValidConnection(connection_handle)) {
    instance->_pairingRequestPending.store(true, std::memory_order_release);
  }
  return true;
}

void SerialBLEInterface::onPairingComplete(uint16_t connection_handle, uint8_t auth_status) {
  BLE_DEBUG_PRINTLN("SerialBLEInterface: pairing complete handle=0x%04X status=%u", connection_handle, auth_status);
  if (instance) {
    if (instance->isValidConnection(connection_handle)) {
      if (auth_status == BLE_GAP_SEC_STATUS_SUCCESS) {
        BLE_DEBUG_PRINTLN("SerialBLEInterface: pairing successful");
        BLEConnection* conn = Bluefruit.Connection(connection_handle);
        if (conn != nullptr && conn->secured()) {
          // Bluefruit may invoke onSecured before it marks a newly paired
          // connection as bonded. Record the completed pairing here as well;
          // resolveSuccessfulPeer() will keep the event pending until the
          // deferred bond write can be loaded.
          instance->noteSuccessfulConnection(conn->getPeerAddr());
        }
      } else {
        BLE_DEBUG_PRINTLN("SerialBLEInterface: pairing failed, clearing stale bond and disconnecting");
        if (instance->_bonded_only) {
          instance->requestBondedOnlyRecovery("pairing failure");
        }
        BLEConnection* conn = Bluefruit.Connection(connection_handle);
        if (conn != nullptr && conn->bonded()) {
          instance->removeStoredBondForPeer("pairing failure");
        }
        instance->_isDeviceConnected = false;
        instance->_security_timer.cancel();
        instance->disconnect();
      }
    } else {
      BLE_DEBUG_PRINTLN("onPairingComplete: ignoring stale callback");
    }
  }
}

void SerialBLEInterface::onBLEEvent(ble_evt_t* evt) {
  if (!instance) return;

  if (evt->header.evt_id == BLE_GAP_EVT_CONNECTED) {
    ble_gap_evt_connected_t const* connected = &evt->evt.gap_evt.params.connected;
    if (connected->role == BLE_GAP_ROLE_PERIPH) {
      instance->_peer_address = connected->peer_addr;
      instance->_peer_address_valid = true;
      instance->_bond_removed_for_connection = false;
#if defined(COMPANION_RADIO_FULL) && COMPANION_RADIO_FULL
      cccd_written_this_connection = false;
      BLE_DEBUG_PRINTLN("CCCD RAM connect: cached=%u",
                        (unsigned)cccd_ram_cache.valid.load(std::memory_order_relaxed));
#endif
    }
  } else if (evt->header.evt_id == BLE_GAP_EVT_CONN_SEC_UPDATE) {
    uint16_t conn_handle = evt->evt.gap_evt.conn_handle;
    BLEConnection* conn = Bluefruit.Connection(conn_handle);
    if (conn != nullptr && conn->connected() && !conn->secured()) {
      BLE_DEBUG_PRINTLN("CONN_SEC_UPDATE: link is not secured, bonded=%d", conn->bonded());
      if (conn->bonded()) {
        instance->removeStoredBondForPeer("failed bond encryption");
      }
      if (instance->_bonded_only) {
        instance->requestBondedOnlyRecovery("failed bond encryption");
      }
      instance->_isDeviceConnected = false;
      instance->_security_timer.cancel();
      sd_ble_gap_disconnect(conn_handle, BLE_HCI_REMOTE_USER_TERMINATED_CONNECTION);
    }
#if defined(COMPANION_RADIO_FULL) && COMPANION_RADIO_FULL
  } else if (evt->header.evt_id == BLE_GATTS_EVT_WRITE) {
    const ble_gatts_evt_write_t& write = evt->evt.gatts_evt.params.write;
    if (write.uuid.type == BLE_UUID_TYPE_BLE
        && write.uuid.uuid == BLE_UUID_DESCRIPTOR_CLIENT_CHAR_CONFIG
        && instance->_peer_address_valid) {
      cccd_written_this_connection = true;
      BLE_DEBUG_PRINTLN("CCCD RAM write: len=%u value=%u",
                        (unsigned)write.len,
                        (unsigned)(write.len ? write.data[0] : 0));
      // Mirror Bluefruit's deferred CCCD save timing, but copy only to RAM.
      // Reading during the BLE event is too early: sys_attr_get succeeds but
      // returns the pre-write CCCD even though notifyEnabled() is already on.
      cccd_ram_cache.valid.store(false, std::memory_order_release);
      if (!ada_callback(&instance->_peer_address,
                        sizeof(instance->_peer_address), captureCccdDeferred,
                        evt->evt.gatts_evt.conn_handle,
                        &instance->_peer_address)) {
        BLE_DEBUG_PRINTLN("CCCD RAM capture could not be queued");
      }
    }
#endif
  } else if (evt->header.evt_id == BLE_GATTS_EVT_SYS_ATTR_MISSING) {
    BLE_DEBUG_PRINTLN("CCCD RAM system attributes missing");
  } else if (evt->header.evt_id == BLE_GAP_EVT_DISCONNECTED) {
    ble_gap_evt_disconnected_t const* disconnected = &evt->evt.gap_evt.params.disconnected;
    if (isBondAuthenticationFailure(disconnected->reason)) {
      instance->removeStoredBondForPeer("authentication disconnect");
      if (instance->_bonded_only) {
        instance->requestBondedOnlyRecovery(
            "bond authentication disconnect");
      }
    }
#if defined(COMPANION_RADIO_FULL) && COMPANION_RADIO_FULL
    cccd_written_this_connection = false;
#endif
    instance->_peer_address_valid = false;
  } else if (evt->header.evt_id == BLE_GAP_EVT_CONN_PARAM_UPDATE_REQUEST) {
    uint16_t conn_handle = evt->evt.gap_evt.conn_handle;
    if (instance->isValidConnection(conn_handle)) {
      BLE_DEBUG_PRINTLN("CONN_PARAM_UPDATE_REQUEST: handle=0x%04X, min_interval=%u, max_interval=%u, latency=%u, timeout=%u",
                       conn_handle,
                       evt->evt.gap_evt.params.conn_param_update_request.conn_params.min_conn_interval,
                       evt->evt.gap_evt.params.conn_param_update_request.conn_params.max_conn_interval,
                       evt->evt.gap_evt.params.conn_param_update_request.conn_params.slave_latency,
                       evt->evt.gap_evt.params.conn_param_update_request.conn_params.conn_sup_timeout);
      
      uint32_t err_code = sd_ble_gap_conn_param_update(conn_handle, NULL);
      if (err_code == NRF_SUCCESS) {
        BLE_DEBUG_PRINTLN("Accepted CONN_PARAM_UPDATE_REQUEST (using PPCP)");
      } else {
        BLE_DEBUG_PRINTLN("ERROR: Failed to accept CONN_PARAM_UPDATE_REQUEST: 0x%08X", err_code);
      }
    } else {
      BLE_DEBUG_PRINTLN("CONN_PARAM_UPDATE_REQUEST: ignoring stale callback for handle=0x%04X", conn_handle);
    }
  }
}

bool SerialBLEInterface::removeStoredBondForPeer(const char* cause) {
  if (!_peer_address_valid || _bond_removed_for_connection) {
    return false;
  }

  ble_gap_addr_t peer_address = _peer_address;
  bond_keys_t bond_keys;
  if (!bond_load_keys(BLE_GAP_ROLE_PERIPH, &peer_address, &bond_keys)) {
    BLE_DEBUG_PRINTLN("SerialBLEInterface: no stored peer bond to clear (%s)", cause);
    return false;
  }

  bond_remove_key(BLE_GAP_ROLE_PERIPH, &bond_keys.peer_id.id_addr_info);
  _bond_removed_for_connection = true;
  BLE_DEBUG_PRINTLN("SerialBLEInterface: cleared stale peer bond (%s)", cause);
  return true;
}

void SerialBLEInterface::noteSuccessfulConnection(
    const ble_gap_addr_t& peer_address) {
  if (_stealth_pair_once) {
    _advertisingSuppressed.store(true, std::memory_order_release);
    Bluefruit.Advertising.restartOnDisconnect(false);
  }

  if (!_successfulConnectionPending.load(std::memory_order_acquire)) {
    _successful_peer_address = peer_address;
    _successfulConnectionStarted.store(
        (uint32_t)millis(), std::memory_order_relaxed);
    _successfulConnectionPending.store(true, std::memory_order_release);
  }
}

bool SerialBLEInterface::resolveSuccessfulPeer(
    mesh::companion::BluetoothPeerIdentity& peer) const {
  ble_gap_addr_t lookup = _successful_peer_address;
  bond_keys_t keys;
  if (!bond_load_keys(BLE_GAP_ROLE_PERIPH, &lookup, &keys)) {
    return false;
  }

  const ble_gap_addr_t& identity = keys.peer_id.id_addr_info;
  if (identity.addr_type == BLE_GAP_ADDR_TYPE_PUBLIC) {
    peer.type = mesh::companion::BLUETOOTH_PEER_ADDRESS_PUBLIC;
  } else if (identity.addr_type == BLE_GAP_ADDR_TYPE_RANDOM_STATIC) {
    peer.type = mesh::companion::BLUETOOTH_PEER_ADDRESS_RANDOM;
  } else {
    return false;
  }
  for (size_t i = 0; i < mesh::companion::BLUETOOTH_MAC_BYTES; i++) {
    peer.address[i] = identity.addr[
        mesh::companion::BLUETOOTH_MAC_BYTES - 1 - i];
  }
  return mesh::companion::isValidBluetoothPeerIdentity(peer);
}

bool SerialBLEInterface::configureBondedOnlyAdvertising(
    const mesh::companion::BluetoothPeerIdentity& peer,
    bool require_stored_bond) {
  if (!mesh::companion::isValidBluetoothPeerIdentity(peer)) return false;

  ble_gap_addr_t native_peer = {};
  native_peer.addr_type =
      peer.type == mesh::companion::BLUETOOTH_PEER_ADDRESS_PUBLIC
          ? BLE_GAP_ADDR_TYPE_PUBLIC
          : BLE_GAP_ADDR_TYPE_RANDOM_STATIC;
  for (size_t i = 0; i < mesh::companion::BLUETOOTH_MAC_BYTES; i++) {
    native_peer.addr[i] = peer.address[
        mesh::companion::BLUETOOTH_MAC_BYTES - 1 - i];
  }

  ble_gap_addr_t lookup = native_peer;
  bond_keys_t keys;
  if (!bond_load_keys(BLE_GAP_ROLE_PERIPH, &lookup, &keys)
      || keys.peer_id.id_addr_info.addr_type != native_peer.addr_type
      || memcmp(keys.peer_id.id_addr_info.addr, native_peer.addr,
                sizeof(native_peer.addr)) != 0) {
    if (require_stored_bond) {
      requestBondedOnlyRecovery("saved peer bond is unavailable");
    }
    return false;
  }

  if (isAdvertising() && !Bluefruit.Advertising.stop()) {
    requestBondedOnlyRecovery("general advertising could not stop");
    return false;
  }

  // A bonded phone may reconnect from a resolvable private address. Loading
  // its identity key lets the SoftDevice put a current RPA in TargetA instead
  // of directing advertisements only to the phone's static identity address.
  const ble_gap_id_key_t* peer_identities[] = {&keys.peer_id};
  const uint32_t identity_error = sd_ble_gap_device_identities_set(
      peer_identities, nullptr, 1);
  if (identity_error != NRF_SUCCESS) {
    BLE_DEBUG_PRINTLN(
        "SerialBLEInterface: peer identity-list setup failed: %lu",
        (unsigned long)identity_error);
    requestBondedOnlyRecovery("peer identity-list setup failed");
    return false;
  }

  // Legacy directed advertisements do not carry an advertising or scan
  // response payload. Leaving the normal name/service payload configured can
  // make the SoftDevice reject the directed advertising parameters.
  Bluefruit.Advertising.clearData();
  Bluefruit.ScanResponse.clearData();
  Bluefruit.Advertising.setType(
      BLE_GAP_ADV_TYPE_CONNECTABLE_NONSCANNABLE_DIRECTED);
  Bluefruit.Advertising.setPeerAddress(native_peer);
  Bluefruit.Advertising.restartOnDisconnect(true);
  _bonded_only = true;
  _stealth_pair_once = false;
  _advertisingSuppressed.store(false, std::memory_order_release);

  if (_isEnabled && _conn_handle == BLE_CONN_HANDLE_INVALID
      && !startAdvertising("directed advertising failed to start")) {
    return false;
  }
  return true;
}

void SerialBLEInterface::requestBondedOnlyRecovery(const char* cause) {
  BLE_DEBUG_PRINTLN("SerialBLEInterface: stealth recovery required (%s)",
                    cause);
  _bonded_only = true;
  _stealth_pair_once = false;
  _advertisingSuppressed.store(true, std::memory_order_release);
  _bondedOnlyRecoveryPending.store(true, std::memory_order_release);
  Bluefruit.Advertising.restartOnDisconnect(false);
  if (isAdvertising()) Bluefruit.Advertising.stop();
}

bool SerialBLEInterface::advertisingAllowed() const {
  return _isEnabled
      && !_advertisingSuppressed.load(std::memory_order_acquire)
      && !_bondedOnlyRecoveryPending.load(std::memory_order_acquire);
}

bool SerialBLEInterface::startAdvertising(const char* failure_cause) {
  if (!advertisingAllowed()) return false;
  if (Bluefruit.Advertising.start(0)) return true;
  if (_bonded_only) requestBondedOnlyRecovery(failure_cause);
  return false;
}

bool SerialBLEInterface::begin(const char* prefix, const char* name,
                               uint32_t pin_code,
                               const uint8_t* custom_address,
                               bool clear_bonds, bool stealth_pair_once,
                               const mesh::companion::BluetoothPeerIdentity*
                                   bonded_only_peer) {
  // Bluefruit cannot safely reinitialize a partly started SoftDevice: doing
  // so leaks worker tasks, FIFOs and GATT registrations on every retry. Keep
  // USB/UI usable after a failed start; recovery requires a corrected build
  // or configuration and a reboot.
  if (_begin_attempted) return _begin_ready;
  _begin_attempted = true;
  _begin_failure[0] = 0;
  instance = this;
  _successfulConnectionPending.store(false, std::memory_order_release);
  _successfulConnectionStarted.store(0, std::memory_order_relaxed);
  _bondedOnlyRecoveryPending.store(false, std::memory_order_release);
  _advertisingSuppressed.store(false, std::memory_order_release);
  _stealth_pair_once = stealth_pair_once;
  _bonded_only = false;
  _bonded_only_configure_pending = false;
  _pending_bonded_peer = mesh::companion::BluetoothPeerIdentity();

  char charpin[20];
  snprintf(charpin, sizeof(charpin), "%lu", (unsigned long)pin_code);
  
  // If we want to control BLE LED ourselves, uncomment this:
  // Bluefruit.autoConnLed(false);
  // Configure individual connection values rather than the all-or-nothing
  // Bluefruit preset. Defaults are identical to BANDWIDTH_MAX; constrained
  // board profiles can lower event/buffer reservations without shrinking the
  // Companion's negotiated 247-byte MTU.
  Bluefruit.configPrphConn(COMPANION_BLE_PRPH_MTU,
                           COMPANION_BLE_PRPH_EVENT_LENGTH,
                           COMPANION_BLE_PRPH_HVN_QUEUE,
                           COMPANION_BLE_PRPH_WRCMD_QUEUE);
  // The pinned nRF52 core is built with LTO. Its internal xTaskCreate() calls
  // are resolved before the application's linker wrappers, so a wrapper-based
  // task observation incorrectly reports that the BLE/SOC workers never
  // started. Bluefruit.begin() is the reliable startup result available to
  // this application.
  if (!Bluefruit.begin()) {
    uint8_t softdevice_enabled = 0;
    const uint32_t softdevice_status =
        mesh_nrf52::softdeviceIsEnabled(softdevice_enabled);
    ble_gap_addr_t address = {};
    const uint32_t gap_status = softdevice_enabled
        ? sd_ble_gap_addr_get(&address) : NRF_ERROR_INVALID_STATE;
    snprintf(_begin_failure, sizeof(_begin_failure),
             "Bluefruit begin failed (SD %lu/%u, GAP %lu)",
             (unsigned long)softdevice_status,
             (unsigned)softdevice_enabled,
             (unsigned long)gap_status);
    instance = nullptr;
    mesh::usbLoggingPort().println(
        _begin_failure);
    return false;
  }

  if (clear_bonds) Bluefruit.Periph.clearBonds();

  if (custom_address != nullptr) {
    if (!mesh::companion::isValidBluetoothMac(custom_address)) {
      strncpy(_begin_failure, "custom Bluetooth MAC is invalid",
              sizeof(_begin_failure) - 1);
      _begin_failure[sizeof(_begin_failure) - 1] = 0;
      instance = nullptr;
      BLE_DEBUG_PRINTLN("Custom Bluetooth MAC is invalid");
      return false;
    }

    ble_gap_addr_t address = {};
    address.addr_type = BLE_GAP_ADDR_TYPE_RANDOM_STATIC;
    for (size_t i = 0; i < mesh::companion::BLUETOOTH_MAC_BYTES; i++) {
      address.addr[i] = custom_address[
          mesh::companion::BLUETOOTH_MAC_BYTES - 1 - i];
    }
    const uint32_t address_error = sd_ble_gap_addr_set(&address);
    if (address_error != NRF_SUCCESS) {
      snprintf(_begin_failure, sizeof(_begin_failure),
               "custom Bluetooth MAC rejected (%lu)",
               (unsigned long)address_error);
      instance = nullptr;
      BLE_DEBUG_PRINTLN("Custom Bluetooth MAC failed: %lu", address_error);
      return false;
    }
  }
 
  char resolved_name[32];
  const char* suffix = name;
  if (strcmp(name, "@@MAC") == 0) {
    ble_gap_addr_t addr;
    if (sd_ble_gap_addr_get(&addr) == NRF_SUCCESS) {
      snprintf(resolved_name, sizeof(resolved_name),
               "%02X%02X%02X%02X%02X%02X",
               addr.addr[5], addr.addr[4], addr.addr[3], addr.addr[2],
               addr.addr[1], addr.addr[0]);
      suffix = resolved_name;
    }
  }
  char dev_name[32+16];
  const int dev_name_len = snprintf(dev_name, sizeof(dev_name), "%s%s",
                                    prefix, suffix);
  if (dev_name_len < 0 || dev_name_len >= (int)sizeof(dev_name)) {
    strncpy(_begin_failure, "Bluetooth device name is too long",
            sizeof(_begin_failure) - 1);
    _begin_failure[sizeof(_begin_failure) - 1] = 0;
    instance = nullptr;
    BLE_DEBUG_PRINTLN("Bluetooth device name is too long");
    return false;
  }

  // Connection interval units: 1.25ms, supervision timeout units: 10ms
  ble_gap_conn_params_t ppcp_params;
  ppcp_params.min_conn_interval = BLE_MIN_CONN_INTERVAL;
  ppcp_params.max_conn_interval = BLE_MAX_CONN_INTERVAL;
  ppcp_params.slave_latency = BLE_SLAVE_LATENCY;
  ppcp_params.conn_sup_timeout = BLE_CONN_SUP_TIMEOUT;
  
  uint32_t err_code = sd_ble_gap_ppcp_set(&ppcp_params);
  if (err_code == NRF_SUCCESS) {
    BLE_DEBUG_PRINTLN("PPCP set: %u-%ums interval, latency=%u, %ums timeout",
                     ppcp_params.min_conn_interval * 5 / 4,  // convert to ms (1.25ms units)
                     ppcp_params.max_conn_interval * 5 / 4,
                     ppcp_params.slave_latency,
                     ppcp_params.conn_sup_timeout * 10);  // convert to ms (10ms units)
  } else {
    BLE_DEBUG_PRINTLN("Failed to set PPCP: %lu", err_code);
  }
  
  Bluefruit.setTxPower(BLE_TX_POWER);
  Bluefruit.setName(dev_name);

  Bluefruit.Security.setMITM(true);
  Bluefruit.Security.setPIN(charpin);
  Bluefruit.Security.setIOCaps(true, false, false);
  Bluefruit.Security.setPairPasskeyCallback(onPairingPasskey);
  Bluefruit.Security.setPairCompleteCallback(onPairingComplete);

  Bluefruit.Periph.setConnectCallback(onConnect);
  Bluefruit.Periph.setDisconnectCallback(onDisconnect);
  Bluefruit.Security.setSecuredCallback(onSecured);

  Bluefruit.setEventCallback(onBLEEvent);

  bleuart.setPermission(SECMODE_ENC_WITH_MITM, SECMODE_ENC_WITH_MITM);
  if (bleuart.begin() != ERROR_NONE) {
    strncpy(_begin_failure, "Bluetooth UART service registration failed",
            sizeof(_begin_failure) - 1);
    _begin_failure[sizeof(_begin_failure) - 1] = 0;
    instance = nullptr;
    BLE_DEBUG_PRINTLN("Bluetooth UART service begin failed");
    return false;
  }
  bleuart.setRxCallback(onBleUartRX);

  // Register the legacy DFU service before the optional mOTA service. A
  // fielded SoftDevice has a fixed GATT table: reserve the ordinary Companion
  // and firmware-recovery transports first, then use whatever remains for
  // the larger mOTA extension. Do not attempt a new service after an mOTA
  // registration failure because Bluefruit cannot remove attributes that did
  // fit from its active table.
#if COMPANION_FEATURE_BLE_DFU
  bledfu.setPermission(SECMODE_ENC_WITH_MITM, SECMODE_ENC_WITH_MITM);
  if (bledfu.begin() != ERROR_NONE) {
    mesh::usbLoggingPort().println(
        "Bluetooth DFU service unavailable; normal Companion Bluetooth continues");
  }
#endif

#if COMPANION_FEATURE_BLE_MOTA_SOURCE
  _mota_stream.setSender(sendMotaRequest, this);
  _mota_stream.setActive(false);
  _mota_available = false;

  _mota_service.setPermission(SECMODE_ENC_WITH_MITM,
                              SECMODE_ENC_WITH_MITM);
  if (_mota_service.begin() == ERROR_NONE) {
    _mota_request.setProperties(CHR_PROPS_NOTIFY);
    _mota_request.setPermission(SECMODE_ENC_WITH_MITM,
                                SECMODE_NO_ACCESS);
    _mota_request.setMaxLen(mesh::ota::BLE_MOTA_REQUEST_MAX);
    _mota_request.setUserDescriptor("mOTA device request");
    if (_mota_request.begin() == ERROR_NONE) {
      _mota_response.setProperties(CHR_PROPS_WRITE);
      _mota_response.setPermission(SECMODE_NO_ACCESS,
                                   SECMODE_ENC_WITH_MITM);
      _mota_response.setMaxLen(mesh::ota::BLE_MOTA_RESPONSE_MAX);
      _mota_response.setUserDescriptor("mOTA host response");
      _mota_response.setWriteCallback(onMotaResponse);
      _mota_available = _mota_response.begin() == ERROR_NONE;
    }
  }
  if (!_mota_available) {
    // GATT attributes cannot be removed after Bluefruit starts, so do not
    // retry here.  Continuing with the already-registered UART service keeps
    // the phone and USB recovery paths available; a corrected image can add
    // the optional mOTA service on the next reboot.
    mesh::usbLoggingPort().println(
        "Bluetooth mOTA unavailable; normal Companion Bluetooth continues");
  }
#endif

  Bluefruit.Advertising.setType(
      BLE_GAP_ADV_TYPE_CONNECTABLE_SCANNABLE_UNDIRECTED);
  Bluefruit.Advertising.addFlags(BLE_GAP_ADV_FLAGS_LE_ONLY_GENERAL_DISC_MODE);
  Bluefruit.Advertising.addTxPower();
  Bluefruit.Advertising.addService(bleuart);

  Bluefruit.ScanResponse.addName();

  Bluefruit.Advertising.setInterval(BLE_ADV_INTERVAL_MIN, BLE_ADV_INTERVAL_MAX);
  Bluefruit.Advertising.setFastTimeout(BLE_ADV_FAST_TIMEOUT);

  Bluefruit.Advertising.restartOnDisconnect(true);

  if (bonded_only_peer != nullptr) {
    configureBondedOnlyAdvertising(*bonded_only_peer, true);
  }

  _begin_ready = true;
  return true;
}

bool SerialBLEInterface::takeSuccessfulConnection(
    mesh::companion::BluetoothPeerIdentity* peer) {
  if (!_successfulConnectionPending.load(std::memory_order_acquire)) {
    return false;
  }
  if (peer != nullptr && !resolveSuccessfulPeer(*peer)) {
    // Bond persistence is deferred by Bluefruit. Leave the event pending and
    // let the main loop retry after that callback has written the keys. If the
    // deferred write failed, reopen pairing instead of remaining hidden until
    // somebody manually power-cycles the node.
    const uint32_t started = _successfulConnectionStarted.load(
        std::memory_order_relaxed);
    if (_stealth_pair_once
        && (uint32_t)((uint32_t)millis() - started)
            >= BLE_BOND_PERSIST_TIMEOUT_MS) {
      _successfulConnectionPending.store(false, std::memory_order_release);
      requestBondedOnlyRecovery("new peer bond did not persist");
    }
    return false;
  }
  const bool pending = _successfulConnectionPending.exchange(
      false, std::memory_order_acq_rel);
  if (pending) {
    _successfulConnectionStarted.store(0, std::memory_order_relaxed);
  }
  return pending;
}

bool SerialBLEInterface::enableBondedOnlyAdvertising(
    const mesh::companion::BluetoothPeerIdentity& peer) {
  if (!mesh::companion::isValidBluetoothPeerIdentity(peer)) return false;

  // The SoftDevice may reject identity-list changes while a connection is
  // using that list. Preserve the newly paired session and finish switching
  // to directed advertising from the main loop after it disconnects.
  if (_conn_handle != BLE_CONN_HANDLE_INVALID) {
    _pending_bonded_peer = peer;
    _bonded_only_configure_pending = true;
    _bonded_only = true;
    _stealth_pair_once = false;
    _advertisingSuppressed.store(true, std::memory_order_release);
    Bluefruit.Advertising.restartOnDisconnect(false);
    return true;
  }
  return configureBondedOnlyAdvertising(peer, false);
}

void SerialBLEInterface::serviceBondedOnlyTransition() {
  if (!_bonded_only_configure_pending
      || _conn_handle != BLE_CONN_HANDLE_INVALID) {
    return;
  }

  const mesh::companion::BluetoothPeerIdentity peer = _pending_bonded_peer;
  _bonded_only_configure_pending = false;
  configureBondedOnlyAdvertising(peer, true);
}

void SerialBLEInterface::cancelStealthPairingTransition() {
  if (!_stealth_pair_once) return;
  _stealth_pair_once = false;
  _advertisingSuppressed.store(false, std::memory_order_release);
  Bluefruit.Advertising.setType(
      BLE_GAP_ADV_TYPE_CONNECTABLE_SCANNABLE_UNDIRECTED);
  Bluefruit.Advertising.restartOnDisconnect(true);
  if (_isEnabled && _conn_handle == BLE_CONN_HANDLE_INVALID
      && !isAdvertising()) {
    startAdvertising("advertising failed after stealth cancellation");
  }
}

void SerialBLEInterface::clearBuffers() {
  send_queue_len = 0;
  recv_queue_len = 0;
  _last_retry_attempt = 0;
  _tx_stall_watchdog.reset();
  _tx_disconnect_recovery.complete();
  bleuart.flush();
}

void SerialBLEInterface::shiftSendQueueLeft() {
  if (send_queue_len > 0) {
    send_queue_len--;
    for (uint8_t i = 0; i < send_queue_len; i++) {
      send_queue[i] = send_queue[i + 1];
    }
  }
}

void SerialBLEInterface::shiftRecvQueueLeft() {
  if (recv_queue_len > 0) {
    recv_queue_len--;
    for (uint8_t i = 0; i < recv_queue_len; i++) {
      recv_queue[i] = recv_queue[i + 1];
    }
  }
}

size_t SerialBLEInterface::writeBleUartFrame(const Frame& frame) {
  BLEConnection* conn = Bluefruit.Connection(_conn_handle);
  if (conn == nullptr || !conn->connected() ||
      !bleuart.notifyEnabled(_conn_handle)) {
    return 0;
  }

  const uint16_t mtu = conn->getMtu();
  if (mtu <= 3) return 0;

  // BLEUart::write() reports either the full requested length or zero, even
  // when its internal multi-notification loop queued an earlier fragment
  // before a later fragment failed. Submit one ATT payload at a time so a
  // non-zero return accurately tells us that part of the protocol frame is
  // already on the stream and must never be followed by a whole-frame retry.
  return mesh::writeBleFrameInChunks(
      frame.buf, frame.len, mtu - 3,
      [this](const uint8_t* data, size_t len) {
        return bleuart.write(_conn_handle, data, len);
      });
}

void SerialBLEInterface::serviceTxRecovery(uint32_t now) {
  if (!_tx_disconnect_recovery.pending()) return;

  BLEConnection* conn = _conn_handle == BLE_CONN_HANDLE_INVALID
                            ? nullptr
                            : Bluefruit.Connection(_conn_handle);
  if (_conn_handle == BLE_CONN_HANDLE_INVALID || conn == nullptr ||
      !conn->connected()) {
    BLE_DEBUG_PRINTLN("SerialBLEInterface: stalled TX link is already closed");
    _conn_handle = BLE_CONN_HANDLE_INVALID;
    _isDeviceConnected = false;
    _peer_address_valid = false;
    _security_timer.cancel();
    clearBuffers();
    if (advertisingAllowed() && !isAdvertising()) {
      startAdvertising("advertising failed after TX recovery");
    }
    return;
  }

  if (!_tx_disconnect_recovery.shouldAttempt(now)) return;

  const uint32_t result = sd_ble_gap_disconnect(
      _conn_handle, BLE_HCI_REMOTE_USER_TERMINATED_CONNECTION);
  if (result == NRF_SUCCESS) {
    BLE_DEBUG_PRINTLN("SerialBLEInterface: stalled TX disconnect requested");
  } else if (result == NRF_ERROR_INVALID_STATE) {
    BLE_DEBUG_PRINTLN("SerialBLEInterface: stalled TX disconnect already in progress");
  } else {
    BLE_DEBUG_PRINTLN(
        "SerialBLEInterface: stalled TX disconnect failed, err=0x%08lX; will retry",
        (unsigned long)result);
  }
}

void SerialBLEInterface::recoverStalledTx(const char* cause) {
  if (_tx_disconnect_recovery.pending()) return;

  BLE_DEBUG_PRINTLN("SerialBLEInterface: %s; forcing reconnect", cause);

  // Keep the physical connection state intact until the SoftDevice confirms
  // disconnection, but make isConnected() false through the recovery state so
  // no more companion frames enter this damaged stream.
  send_queue_len = 0;
  recv_queue_len = 0;
  _last_retry_attempt = 0;
  _tx_stall_watchdog.reset();
  bleuart.flush();
#if COMPANION_FEATURE_BLE_MOTA_SOURCE
  setMotaStreamActive(false);
#endif
  _tx_disconnect_recovery.begin();
  serviceTxRecovery((uint32_t)millis());
}

bool SerialBLEInterface::isValidConnection(uint16_t handle, bool requireWaitingForSecurity) const {
  if (_conn_handle != handle) {
    return false;
  }
  BLEConnection* conn = Bluefruit.Connection(handle);
  if (conn == nullptr || !conn->connected()) {
    return false;
  }
  if (requireWaitingForSecurity && _isDeviceConnected) {
    return false;
  }
  return true;
}

bool SerialBLEInterface::isAdvertising() const {
  return Bluefruit.Advertising.isRunning();
}

void SerialBLEInterface::enable() {
  if (_isEnabled) return;

  _pairingRequestPending.store(false, std::memory_order_release);
  _isEnabled = true;
  clearBuffers();
  _last_health_check = millis();

  if (advertisingAllowed()) {
    Bluefruit.Advertising.restartOnDisconnect(true);
    startAdvertising("advertising failed while enabling Bluetooth");
  }
}

void SerialBLEInterface::disconnect() {
  if (_conn_handle != BLE_CONN_HANDLE_INVALID) {
    sd_ble_gap_disconnect(_conn_handle, BLE_HCI_REMOTE_USER_TERMINATED_CONNECTION);
  }
}

void SerialBLEInterface::disable() {
  _isEnabled = false;
  _pairingRequestPending.store(false, std::memory_order_release);
  BLE_DEBUG_PRINTLN("SerialBLEInterface: disable");

  Bluefruit.Advertising.restartOnDisconnect(false);
  Bluefruit.Advertising.stop();
  disconnect();
  _security_timer.cancel();
  _last_health_check = 0;
#if COMPANION_FEATURE_BLE_MOTA_SOURCE
  setMotaStreamActive(false);
#endif
}

size_t SerialBLEInterface::writeFrame(const uint8_t src[], size_t len) {
  if (len > MAX_FRAME_SIZE) {
    BLE_DEBUG_PRINTLN("writeFrame(), frame too big, len=%u", (unsigned)len);
    return 0;
  }

  bool connected = isConnected();
  if (connected && len > 0) {
    if (!mesh::enqueueCompanionFrame(send_queue, send_queue_len, FRAME_QUEUE_SIZE,
                                     src, len)) {
      BLE_DEBUG_PRINTLN("writeFrame(), send_queue is full!");
      return 0;
    }
    return len;
  }
  return 0;
}

size_t SerialBLEInterface::checkRecvFrame(uint8_t dest[]) {
  const uint32_t check_now = (uint32_t)millis();
  serviceBondedOnlyTransition();
  if (_tx_disconnect_recovery.pending()) {
    serviceTxRecovery(check_now);
    return 0;
  }

  if (send_queue_len > 0) {
    if (!isConnected()) {
      BLE_DEBUG_PRINTLN("writeBytes: connection invalid, clearing send queue");
      send_queue_len = 0;
      _last_retry_attempt = 0;
      _tx_stall_watchdog.reset();
    } else {
      uint32_t now = check_now;
      bool throttle_active = (_last_retry_attempt > 0 && (now - _last_retry_attempt) < BLE_RETRY_THROTTLE_MS);

      if (!throttle_active) {
        Frame frame_to_send = send_queue[0];
        const bool delivery_required = mesh::companionFrameRequiresDelivery(
            frame_to_send.buf, frame_to_send.len);

        size_t written = writeBleUartFrame(frame_to_send);
        if (written == frame_to_send.len) {
          BLE_DEBUG_PRINTLN("writeBytes: sz=%u, hdr=%u", (unsigned)frame_to_send.len, (unsigned)frame_to_send.buf[0]);
          _last_retry_attempt = 0;
          _tx_stall_watchdog.reset();
          shiftSendQueueLeft();
        } else if (written > 0) {
          BLE_DEBUG_PRINTLN("writeBytes: partial write, sent=%u of %u",
                            (unsigned)written,
                            (unsigned)frame_to_send.len);
          // The app cannot recover framing after receiving only part of one
          // protocol frame. Reconnect instead of following it with another
          // frame on the same BLE UART stream.
          recoverStalledTx("partial BLE UART frame");
          return 0;
        } else {
          if (!isConnected()) {
            BLE_DEBUG_PRINTLN("writeBytes failed: connection lost, dropping frame");
            _last_retry_attempt = 0;
            _tx_stall_watchdog.reset();
            shiftSendQueueLeft();
          } else {
            BLE_DEBUG_PRINTLN("writeBytes failed (buffer full), keeping frame for retry");
            _last_retry_attempt = now;
            if (delivery_required) {
              if (_tx_stall_watchdog.noteBlocked((uint32_t)now)) {
                recoverStalledTx("command reply blocked for 10 seconds");
                return 0;
              }
            } else {
              // Best-effort pushes do not make an otherwise healthy but idle
              // app reconnect. A later response is inserted ahead of them and
              // starts its own bounded watchdog window.
              _tx_stall_watchdog.reset();
            }
          }
        }
      }
    }
  } else {
    _tx_stall_watchdog.reset();
  }
  
  if (recv_queue_len > 0) {
    size_t len = recv_queue[0].len;
    memcpy(dest, recv_queue[0].buf, len);
    
    BLE_DEBUG_PRINTLN("readBytes: sz=%u, hdr=%u", (unsigned)len, (unsigned)dest[0]);
    
    shiftRecvQueueLeft();
    return len;
  }
  
  // Advertising watchdog: periodically check if advertising is running, restart if not
  // Only run when truly disconnected (no connection handle), not during connection establishment
  unsigned long now = millis();
  if (_isEnabled && _conn_handle != BLE_CONN_HANDLE_INVALID
      && _security_timer.expired(now)) {
    // A client may open a link and never finish PIN/bond negotiation.  That
    // otherwise suppresses advertising forever because a connection handle
    // remains live.  Disconnect only: inactivity is not evidence of a stale
    // bond, so do not erase anything here.
    BLE_DEBUG_PRINTLN("SerialBLEInterface: security setup timed out after %lu ms",
                      (unsigned long)BLE_SECURITY_SESSION_TIMEOUT_MS);
    _security_timer.cancel();
    disconnect();
  }
  if (advertisingAllowed() && !isConnected()
      && _conn_handle == BLE_CONN_HANDLE_INVALID) {
    if (now - _last_health_check >= BLE_HEALTH_CHECK_INTERVAL) {
      _last_health_check = now;
      
      if (!isAdvertising()) {
        BLE_DEBUG_PRINTLN("SerialBLEInterface: advertising watchdog - advertising stopped, restarting");
        startAdvertising("advertising watchdog restart failed");
      }
    }
  }
  
  return 0;
}

void SerialBLEInterface::onBleUartRX(uint16_t conn_handle) {
  if (!instance) {
    return;
  }
  
  if (instance->_conn_handle != conn_handle || !instance->isConnected()) {
    while (instance->bleuart.available() > 0) {
      instance->bleuart.read();
    }
    return;
  }
  
  while (instance->bleuart.available() > 0) {
    if (instance->recv_queue_len >= FRAME_QUEUE_SIZE) {
      while (instance->bleuart.available() > 0) {
        instance->bleuart.read();
      }
      BLE_DEBUG_PRINTLN("onBleUartRX: recv queue full, dropping data");
      break;
    }
    
    int avail = instance->bleuart.available();
    
    if (avail > MAX_FRAME_SIZE) {
      BLE_DEBUG_PRINTLN("onBleUartRX: WARN: BLE RX overflow, avail=%d, draining all", avail);
      uint8_t drain_buf[BLE_RX_DRAIN_BUF_SIZE];
      while (instance->bleuart.available() > 0) {
        int chunk = instance->bleuart.available() > BLE_RX_DRAIN_BUF_SIZE ? BLE_RX_DRAIN_BUF_SIZE : instance->bleuart.available();
        instance->bleuart.readBytes(drain_buf, chunk);
      }
      continue;
    }
    
    int read_len = avail;
    instance->recv_queue[instance->recv_queue_len].len = read_len;
    instance->bleuart.readBytes(instance->recv_queue[instance->recv_queue_len].buf, read_len);
    instance->recv_queue_len++;
  }
}

bool SerialBLEInterface::isConnected() const {
  return !_tx_disconnect_recovery.pending() && _isDeviceConnected &&
         Bluefruit.connected() > 0;
}

#if COMPANION_FEATURE_BLE_MOTA_SOURCE
bool SerialBLEInterface::isMotaChannelReady() {
  return _mota_available && isConnected()
      && _conn_handle != BLE_CONN_HANDLE_INVALID
      && _mota_request.notifyEnabled(_conn_handle);
}
#endif

bool SerialBLEInterface::isReadBusy() const {
  return (recv_queue_len > 0);
}

bool SerialBLEInterface::isWriteBusy() const {
  return send_queue_len >= (FRAME_QUEUE_SIZE * 2 / 3);
}

bool SerialBLEInterface::hasPendingIO() const {
  return recv_queue_len > 0 || send_queue_len > 0;
}
