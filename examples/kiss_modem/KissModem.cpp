#include "KissModem.h"
#include <CayenneLPP.h>

namespace {

uint16_t readU16LE(const uint8_t* data) {
  return (uint16_t)data[0] | ((uint16_t)data[1] << 8);
}

uint32_t readU32LE(const uint8_t* data) {
  return (uint32_t)data[0] | ((uint32_t)data[1] << 8)
      | ((uint32_t)data[2] << 16) | ((uint32_t)data[3] << 24);
}

void writeU16LE(uint8_t* data, uint16_t value) {
  data[0] = (uint8_t)value;
  data[1] = (uint8_t)(value >> 8);
}

void writeU32LE(uint8_t* data, uint32_t value) {
  data[0] = (uint8_t)value;
  data[1] = (uint8_t)(value >> 8);
  data[2] = (uint8_t)(value >> 16);
  data[3] = (uint8_t)(value >> 24);
}

uint32_t frequencyToHz(float mhz) {
  return mhz > 0 ? (uint32_t)((double)mhz * 1000000.0 + 0.5) : 0;
}

uint32_t bandwidthToHz(float khz) {
  return khz > 0 ? (uint32_t)((double)khz * 1000.0 + 0.5) : 0;
}

}  // namespace

KissModem::KissModem(Stream& serial, mesh::LocalIdentity& identity, mesh::RNG& rng,
                     mesh::Radio& radio, mesh::MainBoard& board, SensorManager& sensors)
  : _serial(serial), _identity(identity), _rng(rng), _radio(radio), _board(board), _sensors(sensors) {
  _rx_len = 0;
  _rx_escaped = false;
  _rx_active = false;
  _has_pending_tx = false;
  _pending_tx_len = 0;
  _pending_tx_profile = 0;
  _pending_tx_profile_generation = 0;
  _txdelay = KISS_DEFAULT_TXDELAY;
  _persistence = KISS_DEFAULT_PERSISTENCE;
  _slottime = KISS_DEFAULT_SLOTTIME;
  _txtail = 0;
  _fullduplex = 0;
  _tx_state = TX_IDLE;
  _tx_timer = 0;
  _setRadioCallback = nullptr;
  _setTxPowerCallback = nullptr;
  _getCurrentRssiCallback = nullptr;
  _getStatsCallback = nullptr;
  _config = {0, 0, 0, 0, 0};
  _saved_radio2 = {};
  _temporary_radio2 = {};
  _temporary_radio2_active = false;
  _temporary_radio2_end_ms = 0;
  _signal_report_enabled = true;
  resetOutputQueue();
}

void KissModem::begin() {
  _rx_len = 0;
  _rx_escaped = false;
  _rx_active = false;
  _has_pending_tx = false;
  _pending_tx_profile = 0;
  _pending_tx_profile_generation = 0;
  _tx_state = TX_IDLE;
  syncPrimaryConfigFromRadio();
  syncSavedRadio2FromRadio();
  resetOutputQueue();
}

void KissModem::resetOutputQueue() {
  _tx_frame_head = 0;
  _tx_frame_tail = 0;
  _tx_frame_count = 0;
  _tx_busy_error_pending = false;
  _tx_done_pending = false;
  _tx_done_result = 0;
}

void KissModem::popTxFrame() {
  _tx_frame_head = (uint8_t)((_tx_frame_head + 1) % KISS_TX_FRAME_QUEUE_DEPTH);
  _tx_frame_count--;
}

uint16_t KissModem::appendEscapedByte(uint8_t* dest, uint16_t idx, uint16_t max_len, uint8_t b) {
  if (b == KISS_FEND || b == KISS_FESC) {
    if (idx + 2 > max_len) {
      return 0;
    }
    dest[idx++] = KISS_FESC;
    dest[idx++] = (b == KISS_FEND) ? KISS_TFEND : KISS_TFESC;
    return idx;
  }
  if (idx + 1 > max_len) {
    return 0;
  }
  dest[idx++] = b;
  return idx;
}

uint16_t KissModem::encodeFrame(uint8_t type, const uint8_t* data, uint16_t len, uint8_t* dest, uint16_t max_len) {
  if (max_len < KISS_FRAME_BOUNDARY_BYTES) {
    return 0;
  }

  uint16_t idx = 0;
  dest[idx++] = KISS_FEND;

  idx = appendEscapedByte(dest, idx, max_len, type);
  if (idx == 0) {
    return 0;
  }

  for (uint16_t i = 0; i < len; i++) {
    idx = appendEscapedByte(dest, idx, max_len, data[i]);
    if (idx == 0) {
      return 0;
    }
  }

  if (idx + 1 > max_len) {
    return 0;
  }
  dest[idx++] = KISS_FEND;
  return idx;
}

