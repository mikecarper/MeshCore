#include "SerialBLEInterface.h"
#include "../BluetoothMac.h"
#include "../CompanionFrameQueue.h"
#include "esp_mac.h"
#include <stdlib.h>
#if defined(CONFIG_BLUEDROID_ENABLED) && !defined(MESH_USE_NIMBLE_ARDUINO)
#include "esp_gap_ble_api.h"
#endif
#if MESH_BLE_USES_NIMBLE && !defined(MESH_USE_NIMBLE_ARDUINO)
#include <host/ble_store.h>
#endif

// Arduino 3.x relies on a library constructor to mark BLE as used before
// initArduino(), and that constructor ordering is not reliable with all link
// layouts/LTO combinations. A strong override keeps the controller memory
// available until SerialBLEInterface::begin(); Full Companion then reserves
// BLE controller/host memory before starting WiFi.
extern "C" bool bleInUse(void) {
  return true;
}

// See the following for generating UUIDs:
// https://www.uuidgenerator.net/

#define SERVICE_UUID           "6E400001-B5A3-F393-E0A9-E50E24DCCA9E" // UART service UUID
#define CHARACTERISTIC_UUID_RX "6E400002-B5A3-F393-E0A9-E50E24DCCA9E"
#define CHARACTERISTIC_UUID_TX "6E400003-B5A3-F393-E0A9-E50E24DCCA9E"

#define ADVERT_RESTART_DELAY  1000   // millis
#define BLE_BOND_PERSIST_TIMEOUT_MS 15000

static void cleanupFailedBluetoothStart() {
#if defined(MESH_USE_NIMBLE_ARDUINO)
  BLEDevice::deinit(true);
#else
  BLEDevice::deinit(false);
#endif
}

#if !defined(MESH_USE_NIMBLE_ARDUINO)
static void clearStoredBluetoothBonds() {
#if MESH_BLE_USES_NIMBLE
  const int result = ble_store_clear();
  if (result != 0) {
    BLE_DEBUG_PRINTLN("Could not clear NimBLE bonds: %d", result);
  }
#else
  int count = esp_ble_get_bond_device_num();
  if (count <= 0) return;

  esp_ble_bond_dev_t* devices = static_cast<esp_ble_bond_dev_t*>(
      malloc(sizeof(esp_ble_bond_dev_t) * count));
  if (devices == NULL) {
    BLE_DEBUG_PRINTLN("Could not allocate Bluetooth bond list");
    return;
  }
  if (esp_ble_get_bond_device_list(&count, devices) != ESP_OK) {
    BLE_DEBUG_PRINTLN("Could not read Bluetooth bond list");
    free(devices);
    return;
  }
  for (int i = 0; i < count; i++) {
    if (esp_ble_remove_bond_device(devices[i].bd_addr) != ESP_OK) {
      BLE_DEBUG_PRINTLN("Could not remove Bluetooth bond %d", i);
    }
  }
  free(devices);
#endif
}

#endif

static bool bluetoothPeersEqual(
    const mesh::companion::BluetoothPeerIdentity& lhs,
    const mesh::companion::BluetoothPeerIdentity& rhs) {
  return lhs.type == rhs.type
      && memcmp(lhs.address, rhs.address, sizeof(lhs.address)) == 0;
}

void SerialBLEInterface::noteSuccessfulConnection(
    const mesh::companion::BluetoothPeerIdentity& peer) {
  if (_stealth_pair_once) {
    _advertisingSuppressed.store(true, std::memory_order_release);
    _adv_restart_pending = false;
  }

  if (!_successfulConnectionPending.load(std::memory_order_acquire)) {
    _successfulPeer = peer;
    _successfulConnectionStarted.store(
        (uint32_t)millis(), std::memory_order_relaxed);
    _successfulConnectionPending.store(true, std::memory_order_release);
  }
}

#if MESH_BLE_USES_NIMBLE
static void bluetoothPeerFromNimbleAddress(
    const ble_addr_t& address,
    mesh::companion::BluetoothPeerIdentity& peer) {
  peer.type = (address.type & 0x01u) == BLE_ADDR_PUBLIC
      ? mesh::companion::BLUETOOTH_PEER_ADDRESS_PUBLIC
      : mesh::companion::BLUETOOTH_PEER_ADDRESS_RANDOM;
  for (size_t i = 0; i < mesh::companion::BLUETOOTH_MAC_BYTES; i++) {
    peer.address[i] = address.val[
        mesh::companion::BLUETOOTH_MAC_BYTES - 1 - i];
  }
}

static ble_addr_t nimbleAddressFromBluetoothPeer(
    const mesh::companion::BluetoothPeerIdentity& peer) {
  ble_addr_t address = {};
  address.type =
      peer.type == mesh::companion::BLUETOOTH_PEER_ADDRESS_PUBLIC
          ? BLE_ADDR_PUBLIC
          : BLE_ADDR_RANDOM;
  for (size_t i = 0; i < mesh::companion::BLUETOOTH_MAC_BYTES; i++) {
    address.val[i] = peer.address[
        mesh::companion::BLUETOOTH_MAC_BYTES - 1 - i];
  }
  return address;
}

