#include "SerialBLEInterface.h"
#include "../CompanionFrameQueue.h"
#include "esp_mac.h"
#include "esp_gap_ble_api.h"

// See the following for generating UUIDs:
// https://www.uuidgenerator.net/

#define SERVICE_UUID           "6E400001-B5A3-F393-E0A9-E50E24DCCA9E" // UART service UUID
#define CHARACTERISTIC_UUID_RX "6E400002-B5A3-F393-E0A9-E50E24DCCA9E"
#define CHARACTERISTIC_UUID_TX "6E400003-B5A3-F393-E0A9-E50E24DCCA9E"

#define ADVERT_RESTART_DELAY  1000   // millis

void SerialBLEInterface::begin(const char* prefix, char* name, uint32_t pin_code) {
  _pin_code = pin_code;

  if (strcmp(name, "@@MAC") == 0) {
    uint8_t addr[8];
    memset(addr, 0, sizeof(addr));
    esp_efuse_mac_get_default(addr);
    sprintf(name, "%02X%02X%02X%02X%02X%02X",    // modify (IN-OUT param)
          addr[5], addr[4], addr[3], addr[2], addr[1], addr[0]);
  }
  char dev_name[32+16];
  sprintf(dev_name, "%s%s", prefix, name);

  // Create the BLE Device
  BLEDevice::init(dev_name);
  BLEDevice::setSecurityCallbacks(this);
  // ATT notifications consume three bytes of the negotiated MTU. Reserve
  // that overhead so a MAX_FRAME_SIZE protocol frame fits without truncation.
  BLEDevice::setMTU(MAX_FRAME_SIZE + 3);

  BLESecurity  sec;
  sec.setStaticPIN(pin_code);
  sec.setAuthenticationMode(ESP_LE_AUTH_REQ_SC_MITM_BOND);

  //BLEDevice::setPower(ESP_PWR_LVL_N8);

  // Create the BLE Server
  pServer = BLEDevice::createServer();
  pServer->setCallbacks(this);

  // Create the BLE Service
  pService = pServer->createService(SERVICE_UUID);

  // Create a BLE Characteristic
  pTxCharacteristic = pService->createCharacteristic(CHARACTERISTIC_UUID_TX, BLECharacteristic::PROPERTY_READ | BLECharacteristic::PROPERTY_NOTIFY);
  pTxCharacteristic->setAccessPermissions(ESP_GATT_PERM_READ_ENC_MITM);
  pTxCharacteristic->setCallbacks(this);
  pTxDescriptor = new BLE2902();
  // Make notification setup start/finish pairing before the client begins its
  // short device-info request timeout. The RX and TX characteristics already
  // require the same MITM-encrypted link, so this does not add a new pairing
  // requirement; it only moves it earlier in the connection handshake.
  pTxDescriptor->setAccessPermissions(
      (esp_gatt_perm_t)(ESP_GATT_PERM_READ_ENC_MITM | ESP_GATT_PERM_WRITE_ENC_MITM));
  pTxCharacteristic->addDescriptor(pTxDescriptor);

  BLECharacteristic * pRxCharacteristic = pService->createCharacteristic(CHARACTERISTIC_UUID_RX, BLECharacteristic::PROPERTY_WRITE);
  pRxCharacteristic->setAccessPermissions(ESP_GATT_PERM_WRITE_ENC_MITM);
  pRxCharacteristic->setCallbacks(this);

  pServer->getAdvertising()->addServiceUUID(SERVICE_UUID);
}

// -------- BLESecurityCallbacks methods

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

void SerialBLEInterface::onAuthenticationComplete(esp_ble_auth_cmpl_t cmpl) {
  if (cmpl.success) {
    BLE_DEBUG_PRINTLN(" - SecurityCallback - Authentication Success");
    deviceConnected = true;
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
    pServer->disconnect(pServer->getConnId());
    scheduleAdvertisingRestart((uint32_t)millis());
  }
}

// -------- BLEServerCallbacks methods

void SerialBLEInterface::onConnect(BLEServer* pServer) {
}

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
  if (pTxDescriptor != NULL) pTxDescriptor->setNotifications(false);
}

void SerialBLEInterface::onMtuChanged(BLEServer* pServer, esp_ble_gatts_cb_param_t* param) {
  BLE_DEBUG_PRINTLN("onMtuChanged(), mtu=%d", pServer->getPeerMTU(param->mtu.conn_id));
}

void SerialBLEInterface::onDisconnect(BLEServer* pServer) {
  BLE_DEBUG_PRINTLN("onDisconnect()");
  deviceConnected = false;
  notifySucceeded = false;
  xQueueReset(recv_queue);
  _tx_reset_pending.store(true, std::memory_order_release);
  if (pTxDescriptor != NULL) pTxDescriptor->setNotifications(false);
  if (_isEnabled) {
    scheduleAdvertisingRestart((uint32_t)millis());
  }
}

// -------- BLECharacteristicCallbacks methods

void SerialBLEInterface::onWrite(BLECharacteristic* pCharacteristic, esp_ble_gatts_cb_param_t* param) {
  if (_tx_disconnect_recovery.pending()) {
    BLE_DEBUG_PRINTLN("onWrite(): dropping frame while BLE reconnect is pending");
    return;
  }

  uint8_t* rxValue = pCharacteristic->getData();
  int len = pCharacteristic->getLength();

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

void SerialBLEInterface::onStatus(BLECharacteristic* pCharacteristic, Status status, uint32_t code) {
  (void)pCharacteristic;
  notifySucceeded = status == SUCCESS_NOTIFY;
  if (!notifySucceeded) {
    BLE_DEBUG_PRINTLN("notify failed, status=%d, code=%u; retaining frame",
                      (int)status, (unsigned)code);
  }
}

// ---------- public methods

void SerialBLEInterface::clearBuffers() {
  xQueueReset(recv_queue);
  send_queue_len = 0;
  notifySucceeded = false;
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
  _adv_restart_started = now;
  _adv_restart_pending = true;
}

void SerialBLEInterface::serviceTxRecovery(uint32_t now) {
  if (!_tx_disconnect_recovery.pending()) return;

  if (pServer == NULL || pServer->getConnectedCount() == 0) {
    BLE_DEBUG_PRINTLN("SerialBLEInterface: stalled TX link is already closed");
    deviceConnected = false;
    clearBuffers();
    if (_isEnabled) scheduleAdvertisingRestart(now);
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
  pService->start();

  // Start advertising

  //pServer->getAdvertising()->setMinInterval(500);
  //pServer->getAdvertising()->setMaxInterval(1000);

  pServer->getAdvertising()->start();
  _adv_restart_pending = false;
}

void SerialBLEInterface::disable() {
  _isEnabled = false;

  BLE_DEBUG_PRINTLN("SerialBLEInterface::disable");

  pServer->getAdvertising()->stop();
  pServer->disconnect(last_conn_id);
  pService->stop();
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

#define  BLE_WRITE_MIN_INTERVAL   60

bool SerialBLEInterface::isReadBusy() const {
  return uxQueueMessagesWaiting(recv_queue) > 0;
}

bool SerialBLEInterface::isWriteBusy() const {
  return !mesh::bleElapsedAtLeast((uint32_t)millis(), _last_write,
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
    const bool notifications_ready = pTxDescriptor != NULL && pTxDescriptor->getNotifications();
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
      pTxCharacteristic->notify();

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
  if (deviceConnected && xQueueReceive(recv_queue, &frame, 0) == pdTRUE) {
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

      scheduleAdvertisingRestart(now);
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

  if (_adv_restart_pending &&
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
