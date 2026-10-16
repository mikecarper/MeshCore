// only built when the env enables the core BLE stack (build_as_lib.py globs this dir)
#ifdef PIO_FRAMEWORK_ARDUINO_ENABLE_BLUETOOTH
#include "SerialBLEInterface.h"
#include "../CompanionFrameQueue.h"
#include <BluetoothLock.h>
#include <stdio.h>
#include <string.h>

// Nordic UART 6E4000xx-B5A3-F393-E0A9-E50E24DCCA9E, as raw bytes: the core lib's string
// parser uses sscanf("%llx"), which newlib-nano on the RP2040 doesn't support (yields all zeros)
#define NUS_UUID(n) { 0x6E, 0x40, 0x00, n, 0xB5, 0xA3, 0xF3, 0x93, 0xE0, 0xA9, 0xE5, 0x0E, 0x24, 0xDC, 0xCA, 0x9E }
static const uint8_t SERVICE_UUID[16]           = NUS_UUID(0x01);
static const uint8_t CHARACTERISTIC_UUID_RX[16] = NUS_UUID(0x02);
static const uint8_t CHARACTERISTIC_UUID_TX[16] = NUS_UUID(0x03);

// The BLE core lib's own setValue()/notify path reallocs the value buffer on every call,
// so a second frame queued before the radio drained the first would corrupt it. We keep our
// own frame queue and drive att_server_notify() from the can-send-now callback instead.

SerialBLEInterface::SerialBLEInterface()
  : BLEService(BLEUUID(SERVICE_UUID)),
    _rx(BLEUUID(CHARACTERISTIC_UUID_RX), BLEWrite, nullptr, ATT_SECURITY_AUTHENTICATED, ATT_SECURITY_AUTHENTICATED),
    _tx(BLEUUID(CHARACTERISTIC_UUID_TX), BLERead | BLENotify, nullptr, ATT_SECURITY_AUTHENTICATED, ATT_SECURITY_AUTHENTICATED)
{
  _isEnabled = false;
  _device_connected = false;
  _advertising_start_pending = false;
  _tx_pending = false;
  _tx_retry_pending = false;
  _tx_retry_started = 0;
  send_queue_len = 0;
  recv_queue_len = 0;
  memset(&_can_send, 0, sizeof(_can_send));
  _rx.setCallbacks(this);
  addCharacteristic(&_rx);
  addCharacteristic(&_tx);
}

bool SerialBLEInterface::begin(const char* prefix, const char* name, uint32_t pin_code,
                               const uint8_t* custom_address, bool clear_bonds,
                               bool stealth_pair_once,
                               const mesh::companion::BluetoothPeerIdentity* bonded_only_peer) {
  if (custom_address || clear_bonds || stealth_pair_once || bonded_only_peer) return false;
  // JustWorks here only makes the server request pairing on connect; caps are overridden below
  BLE.setSecurity(BLESecurityJustWorks);
  // adv data carries the 128-bit service UUID, leaving room for only 8 name chars;
  // the full name goes in the scan response
  BLE.begin(prefix);

  char mac_name[13];
  if (strcmp(name, "@@MAC") == 0) {
    bd_addr_t a;
    gap_local_bd_addr(a);
    snprintf(mac_name, sizeof(mac_name), "%02X%02X%02X%02X%02X%02X", a[0], a[1], a[2], a[3], a[4], a[5]);
    name = mac_name;
  }
  char dev_name[32+16];
  snprintf(dev_name, sizeof(dev_name), "%s%s", prefix, name);

  BLE.server()->setName(dev_name);   // GAP device name characteristic
  BLE.server()->addService(this);
  BLE.server()->setCallbacks(this);

  // static passkey pairing with MITM protection, matching the esp32/nrf52 interfaces
  sm_set_io_capabilities(IO_CAPABILITY_DISPLAY_ONLY);
  sm_set_authentication_requirements(SM_AUTHREQ_MITM_PROTECTION | SM_AUTHREQ_BONDING);
  sm_use_fixed_passkey_in_display_role(pin_code);

  size_t n = strlen(dev_name);
  if (n > sizeof(_scan_rsp) - 2) n = sizeof(_scan_rsp) - 2;
  _scan_rsp[0] = n + 1;
  _scan_rsp[1] = BLUETOOTH_DATA_TYPE_COMPLETE_LOCAL_NAME;
  memcpy(&_scan_rsp[2], dev_name, n);
  gap_scan_response_set_data(n + 2, _scan_rsp);

  BLE_DEBUG_PRINTLN("begin: name=%s", dev_name);
  return true;
}