bool KissModem::tryFlushFrames() {
  while (_tx_frame_count > 0) {
    const uint8_t idx = _tx_frame_head;
    const uint16_t frame_len = _tx_frame_len[idx];
    uint16_t written_len = _tx_frame_written[idx];

    if (written_len >= frame_len) {
      popTxFrame();
      continue;
    }

    const int available = _serial.availableForWrite();
    if (available <= 0) {
      return false;
    }

    const uint16_t remaining = frame_len - written_len;
    const uint16_t chunk_len = (available < (int)remaining) ? (uint16_t)available : remaining;
    if (chunk_len == 0) {
      return false;
    }

    size_t chunk_written = _serial.write(_tx_frame_buf[idx] + written_len, chunk_len);
    if (chunk_written == 0) {
      return false;
    }

    written_len += (uint16_t)chunk_written;
    _tx_frame_written[idx] = written_len;

    if (written_len < frame_len) {
      return false;
    }

    popTxFrame();
  }
  return true;
}

bool KissModem::queueFrame(uint8_t type, const uint8_t* data, uint16_t len, bool mark_busy_error) {
  if (_tx_frame_count >= KISS_TX_FRAME_QUEUE_DEPTH && !tryFlushFrames()) {
    if (mark_busy_error) {
      _tx_busy_error_pending = true;
    }
    return false;
  }
  const uint8_t idx = _tx_frame_tail;
  uint16_t frame_len = encodeFrame(type, data, len, _tx_frame_buf[idx], sizeof(_tx_frame_buf[idx]));
  if (frame_len == 0) {
    return false;
  }

  _tx_frame_len[idx] = frame_len;
  _tx_frame_written[idx] = 0;
  _tx_frame_tail = (uint8_t)((_tx_frame_tail + 1) % KISS_TX_FRAME_QUEUE_DEPTH);
  _tx_frame_count++;
  tryFlushFrames();
  return true;
}

bool KissModem::queuePendingBusyError() {
  if (!_tx_busy_error_pending) {
    return true;
  }
  const uint8_t err = HW_ERR_TX_BUSY;
  if (!queueHardwareFrame(HW_RESP_ERROR, &err, 1, false)) {
    return false;
  }
  _tx_busy_error_pending = false;
  return true;
}

bool KissModem::queueHardwareFrame(uint8_t sub_cmd, const uint8_t* data, uint16_t len, bool mark_busy_error) {
  if (len > KISS_MAX_FRAME_SIZE) {
    return false;
  }
  _tx_hw_payload[0] = sub_cmd;
  if (len > 0) {
    memcpy(_tx_hw_payload + 1, data, len);
  }
  return queueFrame(KISS_CMD_SETHARDWARE, _tx_hw_payload, len + 1, mark_busy_error);
}

bool KissModem::queuePendingTxDone() {
  if (!_tx_done_pending) {
    return true;
  }
  if (!queueHardwareFrame(HW_RESP_TX_DONE, &_tx_done_result, 1, false)) {
    return false;
  }
  _tx_done_pending = false;
  return true;
}

void KissModem::setTxDonePending(uint8_t result) {
  _tx_done_result = result;
  _tx_done_pending = true;
  _tx_state = TX_DONE_PENDING;
}

bool KissModem::writeHardwareFrame(uint8_t sub_cmd, const uint8_t* data, uint16_t len) {
  return queueHardwareFrame(sub_cmd, data, len, true);
}

void KissModem::writeHardwareError(uint8_t error_code) {
  writeHardwareFrame(HW_RESP_ERROR, &error_code, 1);
}

void KissModem::loop() {
  expireTemporaryRadio2();
  tryFlushFrames();

  uint8_t serviced_bytes = 0;
  while (serviced_bytes < KISS_RX_SERVICE_BYTE_BUDGET && _serial.available()) {
    uint8_t b = _serial.read();
    ++serviced_bytes;

    if (b == KISS_FEND) {
      if (_rx_active && _rx_len > 0) {
        processFrame();
      }
      _rx_len = 0;
      _rx_escaped = false;
      _rx_active = true;
      continue;
    }

    if (!_rx_active) continue;

    if (b == KISS_FESC) {
      _rx_escaped = true;
      continue;
    }

    if (_rx_escaped) {
      _rx_escaped = false;
      if (b == KISS_TFEND) b = KISS_FEND;
      else if (b == KISS_TFESC) b = KISS_FESC;
      else continue;
    }

    if (_rx_len < KISS_MAX_FRAME_SIZE) {
      _rx_buf[_rx_len++] = b;
    } else {
      /* Buffer full with no FEND; reset so we don't stay stuck ignoring input. */
      _rx_len = 0;
      _rx_escaped = false;
      _rx_active = false;
    }
  }

  processTx();
  tryFlushFrames();
  queuePendingBusyError();
}

