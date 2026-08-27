#include "SerialBLEInterface.h"
#include "../CompanionFrameQueue.h"
#include <stdio.h>
#include <string.h>
#include "ble_gap.h"
#include "ble_hci.h"
#include <utility/bonding.h>

// Magic numbers came from actual testing
#define BLE_HEALTH_CHECK_INTERVAL  10000  // Advertising watchdog check every 10 seconds
#define BLE_RETRY_THROTTLE_MS      250    // Throttle retries to 250ms when queue buildup detected

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
        instance->disconnect();
        return;
      }

      instance->_isDeviceConnected = true;
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
      } else {
        BLE_DEBUG_PRINTLN("SerialBLEInterface: pairing failed, clearing stale bond and disconnecting");
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
    }
  } else if (evt->header.evt_id == BLE_GAP_EVT_CONN_SEC_UPDATE) {
    uint16_t conn_handle = evt->evt.gap_evt.conn_handle;
    BLEConnection* conn = Bluefruit.Connection(conn_handle);
    if (conn != nullptr && conn->connected() && !conn->secured()) {
      BLE_DEBUG_PRINTLN("CONN_SEC_UPDATE: link is not secured, bonded=%d", conn->bonded());
      if (conn->bonded()) {
        instance->removeStoredBondForPeer("failed bond encryption");
      }
      instance->_isDeviceConnected = false;
      instance->_security_timer.cancel();
      sd_ble_gap_disconnect(conn_handle, BLE_HCI_REMOTE_USER_TERMINATED_CONNECTION);
    }
  } else if (evt->header.evt_id == BLE_GAP_EVT_DISCONNECTED) {
    ble_gap_evt_disconnected_t const* disconnected = &evt->evt.gap_evt.params.disconnected;
    if (isBondAuthenticationFailure(disconnected->reason)) {
      instance->removeStoredBondForPeer("authentication disconnect");
    }
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

bool SerialBLEInterface::begin(const char* prefix, const char* name, uint32_t pin_code) {
  instance = this;

  char charpin[20];
  snprintf(charpin, sizeof(charpin), "%lu", (unsigned long)pin_code);
  
  // If we want to control BLE LED ourselves, uncomment this:
  // Bluefruit.autoConnLed(false);
  Bluefruit.configPrphBandwidth(BANDWIDTH_MAX);
  if (!Bluefruit.begin()) {
    instance = nullptr;
    BLE_DEBUG_PRINTLN("Bluefruit.begin failed");
    return false;
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
  bleuart.begin();
  bleuart.setRxCallback(onBleUartRX);

#if COMPANION_FEATURE_BLE_MOTA_SOURCE
  _mota_stream.setSender(sendMotaRequest, this);
  _mota_stream.setActive(false);

  _mota_service.setPermission(SECMODE_ENC_WITH_MITM,
                              SECMODE_ENC_WITH_MITM);
  if (_mota_service.begin() != ERROR_NONE) {
    BLE_DEBUG_PRINTLN("Bluetooth mOTA service begin failed");
    return false;
  }

  _mota_request.setProperties(CHR_PROPS_NOTIFY);
  _mota_request.setPermission(SECMODE_ENC_WITH_MITM,
                              SECMODE_NO_ACCESS);
  _mota_request.setMaxLen(mesh::ota::BLE_MOTA_REQUEST_MAX);
  _mota_request.setUserDescriptor("mOTA device request");
  if (_mota_request.begin() != ERROR_NONE) {
    BLE_DEBUG_PRINTLN("Bluetooth mOTA request characteristic begin failed");
    return false;
  }

  _mota_response.setProperties(CHR_PROPS_WRITE);
  _mota_response.setPermission(SECMODE_NO_ACCESS,
                               SECMODE_ENC_WITH_MITM);
  _mota_response.setMaxLen(mesh::ota::BLE_MOTA_RESPONSE_MAX);
  _mota_response.setUserDescriptor("mOTA host response");
  _mota_response.setWriteCallback(onMotaResponse);
  if (_mota_response.begin() != ERROR_NONE) {
    BLE_DEBUG_PRINTLN("Bluetooth mOTA response characteristic begin failed");
    return false;
  }
#endif


  // Register DFU on the main BLE stack so paired clients can discover it
  // without switching the device into a separate OTA-only BLE mode first.
  bledfu.setPermission(SECMODE_ENC_WITH_MITM, SECMODE_ENC_WITH_MITM);
  bledfu.begin();

  Bluefruit.Advertising.addFlags(BLE_GAP_ADV_FLAGS_LE_ONLY_GENERAL_DISC_MODE);
  Bluefruit.Advertising.addTxPower();
  Bluefruit.Advertising.addService(bleuart);

  Bluefruit.ScanResponse.addName();

  Bluefruit.Advertising.setInterval(BLE_ADV_INTERVAL_MIN, BLE_ADV_INTERVAL_MAX);
  Bluefruit.Advertising.setFastTimeout(BLE_ADV_FAST_TIMEOUT);

  Bluefruit.Advertising.restartOnDisconnect(true);

  return true;
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
    if (_isEnabled && !isAdvertising()) {
      Bluefruit.Advertising.start(0);
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
  ble_gap_addr_t adv_addr;
  uint32_t err_code = sd_ble_gap_adv_addr_get(0, &adv_addr);
  return (err_code == NRF_SUCCESS);
}

void SerialBLEInterface::enable() {
  if (_isEnabled) return;

  _pairingRequestPending.store(false, std::memory_order_release);
  _isEnabled = true;
  clearBuffers();
  _last_health_check = millis();

  Bluefruit.Advertising.restartOnDisconnect(true);
  Bluefruit.Advertising.start(0);
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
  if (_isEnabled && !isConnected() && _conn_handle == BLE_CONN_HANDLE_INVALID) {
    if (now - _last_health_check >= BLE_HEALTH_CHECK_INTERVAL) {
      _last_health_check = now;
      
      if (!isAdvertising()) {
        BLE_DEBUG_PRINTLN("SerialBLEInterface: advertising watchdog - advertising stopped, restarting");
        Bluefruit.Advertising.start(0);
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
  return isConnected() && _conn_handle != BLE_CONN_HANDLE_INVALID
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