void SerialBLEInterface::clearBuffers() {
  send_queue_len = 0;
  recv_queue_len = 0;
  _tx_pending = false;   // a stale registration just fires into an empty queue; BTstack ignores double-adds
  _tx_retry_pending = false;
  _tx_retry_started = 0;
  _tx_stall_watchdog.reset();
}

void SerialBLEInterface::onConnect(BLEServer* s) {
  BLE_DEBUG_PRINTLN("connected handle=0x%04X", _tx.conHandle());
  _device_connected = true;
  _advertising_start_pending = false;
  clearBuffers();
  _tx_disconnect_recovery.complete();
  // Advertising disable is asynchronous. Close a connection-complete event
  // already in flight instead of leaving a disabled live link behind.
  if (!_isEnabled) _tx_disconnect_recovery.begin();
}

void SerialBLEInterface::onDisconnect(BLEServer* s) {
  BLE_DEBUG_PRINTLN("disconnected");
  _device_connected = false;
  clearBuffers();   // BTstack re-enables advertising on its own
  _tx_disconnect_recovery.complete();
}

void SerialBLEInterface::onWrite(BLECharacteristic* c) {
  if (c != &_rx || !_isEnabled || _tx_disconnect_recovery.pending()) return;
  size_t len = _rx.valueLen();
  if (len == 0 || len > MAX_FRAME_SIZE) {
    BLE_DEBUG_PRINTLN("onWrite: bad frame len=%u", (unsigned)len);
    return;
  }
  if (recv_queue_len >= FRAME_QUEUE_SIZE) {
    BLE_DEBUG_PRINTLN("onWrite: recv queue full, dropping frame");
    return;
  }
  recv_queue[recv_queue_len].len = len;
  memcpy(recv_queue[recv_queue_len].buf, _rx.valueData(), len);
  recv_queue_len++;
}

// caller holds the BT lock (or is in the BT context)
void SerialBLEInterface::kickSend() {
  if (_tx_pending || send_queue_len == 0 || !_isEnabled
      || !_device_connected || _tx_disconnect_recovery.pending()) return;
  if (_tx_retry_pending
      && !mesh::bleElapsedAtLeast((uint32_t)millis(), _tx_retry_started, 10)) return;
  _tx_pending = true;
  _can_send.callback = onCanSend;
  _can_send.context = this;
  const uint8_t status = att_server_register_can_send_now_callback(&_can_send, _tx.conHandle());
  // The callback can run synchronously and clear _tx_pending. A duplicate
  // registration instead means the existing node is still waiting to run.
  if (status != ERROR_CODE_SUCCESS && status != ERROR_CODE_COMMAND_DISALLOWED) {
    _tx_pending = false;
    _tx_retry_pending = true;
    _tx_retry_started = (uint32_t)millis();
  }
}

// BT context: one notification per can-send-now, re-arm while frames remain
void SerialBLEInterface::sendNext() {
  _tx_pending = false;
  if (send_queue_len == 0) return;
  if (!_isEnabled || !_device_connected || _tx_disconnect_recovery.pending()) return;
  uint16_t h = _tx.conHandle();
  Frame& f = send_queue[0];
  uint16_t mtu = att_server_get_mtu(h);
  const bool required = mesh::companionFrameRequiresDelivery(f.buf, f.len);
  const bool ready = _tx.notifyEnabled() && mtu > 3 && f.len <= mtu - 3;
  // BTstack returns a status, not a byte count. Never submit an oversized
  // protocol frame: the app requires one complete frame per notification.
  const bool sent = ready && att_server_notify(h, _tx.valueHandle(), f.buf, f.len) == ERROR_CODE_SUCCESS;
  if (!sent && required) {
    // Re-registering here can invoke this callback synchronously forever.
    // Retain the whole frame and let loop() perform a throttled retry.
    _tx_retry_pending = true;
    _tx_retry_started = (uint32_t)millis();
    _tx_stall_watchdog.noteBlocked(_tx_retry_started);
    return;
  }
  _tx_retry_pending = false;
  _tx_stall_watchdog.reset();
  send_queue_len--;
  memmove(&send_queue[0], &send_queue[1], send_queue_len * sizeof(Frame));
  if (send_queue_len > 0) kickSend();
}

void SerialBLEInterface::enable() {
  BluetoothLock lock;
  if (_isEnabled) return;
  _isEnabled = true;
  clearBuffers();
  if (_device_connected || _tx_disconnect_recovery.pending()) {
    // Do not rebuild ATT or advertise while the old controller link closes.
    _advertising_start_pending = true;
  } else {
    BLE.startAdvertising();
  }
}