void KissModem::processFrame() {
  if (_rx_len < 1) return;

  uint8_t type_byte = _rx_buf[0];

  if (type_byte == KISS_CMD_RETURN) return;

  uint8_t port = (type_byte >> 4) & 0x0F;
  uint8_t cmd = type_byte & 0x0F;

  const uint8_t* data = &_rx_buf[1];
  uint16_t data_len = _rx_len - 1;

  // KISS v2 exposes the two time-shared profiles as logical data ports.  All
  // configuration/control commands remain on port 0, so legacy KISS clients
  // keep their exact command framing.
  if (cmd == KISS_CMD_DATA) {
    if (port > 1) return;
    if (data_len == 0 || data_len > KISS_MAX_PACKET_SIZE) return;
    if (_has_pending_tx) {
      writeHardwareError(HW_ERR_TX_BUSY);
      return;
    }

    const mesh::RadioProfiles* profiles = _radio.profiles();
    // Accept a legal logical port even if its current profile cannot TX.  The
    // state machine emits the normal TxDone(0) verdict below, which lets KISS
    // clients resolve a Data request without guessing whether F1 belonged to
    // DATA or to an unrelated concurrent SetHardware command.
    memcpy(_pending_tx, data, data_len);
    _pending_tx_len = data_len;
    _pending_tx_profile = port;
    _pending_tx_profile_generation = profiles ? profiles->generation[port] : 0;
    _has_pending_tx = true;
    return;
  }

  if (port != 0) return;

  switch (cmd) {
    case KISS_CMD_TXDELAY:
      if (data_len >= 1) _txdelay = data[0];
      break;

    case KISS_CMD_PERSISTENCE:
      if (data_len >= 1) _persistence = data[0];
      break;

    case KISS_CMD_SLOTTIME:
      if (data_len >= 1) _slottime = data[0];
      break;

    case KISS_CMD_TXTAIL:
      if (data_len >= 1) _txtail = data[0];
      break;

    case KISS_CMD_FULLDUPLEX:
      if (data_len >= 1) _fullduplex = data[0];
      break;

    case KISS_CMD_SETHARDWARE:
      if (data_len >= 1) {
        handleHardwareCommand(data[0], data + 1, data_len - 1);
      }
      break;

    default:
      break;
  }
}

void KissModem::handleHardwareCommand(uint8_t sub_cmd, const uint8_t* data, uint16_t len) {
  switch (sub_cmd) {
    case HW_CMD_GET_IDENTITY:
      handleGetIdentity();
      break;
    case HW_CMD_GET_RANDOM:
      handleGetRandom(data, len);
      break;
    case HW_CMD_VERIFY_SIGNATURE:
      handleVerifySignature(data, len);
      break;
    case HW_CMD_SIGN_DATA:
      handleSignData(data, len);
      break;
    case HW_CMD_ENCRYPT_DATA:
      handleEncryptData(data, len);
      break;
    case HW_CMD_DECRYPT_DATA:
      handleDecryptData(data, len);
      break;
    case HW_CMD_KEY_EXCHANGE:
      handleKeyExchange(data, len);
      break;
    case HW_CMD_HASH:
      handleHash(data, len);
      break;
    case HW_CMD_SET_RADIO:
      handleSetRadio(data, len);
      break;
    case HW_CMD_SET_TX_POWER:
      handleSetTxPower(data, len);
      break;
    case HW_CMD_GET_RADIO:
      handleGetRadio();
      break;
    case HW_CMD_GET_TX_POWER:
      handleGetTxPower();
      break;
    case HW_CMD_GET_VERSION:
      handleGetVersion();
      break;
    case HW_CMD_GET_CURRENT_RSSI:
      handleGetCurrentRssi();
      break;
    case HW_CMD_IS_CHANNEL_BUSY:
      handleIsChannelBusy();
      break;
    case HW_CMD_GET_AIRTIME:
      handleGetAirtime(data, len);
      break;
    case HW_CMD_GET_NOISE_FLOOR:
      handleGetNoiseFloor();
      break;
    case HW_CMD_GET_STATS:
      handleGetStats();
      break;
    case HW_CMD_GET_BATTERY:
      handleGetBattery();
      break;
    case HW_CMD_PING:
      handlePing();
      break;
    case HW_CMD_GET_SENSORS:
      handleGetSensors(data, len);
      break;
    case HW_CMD_GET_MCU_TEMP:
      handleGetMCUTemp();
      break;
    case HW_CMD_REBOOT:
      handleReboot();
      break;
    case HW_CMD_GET_DEVICE_NAME:
      handleGetDeviceName();
      break;
    case HW_CMD_SET_SIGNAL_REPORT:
      handleSetSignalReport(data, len);
      break;
    case HW_CMD_GET_SIGNAL_REPORT:
      handleGetSignalReport();
      break;
    case HW_CMD_SET_RADIO2:
      handleSetRadio2(data, len);
      break;
    case HW_CMD_GET_RADIO2:
      handleGetRadio2();
      break;
    case HW_CMD_SET_TEMPRADIO2:
      handleSetTempRadio2(data, len);
      break;
    case HW_CMD_GET_TEMPRADIO2:
      handleGetTempRadio2();
      break;
    default:
      writeHardwareError(HW_ERR_UNKNOWN_CMD);
      break;
  }
}