static bool nimbleBondExistsForPeer(
    const mesh::companion::BluetoothPeerIdentity& peer) {
#if defined(MESH_USE_NIMBLE_ARDUINO)
  return BLEDevice::isBonded(BLEAddress(nimbleAddressFromBluetoothPeer(peer)));
#else
  ble_addr_t bonded_peers[8];
  int count = 0;
  if (ble_store_util_bonded_peers(
          bonded_peers, &count,
          sizeof(bonded_peers) / sizeof(bonded_peers[0])) != 0) {
    return false;
  }
  for (int i = 0; i < count; i++) {
    mesh::companion::BluetoothPeerIdentity candidate;
    bluetoothPeerFromNimbleAddress(bonded_peers[i], candidate);
    if (bluetoothPeersEqual(candidate, peer)) return true;
  }
  return false;
#endif
}
#else
static void bluetoothPeerFromBluedroidBond(
    const esp_ble_bond_dev_t& bond, uint8_t fallback_type,
    mesh::companion::BluetoothPeerIdentity& peer) {
  const bool has_identity =
      (bond.bond_key.key_mask & ESP_LE_KEY_PID) != 0;
  const uint8_t* address = has_identity
      ? bond.bond_key.pid_key.static_addr
      : bond.bd_addr;
  const uint8_t address_type = has_identity
      ? bond.bond_key.pid_key.addr_type
      : fallback_type;
  peer.type = address_type == BLE_ADDR_TYPE_PUBLIC
      ? mesh::companion::BLUETOOTH_PEER_ADDRESS_PUBLIC
      : mesh::companion::BLUETOOTH_PEER_ADDRESS_RANDOM;
  memcpy(peer.address, address, sizeof(peer.address));
}

static bool findBluedroidBondedPeer(
    const mesh::companion::BluetoothPeerIdentity& requested,
    mesh::companion::BluetoothPeerIdentity& resolved) {
  int count = esp_ble_get_bond_device_num();
  if (count <= 0) return false;

  esp_ble_bond_dev_t* devices = static_cast<esp_ble_bond_dev_t*>(
      malloc(sizeof(esp_ble_bond_dev_t) * count));
  if (devices == NULL) return false;
  if (esp_ble_get_bond_device_list(&count, devices) != ESP_OK) {
    free(devices);
    return false;
  }

  bool found = false;
  for (int i = 0; i < count; i++) {
    mesh::companion::BluetoothPeerIdentity candidate;
    bluetoothPeerFromBluedroidBond(
        devices[i],
        requested.type == mesh::companion::BLUETOOTH_PEER_ADDRESS_PUBLIC
            ? BLE_ADDR_TYPE_PUBLIC
            : BLE_ADDR_TYPE_RANDOM,
        candidate);
    if (bluetoothPeersEqual(candidate, requested)
        || memcmp(devices[i].bd_addr, requested.address,
                  sizeof(requested.address)) == 0
        || count == 1) {
      resolved = candidate;
      found = mesh::companion::isValidBluetoothPeerIdentity(resolved);
      break;
    }
  }
  free(devices);
  return found;
}
#endif

bool SerialBLEInterface::resolveSuccessfulPeer(
    mesh::companion::BluetoothPeerIdentity& peer) const {
#if MESH_BLE_USES_NIMBLE
  if (!mesh::companion::isValidBluetoothPeerIdentity(_successfulPeer)
      || !nimbleBondExistsForPeer(_successfulPeer)) {
    return false;
  }
  peer = _successfulPeer;
  return true;
#else
  if (!mesh::companion::isValidBluetoothPeerIdentity(_successfulPeer)) {
    return false;
  }
  return findBluedroidBondedPeer(_successfulPeer, peer);
#endif
}