void SerialBLEInterface::disconnect() {
  BluetoothLock lock;
  if (_device_connected) recoverStalledTx();
}

void SerialBLEInterface::disable() {
  BluetoothLock lock;
  _isEnabled = false;
  _advertising_start_pending = false;
  BLE_DEBUG_PRINTLN("disable");
  disconnect();
  clearBuffers();
  BLE.stopAdvertising();
}

bool SerialBLEInterface::isConnected() const {
  BluetoothLock lock;
  // notifications can only be enabled once the link is authenticated (CCCD inherits the perms)
  return _isEnabled && _device_connected && !_tx_disconnect_recovery.pending()
      && _tx.conHandle() != 0 && _tx.notifyEnabled();
}

bool SerialBLEInterface::hasRequiredReplyCapacity() const {
  if (send_queue_len < FRAME_QUEUE_SIZE) return true;
  for (unsigned i = 0; i < send_queue_len; ++i) {
    if (!mesh::companionFrameRequiresDelivery(send_queue[i].buf, send_queue[i].len)) return true;
  }
  return false;
}

void SerialBLEInterface::serviceTxRecovery(uint32_t now) {
  if (_tx_disconnect_recovery.pending() && _device_connected
      && _tx_disconnect_recovery.shouldAttempt(now)) {
    // A successful request is not proof of closure. Retry until the actual
    // disconnect callback, even if CCCD was disabled or the first call failed.
    gap_disconnect(_tx.conHandle());
  }
}

void SerialBLEInterface::recoverStalledTx() {
  if (!_tx_disconnect_recovery.pending()) {
    clearBuffers();
    _tx_disconnect_recovery.begin();
  }
  serviceTxRecovery((uint32_t)millis());
}

void SerialBLEInterface::loop() {
  BluetoothLock lock;
  const uint32_t now = (uint32_t)millis();
  if (_tx_disconnect_recovery.pending()) {
    serviceTxRecovery(now);
    return;
  }
  if (!_isEnabled) return;
  if (_advertising_start_pending && !_device_connected) {
    _advertising_start_pending = false;
    BLE.startAdvertising();
  }
  if (send_queue_len == 0) {
    _tx_stall_watchdog.reset();
    return;
  }
  // Attempt progress before declaring a timeout after a scheduler gap.
  if (!_tx_pending) kickSend();
  if (send_queue_len == 0) {
    _tx_stall_watchdog.reset();
    return;
  }
  if (mesh::companionFrameRequiresDelivery(send_queue[0].buf, send_queue[0].len)) {
    if (_tx_stall_watchdog.noteBlocked((uint32_t)millis())) {
      recoverStalledTx();
      return;
    }
  } else {
    _tx_stall_watchdog.reset();
  }
}

bool SerialBLEInterface::isWriteBusy() const {
  BluetoothLock lock;
  return _tx_disconnect_recovery.pending() || send_queue_len >= (FRAME_QUEUE_SIZE * 2 / 3);
}

bool SerialBLEInterface::isReadBusy() const {
  BluetoothLock lock;
  return recv_queue_len > 0;
}

bool SerialBLEInterface::hasPendingIO() const {
  BluetoothLock lock;
  return recv_queue_len > 0 || send_queue_len > 0 || _tx_pending || _tx_disconnect_recovery.pending();
}

size_t SerialBLEInterface::writeFrame(const uint8_t src[], size_t len) {
  if (src == nullptr || len == 0 || len > MAX_FRAME_SIZE) {
    BLE_DEBUG_PRINTLN("writeFrame(), frame too big, len=%u", (unsigned)len);
    return 0;
  }
  BluetoothLock lock;
  if (!isConnected()) return 0;
  if (!mesh::enqueueCompanionFrame(send_queue, send_queue_len, FRAME_QUEUE_SIZE, src, len)) {
    BLE_DEBUG_PRINTLN("writeFrame(), send_queue is full!");
    return 0;
  }
  kickSend();
  return len;
}

size_t SerialBLEInterface::checkRecvFrame(uint8_t dest[]) {
  BluetoothLock lock;
  if (dest == nullptr || !isConnected() || recv_queue_len == 0
      || !hasRequiredReplyCapacity()) return 0;

  size_t len = recv_queue[0].len;
  memcpy(dest, recv_queue[0].buf, len);
  recv_queue_len--;
  memmove(&recv_queue[0], &recv_queue[1], recv_queue_len * sizeof(Frame));
  BLE_DEBUG_PRINTLN("readBytes: sz=%u, hdr=%u", (unsigned)len, dest[0]);
  return len;
}
#endif