void KissModem::expireTemporaryRadio2() {
  if (!_temporary_radio2_active
      || (int32_t)(millis() - _temporary_radio2_end_ms) < 0) return;

  mesh::RadioProfiles* profiles = _radio.profiles();
  if (profiles) profiles->setSecondary(_saved_radio2.profile, false);
  _temporary_radio2_active = false;
  _temporary_radio2_end_ms = 0;
  _temporary_radio2 = {};
}

bool KissModem::pendingTxProfileUnchanged() const {
  const mesh::RadioProfiles* profiles = _radio.profiles();
  if (!profiles) return _pending_tx_profile == 0;
  return _pending_tx_profile <= 1
      && profiles->generation[_pending_tx_profile] == _pending_tx_profile_generation
      && profiles->canTransmit(_pending_tx_profile);
}

mesh::RadioParamApplyResult KissModem::preparePendingTransmitProfile() {
  if (!pendingTxProfileUnchanged()) return mesh::RadioParamApplyResult::FAILED;
  return _radio.prepareTransmitProfile(_pending_tx_profile);
}

uint32_t KissModem::pendingTxAirtime(uint16_t len) {
  return _radio.getProfileAirtime(_pending_tx_profile, len);
}

void KissModem::processTx() {
  switch (_tx_state) {
    case TX_IDLE:
      if (_has_pending_tx) {
        if (_fullduplex) {
          _tx_timer = millis();
          _tx_state = TX_DELAY;
        } else {
          _tx_timer = millis();
          _tx_state = TX_WAIT_CLEAR;
        }
      }
      break;

    case TX_WAIT_CLEAR:
      {
      const auto result = preparePendingTransmitProfile();
      if (result == mesh::RadioParamApplyResult::BUSY) break;
      if (result != mesh::RadioParamApplyResult::APPLIED) {
        setTxDonePending(0x00);
        break;
      }
      if (!_radio.isReceiving()) {
        uint8_t rand_val;
        _rng.random(&rand_val, 1);
        if (rand_val <= _persistence) {
          _tx_timer = millis();
          _tx_state = TX_DELAY;
        } else {
          _tx_timer = millis();
          _tx_state = TX_SLOT_WAIT;
        }
      } else if (millis() - _tx_timer >= pendingTxAirtime(KISS_MAX_PACKET_SIZE) * KISS_TX_TIMEOUT_FACTOR) {
        _tx_timer = millis();
        _tx_state = TX_DELAY;
      }
      }
      break;

    case TX_SLOT_WAIT:
      if (millis() - _tx_timer >= (uint32_t)_slottime * 10) {
        _tx_timer = millis();
        _tx_state = TX_WAIT_CLEAR;
      }
      break;

    case TX_DELAY:
      if (millis() - _tx_timer >= (uint32_t)_txdelay * 10) {
        // The scanner runs after this modem loop.  Retune at the last safe
        // instant so TXDELAY cannot make a queued port-1 packet leak out on
        // the primary profile.
        const auto result = preparePendingTransmitProfile();
        if (result == mesh::RadioParamApplyResult::BUSY) break;
        if (result != mesh::RadioParamApplyResult::APPLIED) {
          setTxDonePending(0x00);
          break;
        }
        if (!_fullduplex && _radio.isReceiving()) {
          _tx_timer = millis();
          _tx_state = TX_WAIT_CLEAR;
          break;
        }
        if (_radio.startSendRaw(_pending_tx, _pending_tx_len)) {
          _tx_timer = millis();
          _tx_state = TX_SENDING;
        } else {
          setTxDonePending(0x00);
        }
      }
      break;

    case TX_SENDING:
      if (_radio.isSendComplete()) {
        _radio.onSendFinished();
        setTxDonePending(0x01);
      } else if (millis() - _tx_timer >= pendingTxAirtime(_pending_tx_len) * KISS_TX_TIMEOUT_FACTOR) {
        _radio.onSendFinished();
        setTxDonePending(0x00);
      }
      break;

    case TX_DONE_PENDING:
      if (queuePendingTxDone()) {
        _has_pending_tx = false;
        _tx_state = TX_IDLE;
      }
      break;
  }
}