bool SerialBLEInterface::configureBondedOnlyAdvertising(
    const mesh::companion::BluetoothPeerIdentity& peer,
    bool require_stored_bond) {
  if (!mesh::companion::isValidBluetoothPeerIdentity(peer)
      || pServer == NULL) {
    return false;
  }

#if MESH_BLE_USES_NIMBLE
  if (require_stored_bond && !nimbleBondExistsForPeer(peer)) {
    requestBondedOnlyRecovery("saved peer bond is unavailable");
    return false;
  }
  const ble_addr_t native_peer = nimbleAddressFromBluetoothPeer(peer);
  if (ble_gap_wl_set(&native_peer, 1) != 0) {
    requestBondedOnlyRecovery("could not set NimBLE allowlist");
    return false;
  }
#else
  if (require_stored_bond) {
    mesh::companion::BluetoothPeerIdentity resolved;
    if (!findBluedroidBondedPeer(peer, resolved)
        || !bluetoothPeersEqual(peer, resolved)) {
      requestBondedOnlyRecovery("saved peer bond is unavailable");
      return false;
    }
  }
  esp_bd_addr_t native_peer;
  memcpy(native_peer, peer.address, sizeof(native_peer));
  const esp_ble_wl_addr_type_t address_type =
      peer.type == mesh::companion::BLUETOOTH_PEER_ADDRESS_PUBLIC
          ? BLE_WL_ADDR_TYPE_PUBLIC
          : BLE_WL_ADDR_TYPE_RANDOM;
  // The Bluedroid API adds one entry at a time. Replace any controller state
  // left by an older mode/session so the requested peer is genuinely the only
  // address allowed to scan or connect.
  if (esp_ble_gap_clear_whitelist() != ESP_OK) {
    requestBondedOnlyRecovery("could not clear Bluedroid allowlist");
    return false;
  }
  if (esp_ble_gap_update_whitelist(true, native_peer, address_type)
      != ESP_OK) {
    requestBondedOnlyRecovery("could not set Bluedroid allowlist");
    return false;
  }
#endif

  BLEAdvertising* advertising = pServer->getAdvertising();
  if (advertising == NULL) {
    requestBondedOnlyRecovery("Bluetooth advertising is unavailable");
    return false;
  }
  advertising->stop();
  BLEAdvertisementData minimal_advertisement;
#if defined(MESH_USE_NIMBLE_ARDUINO)
  minimal_advertisement.setFlags(BLE_HS_ADV_F_BREDR_UNSUP);
  advertising->enableScanResponse(false);
#else
  minimal_advertisement.setFlags(ESP_BLE_ADV_FLAG_BREDR_NOT_SPT);
  advertising->setScanResponse(false);
#endif
  advertising->setScanFilter(true, true);
  advertising->setAdvertisementData(minimal_advertisement);

  _bonded_only = true;
  _stealth_pair_once = false;
  _advertisingSuppressed.store(false, std::memory_order_release);
  _adv_restart_pending = false;
  if (_isEnabled && pServer->getConnectedCount() == 0) {
    advertising->start();
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
  _adv_restart_pending = false;
  if (pServer != NULL && pServer->getAdvertising() != NULL) {
    pServer->getAdvertising()->stop();
  }
}

bool SerialBLEInterface::advertisingAllowed() const {
  return _isEnabled
      && !_advertisingSuppressed.load(std::memory_order_acquire)
      && !_bondedOnlyRecoveryPending.load(std::memory_order_acquire);
}

bool SerialBLEInterface::begin(const char* prefix, const char* name,
                               uint32_t pin_code,
                               const uint8_t* custom_address,
                               bool clear_bonds, bool stealth_pair_once,
                               const mesh::companion::BluetoothPeerIdentity*
                                   bonded_only_peer) {
  _pin_code = pin_code;
  _successfulConnectionPending.store(false, std::memory_order_release);
  _successfulConnectionStarted.store(0, std::memory_order_relaxed);
  _bondedOnlyRecoveryPending.store(false, std::memory_order_release);
  _advertisingSuppressed.store(false, std::memory_order_release);
  _successfulPeer = mesh::companion::BluetoothPeerIdentity();
  _stealth_pair_once = stealth_pair_once;
  _bonded_only = false;

  if (custom_address != nullptr
      && !mesh::companion::isValidBluetoothMac(custom_address)) {
    BLE_DEBUG_PRINTLN("Custom Bluetooth MAC is invalid");
    return false;
  }

  char resolved_name[32];
  const char* suffix = name;
  if (strcmp(name, "@@MAC") == 0) {
    uint8_t addr[8];
    memset(addr, 0, sizeof(addr));
    esp_efuse_mac_get_default(addr);
    snprintf(resolved_name, sizeof(resolved_name),
             "%02X%02X%02X%02X%02X%02X",
             addr[5], addr[4], addr[3], addr[2], addr[1], addr[0]);
    suffix = resolved_name;
  }
  char dev_name[32+16];
  const int dev_name_len = snprintf(dev_name, sizeof(dev_name), "%s%s",
                                    prefix, suffix);
  if (dev_name_len < 0 || dev_name_len >= (int)sizeof(dev_name)) {
    BLE_DEBUG_PRINTLN("Bluetooth device name is too long");
    return false;
  }

  // Create the BLE Device
#if MESH_BLE_USES_NIMBLE
  if (!BLEDevice::init(dev_name)) {
    BLE_DEBUG_PRINTLN("BLEDevice::init failed");
    return false;
  }
#else
  // Arduino-ESP32 2.x Bluedroid exposes a void init(). Its first observable
  // failure is a null server below, which is handled without dereferencing it.
  BLEDevice::init(dev_name);
#endif

#if defined(MESH_USE_NIMBLE_ARDUINO)
  if (clear_bonds && !BLEDevice::deleteAllBonds()) {
    BLE_DEBUG_PRINTLN("Could not clear NimBLE bonds for the new identity");
    cleanupFailedBluetoothStart();
    return false;
  }
#else
  if (clear_bonds) clearStoredBluetoothBonds();
#endif

#if MESH_BLE_USES_NIMBLE
  if (custom_address != nullptr) {
    uint8_t native_address[mesh::companion::BLUETOOTH_MAC_BYTES];
    for (size_t i = 0; i < mesh::companion::BLUETOOTH_MAC_BYTES; i++) {
      native_address[i] = custom_address[
          mesh::companion::BLUETOOTH_MAC_BYTES - 1 - i];
    }
    if (!BLEDevice::setOwnAddr(native_address)
        || !BLEDevice::setOwnAddrType(BLE_OWN_ADDR_RANDOM)) {
      BLE_DEBUG_PRINTLN("Custom Bluetooth MAC setup failed");
      cleanupFailedBluetoothStart();
      return false;
    }
  }
#if defined(MESH_USE_NIMBLE_ARDUINO)
  else if (!BLEDevice::setOwnAddrType(BLE_OWN_ADDR_PUBLIC)) {
    cleanupFailedBluetoothStart();
    return false;
  }
#endif
#endif

#if !defined(MESH_USE_NIMBLE_ARDUINO)
  BLEDevice::setSecurityCallbacks(this);
#endif
  // ATT notifications consume three bytes of the negotiated MTU. Reserve
  // that overhead so a MAX_FRAME_SIZE protocol frame fits without truncation.
  BLEDevice::setMTU(MAX_FRAME_SIZE + 3);

#if defined(MESH_USE_NIMBLE_ARDUINO)
  // NimBLE-Arduino 2.5.1 bypasses onPassKeyDisplay() for a non-default
  // static passkey. Keep its callback sentinel here: our callback returns
  // the actual random/saved _pin_code and also wakes the pairing display.
  // This does not change the PIN used on air or weaken authentication.
  BLEDevice::setSecurityPasskey(123456);
  BLEDevice::setSecurityIOCap(BLE_HS_IO_DISPLAY_ONLY);
  BLEDevice::setSecurityAuth(true, true, true);
#else
  BLESecurity  sec;
#if MESH_BLE_USES_NIMBLE
  sec.setPassKey(true, pin_code);
  // A passkey alone does not provide MITM protection when the controller's
  // default capability is NoInputNoOutput: the peers can silently fall back
  // to Just Works and create an unauthenticated bond. The Companion displays
  // (or otherwise publishes) this PIN for entry on the phone/host, so declare
  // the peripheral as DisplayOnly and force the passkey-entry association.
  sec.setCapability(ESP_IO_CAP_OUT);
  sec.setAuthenticationMode(true, true, true);
#else
  sec.setStaticPIN(pin_code);
  sec.setCapability(ESP_IO_CAP_OUT);
  sec.setAuthenticationMode(ESP_LE_AUTH_REQ_SC_MITM_BOND);
#endif
#endif

  //BLEDevice::setPower(ESP_PWR_LVL_N8);

  // Create the BLE Server
  pServer = BLEDevice::createServer();
  if (pServer == NULL) {
    BLE_DEBUG_PRINTLN("BLEDevice::createServer failed");
    cleanupFailedBluetoothStart();
    return false;
  }
#if defined(MESH_USE_NIMBLE_ARDUINO)
  // The interface is owned by the application, not by the NimBLE server.
  pServer->setCallbacks(this, false);
  pServer->advertiseOnDisconnect(false);
#else
  pServer->setCallbacks(this);
#endif

#if !MESH_BLE_USES_NIMBLE
  if (custom_address != nullptr) {
    esp_bd_addr_t native_address;
    memcpy(native_address, custom_address, sizeof(native_address));
    // Arduino-ESP32 2.x returns void here, while newer Bluedroid wrappers
    // return bool. Both configure the advertising address type and report any
    // controller failure through the BLE library log.
    pServer->getAdvertising()->setDeviceAddress(
        native_address, BLE_ADDR_TYPE_RANDOM);
  }
#endif

  // Create the BLE Service
  pService = pServer->createService(SERVICE_UUID);
  if (pService == NULL) {
    BLE_DEBUG_PRINTLN("BLEServer::createService failed");
    cleanupFailedBluetoothStart();
    pServer = NULL;
    return false;
  }

  // Create a BLE Characteristic
#if defined(MESH_USE_NIMBLE_ARDUINO)
  uint32_t tx_properties = NIMBLE_PROPERTY::READ | NIMBLE_PROPERTY::NOTIFY |
                           NIMBLE_PROPERTY::READ_AUTHEN;
  uint32_t rx_properties = NIMBLE_PROPERTY::WRITE | NIMBLE_PROPERTY::WRITE_AUTHEN;
#else
  uint32_t tx_properties = BLECharacteristic::PROPERTY_READ |
                           BLECharacteristic::PROPERTY_NOTIFY;
  uint32_t rx_properties = BLECharacteristic::PROPERTY_WRITE;
#if MESH_BLE_USES_NIMBLE
  // NimBLE ignores setAccessPermissions(). Authentication requirements must
  // be part of the characteristic properties instead.
  tx_properties |= BLECharacteristic::PROPERTY_READ_AUTHEN;
  rx_properties |= BLECharacteristic::PROPERTY_WRITE_AUTHEN;
#endif
#endif
  pTxCharacteristic = pService->createCharacteristic(
      CHARACTERISTIC_UUID_TX, tx_properties);
  if (pTxCharacteristic == NULL) {
    BLE_DEBUG_PRINTLN("BLEService::createCharacteristic(TX) failed");
    cleanupFailedBluetoothStart();
    pServer = NULL;
    pService = NULL;
    return false;
  }
#if !defined(MESH_USE_NIMBLE_ARDUINO)
  pTxCharacteristic->setAccessPermissions(ESP_GATT_PERM_READ_ENC_MITM);
#endif
  pTxCharacteristic->setCallbacks(this);
#if !MESH_BLE_USES_NIMBLE
  // NimBLE creates and owns the 0x2902 descriptor automatically.
  pTxDescriptor = new BLE2902();
  // Make notification setup start/finish pairing before the client begins its
  // short device-info request timeout. The RX and TX characteristics already
  // require the same MITM-encrypted link, so this does not add a new pairing
  // requirement; it only moves it earlier in the connection handshake.
  pTxDescriptor->setAccessPermissions(
      (esp_gatt_perm_t)(ESP_GATT_PERM_READ_ENC_MITM | ESP_GATT_PERM_WRITE_ENC_MITM));
  pTxCharacteristic->addDescriptor(pTxDescriptor);
#endif

  BLECharacteristic * pRxCharacteristic = pService->createCharacteristic(
      CHARACTERISTIC_UUID_RX, rx_properties);
  if (pRxCharacteristic == NULL) {
    BLE_DEBUG_PRINTLN("BLEService::createCharacteristic(RX) failed");
    cleanupFailedBluetoothStart();
    pServer = NULL;
    pService = NULL;
    pTxCharacteristic = NULL;
#if !defined(MESH_USE_NIMBLE_ARDUINO)
    pTxDescriptor = NULL;
#endif
    return false;
  }
#if !defined(MESH_USE_NIMBLE_ARDUINO)
  pRxCharacteristic->setAccessPermissions(ESP_GATT_PERM_WRITE_ENC_MITM);
#endif
  pRxCharacteristic->setCallbacks(this);

#if defined(MESH_USE_NIMBLE_ARDUINO)
  pServer->getAdvertising()->setName(dev_name);
  pServer->getAdvertising()->enableScanResponse(true);
#endif
  pServer->getAdvertising()->addServiceUUID(SERVICE_UUID);
  if (bonded_only_peer != nullptr) {
    configureBondedOnlyAdvertising(*bonded_only_peer, true);
  }
  return true;
}

// -------- BLESecurityCallbacks methods

#if defined(MESH_USE_NIMBLE_ARDUINO)
uint32_t SerialBLEInterface::onPassKeyDisplay() {
  _pairingRequestPending.store(true, std::memory_order_release);
  return _pin_code;
}

void SerialBLEInterface::onAuthenticationComplete(NimBLEConnInfo& info) {
  if (info.isEncrypted() && info.isAuthenticated() && info.isBonded()) {
    deviceConnected = true;
    mesh::companion::BluetoothPeerIdentity peer;
    bluetoothPeerFromNimbleAddress(*info.getIdAddress().getBase(), peer);
    noteSuccessfulConnection(peer);
  } else {
    BLEDevice::deleteBond(info.getIdAddress());
    deviceConnected = false;
    if (_bonded_only) requestBondedOnlyRecovery("bond authentication failed");
    pServer->disconnect(info.getConnHandle());
    if (advertisingAllowed()) scheduleAdvertisingRestart((uint32_t)millis());
  }
}
#else
uint32_t SerialBLEInterface::onPassKeyRequest() {
  BLE_DEBUG_PRINTLN("onPassKeyRequest()");
  _pairingRequestPending.store(true, std::memory_order_release);
  return _pin_code;
}

void SerialBLEInterface::onPassKeyNotify(uint32_t pass_key) {
  BLE_DEBUG_PRINTLN("onPassKeyNotify(%u)", pass_key);
  _pairingRequestPending.store(true, std::memory_order_release);
}

bool SerialBLEInterface::onConfirmPIN(uint32_t pass_key) {
  BLE_DEBUG_PRINTLN("onConfirmPIN(%u)", pass_key);
  _pairingRequestPending.store(true, std::memory_order_release);
  return true;
}

bool SerialBLEInterface::onSecurityRequest() {
  BLE_DEBUG_PRINTLN("onSecurityRequest()");
  return true;  // allow
}

#if MESH_BLE_USES_NIMBLE
void SerialBLEInterface::onAuthenticationComplete(ble_gap_conn_desc* desc) {
  const bool success = desc != NULL && desc->sec_state.encrypted &&
                       desc->sec_state.authenticated
                       && desc->sec_state.bonded;
  if (success) {
    BLE_DEBUG_PRINTLN(" - SecurityCallback - Authentication Success");
    deviceConnected = true;
    mesh::companion::BluetoothPeerIdentity peer;
    bluetoothPeerFromNimbleAddress(desc->peer_id_addr, peer);
    noteSuccessfulConnection(peer);
  } else {
    BLE_DEBUG_PRINTLN(" - SecurityCallback - Authentication Failure");

    if (desc != NULL) {
      int remove_result = ble_store_util_delete_peer(&desc->peer_id_addr);
      if (remove_result == 0) {
        BLE_DEBUG_PRINTLN(" - SecurityCallback - Cleared failed peer bond");
      } else {
        BLE_DEBUG_PRINTLN(" - SecurityCallback - No peer bond cleared, err=%d",
                          remove_result);
      }
    }

    deviceConnected = false;
    if (_bonded_only) {
      requestBondedOnlyRecovery("bond authentication failed");
    }
    if (desc != NULL) {
      pServer->disconnect(desc->conn_handle);
    }
    if (advertisingAllowed()) {
      scheduleAdvertisingRestart((uint32_t)millis());
    }
  }
}
#else
void SerialBLEInterface::onAuthenticationComplete(esp_ble_auth_cmpl_t cmpl) {
  if (cmpl.success) {
    BLE_DEBUG_PRINTLN(" - SecurityCallback - Authentication Success");
    deviceConnected = true;
    mesh::companion::BluetoothPeerIdentity peer;
    peer.type = cmpl.addr_type == BLE_ADDR_TYPE_PUBLIC
        ? mesh::companion::BLUETOOTH_PEER_ADDRESS_PUBLIC
        : mesh::companion::BLUETOOTH_PEER_ADDRESS_RANDOM;
    memcpy(peer.address, cmpl.bd_addr, sizeof(peer.address));
    noteSuccessfulConnection(peer);
  } else {
    BLE_DEBUG_PRINTLN(" - SecurityCallback - Authentication Failure, reason=%u", (unsigned)cmpl.fail_reason);

    // Firmware flashing normally preserves the Bluetooth bond database. If
    // either side has forgotten or replaced its key, retaining the local key
    // makes every reconnect fail until the device is fully erased. Remove only
    // the peer whose authentication just failed; successful bonds are kept.
    esp_err_t remove_result = esp_ble_remove_bond_device(cmpl.bd_addr);
    if (remove_result == ESP_OK) {
      BLE_DEBUG_PRINTLN(" - SecurityCallback - Cleared failed peer bond");
    } else {
      BLE_DEBUG_PRINTLN(" - SecurityCallback - No peer bond cleared, err=%d", (int)remove_result);
    }

    deviceConnected = false;
    if (_bonded_only) {
      requestBondedOnlyRecovery("bond authentication failed");
    }
    pServer->disconnect(pServer->getConnId());
    if (advertisingAllowed()) {
      scheduleAdvertisingRestart((uint32_t)millis());
    }
  }
}
#endif

#endif

// -------- BLEServerCallbacks methods

#if defined(MESH_USE_NIMBLE_ARDUINO)
void SerialBLEInterface::onConnect(BLEServer* pServer, NimBLEConnInfo& info) {
  last_conn_id = info.getConnHandle();
  deviceConnected = false;
  oldDeviceConnected = false;
  notifySucceeded = false;
  notificationsEnabled.store(false, std::memory_order_release);
  xQueueReset(recv_queue);
  _tx_reset_pending.store(true, std::memory_order_release);
  _adv_restart_pending = false;
}

void SerialBLEInterface::onMTUChange(uint16_t mtu, NimBLEConnInfo& info) {
  (void)info;
  BLE_DEBUG_PRINTLN("onMtuChanged(), mtu=%d", mtu);
}
#else
void SerialBLEInterface::onConnect(BLEServer* pServer) {
}

#if MESH_BLE_USES_NIMBLE
void SerialBLEInterface::onConnect(BLEServer* pServer, ble_gap_conn_desc* desc) {
  BLE_DEBUG_PRINTLN("onConnect(), conn_id=%d, mtu=%d", desc->conn_handle,
                    pServer->getPeerMTU(desc->conn_handle));
  last_conn_id = desc->conn_handle;
  deviceConnected = false;  // becomes usable only after authentication completes
  oldDeviceConnected = false;
  notifySucceeded = false;
  notificationsEnabled.store(false, std::memory_order_release);
  xQueueReset(recv_queue);
  _tx_reset_pending.store(true, std::memory_order_release);
  _adv_restart_pending = false;
}

void SerialBLEInterface::onMtuChanged(BLEServer* pServer,
                                      ble_gap_conn_desc* desc,
                                      uint16_t mtu) {
  (void)pServer;
  (void)desc;
  BLE_DEBUG_PRINTLN("onMtuChanged(), mtu=%d", mtu);
}
#else
void SerialBLEInterface::onConnect(BLEServer* pServer, esp_ble_gatts_cb_param_t *param) {
  BLE_DEBUG_PRINTLN("onConnect(), conn_id=%d, mtu=%d", param->connect.conn_id, pServer->getPeerMTU(param->connect.conn_id));
  last_conn_id = param->connect.conn_id;
  deviceConnected = false;  // becomes usable only after authentication completes
  oldDeviceConnected = false;
  notifySucceeded = false;
  // BLE callbacks run outside the Arduino loop. FreeRTOS owns the RX queue,
  // so it is safe to reset here; defer the plain-array TX queue reset to the
  // loop to avoid racing a notification completion.
  xQueueReset(recv_queue);
  _tx_reset_pending.store(true, std::memory_order_release);
  _adv_restart_pending = false;
#if !defined(MESH_USE_NIMBLE_ARDUINO)
  if (pTxDescriptor != NULL) pTxDescriptor->setNotifications(false);
#endif
}

void SerialBLEInterface::onMtuChanged(BLEServer* pServer, esp_ble_gatts_cb_param_t* param) {
  BLE_DEBUG_PRINTLN("onMtuChanged(), mtu=%d", pServer->getPeerMTU(param->mtu.conn_id));
}
#endif

#endif

#if defined(MESH_USE_NIMBLE_ARDUINO)
void SerialBLEInterface::onDisconnect(BLEServer* pServer, NimBLEConnInfo& info,
                                       int reason) {
  (void)info;
  (void)reason;
#else
void SerialBLEInterface::onDisconnect(BLEServer* pServer) {
#endif
  BLE_DEBUG_PRINTLN("onDisconnect()");
  deviceConnected = false;
  notifySucceeded = false;
  notificationsEnabled.store(false, std::memory_order_release);
  xQueueReset(recv_queue);
  _tx_reset_pending.store(true, std::memory_order_release);
#if !defined(MESH_USE_NIMBLE_ARDUINO)
  if (pTxDescriptor != NULL) pTxDescriptor->setNotifications(false);
#endif
  if (advertisingAllowed()) {
    scheduleAdvertisingRestart((uint32_t)millis());
  }
}

// -------- BLECharacteristicCallbacks methods

#if defined(MESH_USE_NIMBLE_ARDUINO)
void SerialBLEInterface::onWrite(BLECharacteristic* pCharacteristic,
                                 NimBLEConnInfo& info) {
  if (!info.isEncrypted() || !info.isAuthenticated() || !info.isBonded()) return;
#elif MESH_BLE_USES_NIMBLE
void SerialBLEInterface::onWrite(BLECharacteristic* pCharacteristic,
                                 ble_gap_conn_desc* desc) {
  (void)desc;
#else
void SerialBLEInterface::onWrite(BLECharacteristic* pCharacteristic,
                                 esp_ble_gatts_cb_param_t* param) {
  (void)param;
#endif
  if (_tx_disconnect_recovery.pending()) {
    BLE_DEBUG_PRINTLN("onWrite(): dropping frame while BLE reconnect is pending");
    return;
  }

#if defined(MESH_USE_NIMBLE_ARDUINO)
  const auto value = pCharacteristic->getValue();
  const uint8_t* rxValue = value.data();
  const int len = value.size();
#else
  uint8_t* rxValue = pCharacteristic->getData();
  int len = pCharacteristic->getLength();
#endif

  if (len > MAX_FRAME_SIZE) {
    BLE_DEBUG_PRINTLN("ERROR: onWrite(), frame too big, len=%d", len);
  } else {
    Frame frame = {};
    frame.len = len;
    memcpy(frame.buf, rxValue, len);

    if (xQueueSend(recv_queue, &frame, 0) != pdTRUE) {
      BLE_DEBUG_PRINTLN("ERROR: onWrite(), recv_queue is full!");
    }
  }
}

#if defined(MESH_USE_NIMBLE_ARDUINO)
void SerialBLEInterface::onSubscribe(BLECharacteristic* pCharacteristic,
                                     NimBLEConnInfo& info, uint16_t subValue) {
  (void)info;
#elif MESH_BLE_USES_NIMBLE
void SerialBLEInterface::onSubscribe(BLECharacteristic* pCharacteristic,
                                     ble_gap_conn_desc* desc,
                                     uint16_t subValue) {
  (void)desc;
#endif
#if MESH_BLE_USES_NIMBLE
  if (pCharacteristic == pTxCharacteristic) {
    // NimBLE uses bit 0 for notification subscriptions.
    notificationsEnabled.store((subValue & 0x0001u) != 0,
                               std::memory_order_release);
  }
}
#endif

#if !defined(MESH_USE_NIMBLE_ARDUINO)
void SerialBLEInterface::onStatus(BLECharacteristic* pCharacteristic, Status status, uint32_t code) {
  (void)pCharacteristic;
  notifySucceeded = status == SUCCESS_NOTIFY;
  if (!notifySucceeded) {
    BLE_DEBUG_PRINTLN("notify failed, status=%d, code=%u; retaining frame",
                      (int)status, (unsigned)code);
  }
}

#endif

// ---------- public methods

bool SerialBLEInterface::takeSuccessfulConnection(
    mesh::companion::BluetoothPeerIdentity* peer) {
  if (!_successfulConnectionPending.load(std::memory_order_acquire)) {
    return false;
  }
  if (peer != nullptr && !resolveSuccessfulPeer(*peer)) {
    // Bond storage can finish shortly after authentication. Keep the event
    // pending so the main loop can retry without reopening general access. A
    // failed bond write must not leave the node unavailable indefinitely.
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
  return configureBondedOnlyAdvertising(peer, false);
}

void SerialBLEInterface::cancelStealthPairingTransition() {
  if (!_stealth_pair_once) return;
  _stealth_pair_once = false;
  _advertisingSuppressed.store(false, std::memory_order_release);
  if (_isEnabled && pServer != NULL && pServer->getConnectedCount() == 0) {
    scheduleAdvertisingRestart((uint32_t)millis());
  }
}

void SerialBLEInterface::clearBuffers() {
  xQueueReset(recv_queue);
  send_queue_len = 0;
  notifySucceeded = false;
  notificationsEnabled.store(false, std::memory_order_release);
  _tx_stall_watchdog.reset();
  _tx_disconnect_recovery.complete();
  _tx_reset_pending.store(false, std::memory_order_release);
}

void SerialBLEInterface::servicePendingTxReset() {
  if (!_tx_reset_pending.exchange(false, std::memory_order_acq_rel)) return;
  send_queue_len = 0;
  notifySucceeded = false;
  _tx_stall_watchdog.reset();
  _tx_disconnect_recovery.complete();
}

void SerialBLEInterface::scheduleAdvertisingRestart(uint32_t now) {
  if (!advertisingAllowed()) {
    _adv_restart_pending = false;
    return;
  }
  _adv_restart_started = now;
  _adv_restart_pending = true;
}

void SerialBLEInterface::serviceTxRecovery(uint32_t now) {
  if (!_tx_disconnect_recovery.pending()) return;

  if (pServer == NULL || pServer->getConnectedCount() == 0) {
    BLE_DEBUG_PRINTLN("SerialBLEInterface: stalled TX link is already closed");
    deviceConnected = false;
    clearBuffers();
    if (advertisingAllowed()) scheduleAdvertisingRestart(now);
    return;
  }

  if (!_tx_disconnect_recovery.shouldAttempt(now)) return;

  // BLEServer::disconnect() does not expose the controller return code. Keep
  // recovery pending and issue another bounded request until onDisconnect() or
  // getConnectedCount() confirms that the link actually closed.
  pServer->disconnect(last_conn_id);
  BLE_DEBUG_PRINTLN("SerialBLEInterface: stalled TX disconnect requested");
}

void SerialBLEInterface::recoverStalledTx(const char* cause) {
  if (_tx_disconnect_recovery.pending()) return;

  BLE_DEBUG_PRINTLN("SerialBLEInterface: %s; forcing reconnect", cause);

  // Preserve the controller's physical state until the disconnect callback,
  // while the recovery state makes isConnected() false to callers.
  xQueueReset(recv_queue);
  notifySucceeded = false;
  send_queue_len = 0;
  _tx_stall_watchdog.reset();
  _tx_disconnect_recovery.begin();
  serviceTxRecovery((uint32_t)millis());
}

void SerialBLEInterface::enable() { 
  if (_isEnabled) return;

  _isEnabled = true;
  clearBuffers();

  // Start the service
#if defined(MESH_USE_NIMBLE_ARDUINO)
  if (!pServer->start()) {
    _isEnabled = false;
    return;
  }
#else
  pService->start();
#endif

  // Start advertising

  //pServer->getAdvertising()->setMinInterval(500);
  //pServer->getAdvertising()->setMaxInterval(1000);

  if (advertisingAllowed()) {
    pServer->getAdvertising()->start();
  }
  _adv_restart_pending = false;
}

void SerialBLEInterface::disable() {
  _isEnabled = false;

  BLE_DEBUG_PRINTLN("SerialBLEInterface::disable");

  pServer->getAdvertising()->stop();
  pServer->disconnect(last_conn_id);
#if !defined(MESH_USE_NIMBLE_ARDUINO)
  pService->stop();
#endif
  oldDeviceConnected = deviceConnected = false;
  clearBuffers();
  _adv_restart_pending = false;
}

size_t SerialBLEInterface::writeFrame(const uint8_t src[], size_t len) {
  servicePendingTxReset();
  if (len > MAX_FRAME_SIZE) {
    BLE_DEBUG_PRINTLN("writeFrame(), frame too big, len=%d", len);
    return 0;
  }

  if (isConnected() && len > 0) {
    if (!mesh::enqueueCompanionFrame(send_queue, send_queue_len, FRAME_QUEUE_SIZE,
                                     src, len)) {
      BLE_DEBUG_PRINTLN("writeFrame(), send_queue is full!");
      return 0;
    }
    return len;
  }
  return 0;
}

#if defined(MESH_HIL_BLE_INTERVAL_MS)
#if !defined(MESH_HIL_BLE_BENCH) || MESH_HIL_BLE_BENCH != 1
#error "MESH_HIL_BLE_INTERVAL_MS requires the explicit MESH_HIL_BLE_BENCH=1 lab image"
#endif
#if MESH_HIL_BLE_INTERVAL_MS < 7 || MESH_HIL_BLE_INTERVAL_MS > 60
#error "MESH_HIL_BLE_INTERVAL_MS must be in 7..60 milliseconds"
#endif
#define BLE_WRITE_MIN_INTERVAL MESH_HIL_BLE_INTERVAL_MS
#else
// Preserve the shipped pacing until a separate lab image has measured a safe
// replacement. One value governs admission and both successful/failed sends.
#define BLE_WRITE_MIN_INTERVAL 60
#endif

bool SerialBLEInterface::isReadBusy() const {
  return uxQueueMessagesWaiting(recv_queue) > 0;
}

bool SerialBLEInterface::hasRequiredReplyCapacity() const {
  if (send_queue_len < FRAME_QUEUE_SIZE) return true;
  // Match enqueueCompanionFrame's admission rule: a required response can
  // displace best-effort traffic, but must never displace another required one.
  for (int i = 0; i < send_queue_len; ++i) {
    if (!mesh::companionFrameRequiresDelivery(send_queue[i].buf, send_queue[i].len))
      return true;
  }
  return false;
}

bool SerialBLEInterface::isWriteBusy() const {
  return !hasRequiredReplyCapacity()
      || !mesh::bleElapsedAtLeast((uint32_t)millis(), _last_write,
                                  BLE_WRITE_MIN_INTERVAL);
}

size_t SerialBLEInterface::checkRecvFrame(uint8_t dest[]) {
  const uint32_t now = (uint32_t)millis();
  servicePendingTxReset();
  if (_tx_disconnect_recovery.pending()) {
    serviceTxRecovery(now);
    return 0;
  }

  if (send_queue_len > 0   // first, check send queue
    && mesh::bleElapsedAtLeast(now, _last_write,
                               BLE_WRITE_MIN_INTERVAL)    // space the writes apart
  ) {
    const uint16_t peer_mtu = pServer->getPeerMTU(last_conn_id);
#if MESH_BLE_USES_NIMBLE
    const bool notifications_ready =
        notificationsEnabled.load(std::memory_order_acquire);
#else
    const bool notifications_ready =
        pTxDescriptor != NULL && pTxDescriptor->getNotifications();
#endif
    const bool frame_fits = peer_mtu > 3 && send_queue[0].len <= peer_mtu - 3;
    const bool delivery_required = mesh::companionFrameRequiresDelivery(
        send_queue[0].buf, send_queue[0].len);

    // A fresh pairing can deliver the app's first command before its CCCD
    // subscription or MTU exchange completes. Keep the response queued until
    // both are ready instead of silently dropping/truncating device info.
    if (notifications_ready && frame_fits) {
      _last_write = now;
      notifySucceeded = false;
      pTxCharacteristic->setValue(send_queue[0].buf, send_queue[0].len);
#if defined(MESH_USE_NIMBLE_ARDUINO)
      // NimBLE reports queue acceptance directly. Its status callback may run
      // later; do not retain or duplicate an already accepted frame awaiting it.
      notifySucceeded = pTxCharacteristic->notify(last_conn_id);
#else
      pTxCharacteristic->notify();
#endif

      if (notifySucceeded) {
        BLE_DEBUG_PRINTLN("writeBytes: sz=%d, hdr=%d", (uint32_t)send_queue[0].len, (uint32_t) send_queue[0].buf[0]);

        send_queue_len--;
        for (int i = 0; i < send_queue_len; i++) {   // delete top item from queue
          send_queue[i] = send_queue[i + 1];
        }
        _tx_stall_watchdog.reset();
      } else if (delivery_required
                 && _tx_stall_watchdog.noteBlocked(now)) {
        recoverStalledTx("command reply notification blocked for 10 seconds");
        return 0;
      }
    } else if (delivery_required
               && _tx_stall_watchdog.noteBlocked(now)) {
      recoverStalledTx("command reply waiting for notifications or MTU for 10 seconds");
      return 0;
    } else if (!delivery_required) {
      _tx_stall_watchdog.reset();
    }
  } else if (send_queue_len == 0) {
    _tx_stall_watchdog.reset();
  }

  Frame frame;
  // Service TX above even when full, then retain a command until its required
  // reply can be admitted. Dequeuing while all TX slots are required causes
  // one-shot replies (including CMD66) to be silently lost by writeFrame().
  if (deviceConnected && hasRequiredReplyCapacity()
      && xQueueReceive(recv_queue, &frame, 0) == pdTRUE) {
    memcpy(dest, frame.buf, frame.len);
    BLE_DEBUG_PRINTLN("readBytes: sz=%d, hdr=%d", (uint32_t) frame.len, (uint32_t) dest[0]);
    return frame.len;
  }

  if (deviceConnected != oldDeviceConnected) {
    if (!deviceConnected) {    // disconnecting
      clearBuffers();

      BLE_DEBUG_PRINTLN("SerialBLEInterface -> disconnecting...");

      //pServer->getAdvertising()->setMinInterval(500);
      //pServer->getAdvertising()->setMaxInterval(1000);

      if (advertisingAllowed()) scheduleAdvertisingRestart(now);
    } else {
      BLE_DEBUG_PRINTLN("SerialBLEInterface -> stopping advertising");
      BLE_DEBUG_PRINTLN("SerialBLEInterface -> connecting...");
      // connecting
      // do stuff here on connecting
      pServer->getAdvertising()->stop();
      _adv_restart_pending = false;
    }
    oldDeviceConnected = deviceConnected;
  }

  if (advertisingAllowed() && _adv_restart_pending &&
      mesh::bleElapsedAtLeast(now, _adv_restart_started,
                              ADVERT_RESTART_DELAY)) {
    if (pServer->getConnectedCount() == 0) {
      BLE_DEBUG_PRINTLN("SerialBLEInterface -> re-starting advertising");
      pServer->getAdvertising()->start();  // re-Start advertising
      _adv_restart_pending = false;
    } else {
      // A disconnect can take longer than the normal restart delay. Keep the
      // restart armed instead of losing it while the controller still reports
      // the old connection.
      _adv_restart_started = now;
    }
  }
  return 0;
}

bool SerialBLEInterface::isConnected() const {
  return !_tx_disconnect_recovery.pending() && deviceConnected;
}