void KissModem::onPacketReceived(int8_t snr, int8_t rssi, const uint8_t* packet, uint16_t len, uint8_t profile) {
  if (profile > 1) return;
  const uint8_t data_type = (uint8_t)((profile << 4) | KISS_CMD_DATA);
  if (queueFrame(data_type, packet, len) && _signal_report_enabled) {
    uint8_t meta[2] = { (uint8_t)snr, (uint8_t)rssi };
    writeHardwareFrame(HW_RESP_RX_META, meta, 2);
  }
}

void KissModem::handleGetIdentity() {
  writeHardwareFrame(HW_RESP(HW_CMD_GET_IDENTITY), _identity.pub_key, PUB_KEY_SIZE);
}

void KissModem::handleGetRandom(const uint8_t* data, uint16_t len) {
  if (len < 1) {
    writeHardwareError(HW_ERR_INVALID_LENGTH);
    return;
  }

  uint8_t requested = data[0];
  if (requested < 1 || requested > 64) {
    writeHardwareError(HW_ERR_INVALID_PARAM);
    return;
  }

  uint8_t buf[64];
  _rng.random(buf, requested);
  writeHardwareFrame(HW_RESP(HW_CMD_GET_RANDOM), buf, requested);
}

void KissModem::handleVerifySignature(const uint8_t* data, uint16_t len) {
  if (len < PUB_KEY_SIZE + SIGNATURE_SIZE + 1) {
    writeHardwareError(HW_ERR_INVALID_LENGTH);
    return;
  }

  mesh::Identity signer(data);
  const uint8_t* signature = data + PUB_KEY_SIZE;
  const uint8_t* msg = data + PUB_KEY_SIZE + SIGNATURE_SIZE;
  uint16_t msg_len = len - PUB_KEY_SIZE - SIGNATURE_SIZE;

  uint8_t result = signer.verify(signature, msg, msg_len) ? 0x01 : 0x00;
  writeHardwareFrame(HW_RESP(HW_CMD_VERIFY_SIGNATURE), &result, 1);
}

void KissModem::handleSignData(const uint8_t* data, uint16_t len) {
  if (len < 1) {
    writeHardwareError(HW_ERR_INVALID_LENGTH);
    return;
  }

  uint8_t signature[SIGNATURE_SIZE];
  _identity.sign(signature, data, len);
  writeHardwareFrame(HW_RESP(HW_CMD_SIGN_DATA), signature, SIGNATURE_SIZE);
}

void KissModem::handleEncryptData(const uint8_t* data, uint16_t len) {
  if (len < PUB_KEY_SIZE + 1) {
    writeHardwareError(HW_ERR_INVALID_LENGTH);
    return;
  }

  const uint8_t* key = data;
  const uint8_t* plaintext = data + PUB_KEY_SIZE;
  uint16_t plaintext_len = len - PUB_KEY_SIZE;

  uint8_t buf[KISS_MAX_FRAME_SIZE];
  int encrypted_len = mesh::Utils::encryptThenMAC(key, buf, plaintext, plaintext_len);

  if (encrypted_len > 0) {
    writeHardwareFrame(HW_RESP(HW_CMD_ENCRYPT_DATA), buf, encrypted_len);
  } else {
    writeHardwareError(HW_ERR_ENCRYPT_FAILED);
  }
}

void KissModem::handleDecryptData(const uint8_t* data, uint16_t len) {
  if (len < PUB_KEY_SIZE + CIPHER_MAC_SIZE + 1) {
    writeHardwareError(HW_ERR_INVALID_LENGTH);
    return;
  }

  const uint8_t* key = data;
  const uint8_t* ciphertext = data + PUB_KEY_SIZE;
  uint16_t ciphertext_len = len - PUB_KEY_SIZE;

  uint8_t buf[KISS_MAX_FRAME_SIZE];
  int decrypted_len = mesh::Utils::MACThenDecrypt(key, buf, ciphertext, ciphertext_len);

  if (decrypted_len > 0) {
    writeHardwareFrame(HW_RESP(HW_CMD_DECRYPT_DATA), buf, decrypted_len);
  } else {
    writeHardwareError(HW_ERR_MAC_FAILED);
  }
}

void KissModem::handleKeyExchange(const uint8_t* data, uint16_t len) {
  if (len < PUB_KEY_SIZE) {
    writeHardwareError(HW_ERR_INVALID_LENGTH);
    return;
  }

  uint8_t shared_secret[PUB_KEY_SIZE];
  _identity.calcSharedSecret(shared_secret, data);
  writeHardwareFrame(HW_RESP(HW_CMD_KEY_EXCHANGE), shared_secret, PUB_KEY_SIZE);
}

void KissModem::handleHash(const uint8_t* data, uint16_t len) {
  if (len < 1) {
    writeHardwareError(HW_ERR_INVALID_LENGTH);
    return;
  }

  uint8_t hash[32];
  mesh::Utils::sha256(hash, 32, data, len);
  writeHardwareFrame(HW_RESP(HW_CMD_HASH), hash, 32);
}

bool KissModem::decodeRadio2Config(const uint8_t* data, Radio2Config& config) const {
  config = {};
  config.freq_hz = readU32LE(data);
  config.bw_hz = readU32LE(data + 4);
  config.profile.params.freq = (float)((double)config.freq_hz / 1000000.0);
  config.profile.params.bw = (float)((double)config.bw_hz / 1000.0);
  config.profile.params.sf = data[8];
  config.profile.params.cr = data[9];
  const uint8_t mode = data[10];
  if (mode > (uint8_t)mesh::RadioProfileMode::RxTx) return false;
  config.profile.mode = (mesh::RadioProfileMode)mode;
  config.profile.params.preamble = readU16LE(data + 11);
  return true;
}

void KissModem::encodeRadio2Config(const Radio2Config& config, uint8_t* data) const {
  writeU32LE(data, config.freq_hz);
  writeU32LE(data + 4, config.bw_hz);
  data[8] = config.profile.params.sf;
  data[9] = config.profile.params.cr;
  data[10] = (uint8_t)config.profile.mode;
  writeU16LE(data + 11, config.profile.params.preamble);
}

bool KissModem::validateRadio2Config(const Radio2Config& config) const {
  const mesh::RadioProfiles* profiles = _radio.profiles();
  if (!profiles) return false;
  if ((uint8_t)config.profile.mode > (uint8_t)mesh::RadioProfileMode::RxTx) return false;
  if (config.profile.mode == mesh::RadioProfileMode::Off) return true;
  if (!_radio.validateProfile(config.profile.params)) return false;
  mesh::RadioProfiles preview = *profiles;
  preview.setSecondary(config.profile, false);
  return preview.automaticPreambleFits();
}

void KissModem::syncPrimaryConfigFromRadio() {
  const mesh::RadioProfiles* profiles = _radio.profiles();
  if (!profiles) return;
  _config.freq_hz = frequencyToHz(profiles->primary.freq);
  _config.bw_hz = bandwidthToHz(profiles->primary.bw);
  _config.sf = profiles->primary.sf;
  _config.cr = profiles->primary.cr;
}

void KissModem::syncSavedRadio2FromRadio() {
  const mesh::RadioProfiles* profiles = _radio.profiles();
  if (!profiles) return;
  _saved_radio2.profile = profiles->secondary;
  _saved_radio2.freq_hz = frequencyToHz(profiles->secondary.params.freq);
  _saved_radio2.bw_hz = bandwidthToHz(profiles->secondary.params.bw);
}

void KissModem::handleSetRadio(const uint8_t* data, uint16_t len) {
  if (len < KISS_RADIO_PARAMS_SIZE) {
    writeHardwareError(HW_ERR_INVALID_LENGTH);
    return;
  }

  RadioConfig requested = _config;
  requested.freq_hz = readU32LE(data);
  requested.bw_hz = readU32LE(data + 4);
  requested.sf = data[8];
  requested.cr = data[9];

  mesh::RadioProfiles* profiles = _radio.profiles();
  if (profiles) {
    mesh::RadioProfileParams params = profiles->primary;
    params.freq = (float)((double)requested.freq_hz / 1000000.0);
    params.bw = (float)((double)requested.bw_hz / 1000.0);
    params.sf = requested.sf;
    params.cr = requested.cr;
    const auto result = _radio.trySetPrimaryParams(params, false);
    if (result == mesh::RadioParamApplyResult::BUSY) {
      writeHardwareError(HW_ERR_TX_BUSY);
      return;
    }
    if (result != mesh::RadioParamApplyResult::APPLIED) {
      writeHardwareError(HW_ERR_INVALID_PARAM);
      return;
    }
  } else {
    if (!_setRadioCallback) {
      writeHardwareError(HW_ERR_NO_CALLBACK);
      return;
    }
    _setRadioCallback((float)((double)requested.freq_hz / 1000000.0),
                      (float)((double)requested.bw_hz / 1000.0),
                      requested.sf, requested.cr);
  }

  _config = requested;
  writeHardwareFrame(HW_RESP_OK, nullptr, 0);
}

void KissModem::handleSetTxPower(const uint8_t* data, uint16_t len) {
  if (len < 1) {
    writeHardwareError(HW_ERR_INVALID_LENGTH);
    return;
  }
  if (!_setTxPowerCallback) {
    writeHardwareError(HW_ERR_NO_CALLBACK);
    return;
  }

  _config.tx_power = data[0];
  _setTxPowerCallback(data[0]);
  writeHardwareFrame(HW_RESP_OK, nullptr, 0);
}

void KissModem::handleGetRadio() {
  syncPrimaryConfigFromRadio();
  uint8_t buf[KISS_RADIO_PARAMS_SIZE];
  writeU32LE(buf, _config.freq_hz);
  writeU32LE(buf + 4, _config.bw_hz);
  buf[8] = _config.sf;
  buf[9] = _config.cr;
  writeHardwareFrame(HW_RESP(HW_CMD_GET_RADIO), buf, sizeof(buf));
}

void KissModem::handleSetRadio2(const uint8_t* data, uint16_t len) {
  if (len != KISS_RADIO2_PARAMS_SIZE) {
    writeHardwareError(HW_ERR_INVALID_LENGTH);
    return;
  }
  mesh::RadioProfiles* profiles = _radio.profiles();
  if (!profiles) {
    writeHardwareError(HW_ERR_NO_CALLBACK);
    return;
  }

  Radio2Config config;
  if (!decodeRadio2Config(data, config) || !validateRadio2Config(config)) {
    writeHardwareError(HW_ERR_INVALID_PARAM);
    return;
  }

  // Like the CLI, changing the saved secondary profile must not interrupt an
  // active temporary lease.  It becomes active when that lease ends.
  _saved_radio2 = config;
  if (!_temporary_radio2_active) profiles->setSecondary(_saved_radio2.profile, false);
  writeHardwareFrame(HW_RESP_OK, nullptr, 0);
}

void KissModem::handleGetRadio2() {
  if (!_radio.profiles()) {
    writeHardwareError(HW_ERR_NO_CALLBACK);
    return;
  }
  uint8_t buf[KISS_RADIO2_PARAMS_SIZE];
  encodeRadio2Config(_saved_radio2, buf);
  writeHardwareFrame(HW_RESP(HW_CMD_GET_RADIO2), buf, sizeof(buf));
}

void KissModem::handleSetTempRadio2(const uint8_t* data, uint16_t len) {
  if (len != KISS_TEMPRADIO2_PARAMS_SIZE) {
    writeHardwareError(HW_ERR_INVALID_LENGTH);
    return;
  }
  mesh::RadioProfiles* profiles = _radio.profiles();
  if (!profiles) {
    writeHardwareError(HW_ERR_NO_CALLBACK);
    return;
  }

  Radio2Config config;
  const uint16_t minutes = readU16LE(data + KISS_RADIO2_PARAMS_SIZE);
  if (!decodeRadio2Config(data, config)) {
    writeHardwareError(HW_ERR_INVALID_PARAM);
    return;
  }

  if (config.profile.mode == mesh::RadioProfileMode::Off && minutes == 0) {
    _temporary_radio2 = {};
    _temporary_radio2_active = false;
    _temporary_radio2_end_ms = 0;
    profiles->setSecondary(_saved_radio2.profile, false);
    writeHardwareFrame(HW_RESP_OK, nullptr, 0);
    return;
  }
  if (config.profile.mode == mesh::RadioProfileMode::Off || minutes == 0
      || minutes > KISS_MAX_TEMPRADIO2_MINUTES || !validateRadio2Config(config)) {
    writeHardwareError(HW_ERR_INVALID_PARAM);
    return;
  }

  _temporary_radio2 = config;
  _temporary_radio2_active = true;
  _temporary_radio2_end_ms = millis() + (uint32_t)minutes * 60000UL;
  profiles->setSecondary(_temporary_radio2.profile, true);
  writeHardwareFrame(HW_RESP_OK, nullptr, 0);
}

void KissModem::handleGetTempRadio2() {
  if (!_radio.profiles()) {
    writeHardwareError(HW_ERR_NO_CALLBACK);
    return;
  }
  uint8_t buf[KISS_TEMPRADIO2_PARAMS_SIZE] = {};
  if (_temporary_radio2_active) {
    encodeRadio2Config(_temporary_radio2, buf);
    const uint32_t now = millis();
    const uint32_t remaining_ms = (int32_t)(_temporary_radio2_end_ms - now) > 0
        ? _temporary_radio2_end_ms - now : 0;
    const uint32_t remaining_minutes = (remaining_ms + 59999UL) / 60000UL;
    writeU16LE(buf + KISS_RADIO2_PARAMS_SIZE, (uint16_t)remaining_minutes);
  }
  writeHardwareFrame(HW_RESP(HW_CMD_GET_TEMPRADIO2), buf, sizeof(buf));
}

void KissModem::handleGetTxPower() {
  writeHardwareFrame(HW_RESP(HW_CMD_GET_TX_POWER), &_config.tx_power, 1);
}

void KissModem::handleGetVersion() {
  uint8_t buf[2];
  buf[0] = KISS_FIRMWARE_VERSION;
  buf[1] = 0;
  writeHardwareFrame(HW_RESP(HW_CMD_GET_VERSION), buf, 2);
}

void KissModem::handleGetCurrentRssi() {
  if (!_getCurrentRssiCallback) {
    writeHardwareError(HW_ERR_NO_CALLBACK);
    return;
  }

  float rssi = _getCurrentRssiCallback();
  int8_t rssi_byte = (int8_t)rssi;
  writeHardwareFrame(HW_RESP(HW_CMD_GET_CURRENT_RSSI), (uint8_t*)&rssi_byte, 1);
}

void KissModem::handleIsChannelBusy() {
  uint8_t busy = _radio.isReceiving() ? 0x01 : 0x00;
  writeHardwareFrame(HW_RESP(HW_CMD_IS_CHANNEL_BUSY), &busy, 1);
}

void KissModem::handleGetAirtime(const uint8_t* data, uint16_t len) {
  if (len < 1) {
    writeHardwareError(HW_ERR_INVALID_LENGTH);
    return;
  }

  uint8_t packet_len = data[0];
  uint32_t airtime = _radio.getEstAirtimeFor(packet_len);
  writeHardwareFrame(HW_RESP(HW_CMD_GET_AIRTIME), (uint8_t*)&airtime, 4);
}

void KissModem::handleGetNoiseFloor() {
  int16_t noise_floor = _radio.getNoiseFloor();
  writeHardwareFrame(HW_RESP(HW_CMD_GET_NOISE_FLOOR), (uint8_t*)&noise_floor, 2);
}

void KissModem::handleGetStats() {
  if (!_getStatsCallback) {
    writeHardwareError(HW_ERR_NO_CALLBACK);
    return;
  }

  uint32_t rx, tx, errors;
  _getStatsCallback(&rx, &tx, &errors);
  uint8_t buf[12];
  memcpy(buf, &rx, 4);
  memcpy(buf + 4, &tx, 4);
  memcpy(buf + 8, &errors, 4);
  writeHardwareFrame(HW_RESP(HW_CMD_GET_STATS), buf, 12);
}

void KissModem::handleGetBattery() {
  uint16_t mv = _board.getBattMilliVolts();
  writeHardwareFrame(HW_RESP(HW_CMD_GET_BATTERY), (uint8_t*)&mv, 2);
}

void KissModem::handlePing() {
  writeHardwareFrame(HW_RESP(HW_CMD_PING), nullptr, 0);
}

void KissModem::handleGetSensors(const uint8_t* data, uint16_t len) {
  if (len < 1) {
    writeHardwareError(HW_ERR_INVALID_LENGTH);
    return;
  }

  uint8_t permissions = data[0];
  CayenneLPP telemetry(255);
  if (_sensors.querySensors(permissions, telemetry)) {
    writeHardwareFrame(HW_RESP(HW_CMD_GET_SENSORS), telemetry.getBuffer(), telemetry.getSize());
  } else {
    writeHardwareFrame(HW_RESP(HW_CMD_GET_SENSORS), nullptr, 0);
  }
}

void KissModem::handleGetMCUTemp() {
  float temp = _board.getMCUTemperature();
  if (isnan(temp)) {
    writeHardwareError(HW_ERR_NO_CALLBACK);
    return;
  }
  int16_t temp_tenths = (int16_t)(temp * 10.0f);
  writeHardwareFrame(HW_RESP(HW_CMD_GET_MCU_TEMP), (uint8_t*)&temp_tenths, 2);
}

void KissModem::handleReboot() {
  writeHardwareFrame(HW_RESP_OK, nullptr, 0);
  _serial.flush();
  delay(50);
  _board.reboot();
}

void KissModem::handleGetDeviceName() {
  const char* name = _board.getManufacturerName();
  writeHardwareFrame(HW_RESP(HW_CMD_GET_DEVICE_NAME), (const uint8_t*)name, strlen(name));
}

void KissModem::handleSetSignalReport(const uint8_t* data, uint16_t len) {
  if (len < 1) {
    writeHardwareError(HW_ERR_INVALID_LENGTH);
    return;
  }
  _signal_report_enabled = (data[0] != 0x00);
  uint8_t val = _signal_report_enabled ? 0x01 : 0x00;
  writeHardwareFrame(HW_RESP(HW_CMD_GET_SIGNAL_REPORT), &val, 1);
}

void KissModem::handleGetSignalReport() {
  uint8_t val = _signal_report_enabled ? 0x01 : 0x00;
  writeHardwareFrame(HW_RESP(HW_CMD_GET_SIGNAL_REPORT), &val, 1);
}
