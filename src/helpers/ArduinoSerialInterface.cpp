#include "ArduinoSerialInterface.h"
#include "CompanionFrameQueue.h"

#define RECV_STATE_IDLE        0
#define RECV_STATE_HDR_FOUND   1
#define RECV_STATE_LEN1_FOUND  2
#define RECV_STATE_LEN2_FOUND  3

void ArduinoSerialInterface::resetControlSequenceState() {
  _controlSequenceCursor =
      _controlSequence != nullptr && _controlSequence[0] != 0
          ? _controlSequence : nullptr;
  _secondaryControlSequenceCursor =
      _secondaryControlSequence != nullptr && _secondaryControlSequence[0] != 0
          ? _secondaryControlSequence : nullptr;
}

void ArduinoSerialInterface::resetReceiveState() {
  _state = RECV_STATE_IDLE;
  resetControlSequenceState();
  _frame_len = 0;
  rx_len = 0;
  _last_rx_byte_ms = 0;
  _receive_capacity_paused = false;
  _receive_capacity_paused_at = 0;
}

void ArduinoSerialInterface::serviceReceiveTimeout() {
  updateReceiveCapacityPause(millis());
  if (_state != RECV_STATE_IDLE && _serial != nullptr
      && !_receive_capacity_paused
      && _serial->available() == 0
      && (uint32_t)(millis() - _last_rx_byte_ms) >= RX_FRAME_TIMEOUT_MS) {
    // A truncated length-prefixed frame must not hold the MCU awake forever or
    // turn bytes from a later session into the missing tail of the old frame.
    resetReceiveState();
  }
}

bool ArduinoSerialInterface::responseCapacityAvailable() const {
  if (!_flow_ctl || _tx_queue_len < TX_QUEUE_SIZE) return true;
  // Match enqueueFrame(): a required reply may replace an unsent best-effort
  // push, but the prefix of a partly sent frame must remain intact.
  for (uint8_t i = _tx_offset == 0 ? 0 : 1; i < _tx_queue_len; ++i) {
    const TxFrame& frame = _tx_queue[i];
    if (!mesh::companionFrameRequiresDelivery(&frame.buf[3], frame.len - 3)) {
      return true;
    }
  }
  return false;
}

void ArduinoSerialInterface::updateReceiveCapacityPause(uint32_t now) {
  if (_state == RECV_STATE_IDLE) {
    _receive_capacity_paused = false;
    _receive_capacity_paused_at = 0;
    return;
  }
  const bool pause = !responseCapacityAvailable();
  if (pause && !_receive_capacity_paused) {
    // Backpressure must not retroactively extend an already expired frame.
    if ((uint32_t)(now - _last_rx_byte_ms) >= RX_FRAME_TIMEOUT_MS) return;
    _receive_capacity_paused = true;
    _receive_capacity_paused_at = now;
  } else if (!pause && _receive_capacity_paused) {
    // Bytes retained for reply capacity keep the framing budget they had when
    // RX was paused, even if the host takes a long time to resume reading.
    _last_rx_byte_ms += (uint32_t)(now - _receive_capacity_paused_at);
    _receive_capacity_paused = false;
    _receive_capacity_paused_at = 0;
  }
}

void ArduinoSerialInterface::resetTransmitState() {
  _tx_queue_len = 0;
  _tx_offset = 0;
}

bool ArduinoSerialInterface::enqueueFrame(const uint8_t src[], size_t len) {
  if (src == nullptr || len == 0 || len > MAX_FRAME_SIZE) return false;

  // MSG_WAITING is level-triggered. Retaining more than one copy only takes
  // space away from command replies while a USB host is backpressuring us.
  if (src[0] == 0x83) {
    for (uint8_t i = 0; i < _tx_queue_len; ++i) {
      if (_tx_queue[i].len > 3 && _tx_queue[i].buf[3] == 0x83) return true;
    }
  }

  const bool delivery_required = mesh::companionFrameRequiresDelivery(src, len);
  if (!delivery_required && _tx_queue_len >= TX_QUEUE_SIZE - 1) {
    return false;  // reserve one slot for a response or required push
  }

  if (_tx_queue_len == TX_QUEUE_SIZE) {
    if (!delivery_required) return false;

    // A required frame may replace queued best-effort traffic. Never replace
    // the head after any of it has reached the host: doing so would splice two
    // frames together on the byte stream.
    const uint8_t first_evictable = _tx_offset == 0 ? 0 : 1;
    int evict = -1;
    for (int i = TX_QUEUE_SIZE - 1; i >= first_evictable; --i) {
      const TxFrame& queued = _tx_queue[i];
      if (!mesh::companionFrameRequiresDelivery(&queued.buf[3], queued.len - 3)) {
        evict = i;
        break;
      }
    }
    if (evict < 0) return false;
    for (uint8_t i = (uint8_t)evict; i + 1 < _tx_queue_len; ++i) {
      _tx_queue[i] = _tx_queue[i + 1];
    }
    --_tx_queue_len;
  }

  TxFrame& frame = _tx_queue[_tx_queue_len++];
  frame.len = (uint16_t)(len + 3);
  frame.buf[0] = '>';
  frame.buf[1] = (uint8_t)(len & 0xFF);
  frame.buf[2] = (uint8_t)(len >> 8);
  memcpy(&frame.buf[3], src, len);
  updateReceiveCapacityPause(millis());
  return true;
}

void ArduinoSerialInterface::serviceTransmit() {
  if (!_flow_ctl || _passthroughMode || _serial == nullptr) return;
  if (!isConnected()) {
    // A disconnected USB endpoint cannot make progress. Its host also loses
    // any partial endpoint data on disconnect, so begin the next session clean.
    resetTransmitState();
    return;
  }

  while (_tx_queue_len > 0) {
    TxFrame& frame = _tx_queue[0];
    const size_t remaining = frame.len - _tx_offset;
    int available = _serial->availableForWrite();
    if (available <= 0) return;

    // A UART or CDC FIFO can be smaller than MAX_FRAME_SIZE. Drain the frame in
    // bounded chunks, retaining the offset and never allowing another frame to
    // interleave until this header and body are complete.
    const size_t attempt = (size_t)available < remaining
        ? (size_t)available : remaining;
    size_t written = _serial->write(&frame.buf[_tx_offset], attempt);
    if (written > attempt) written = attempt;
    _tx_offset += (uint16_t)written;
    if (_tx_offset < frame.len) return;

    for (uint8_t i = 0; i + 1 < _tx_queue_len; ++i) {
      _tx_queue[i] = _tx_queue[i + 1];
    }
    --_tx_queue_len;
    _tx_offset = 0;
    if (written < attempt) return;
  }
}

bool ArduinoSerialInterface::checkControlLineByte(uint8_t c) {
  if (c == '\r' || c == '\n') {
    const bool primary = _controlSequenceCursor != nullptr
        && *_controlSequenceCursor == 0;
    const bool secondary = _secondaryControlSequenceCursor != nullptr
        && *_secondaryControlSequenceCursor == 0;
    resetControlSequenceState();
    if (primary) _controlSequenceReceived = true;
    if (secondary) _secondaryControlSequenceReceived = true;
    return primary || secondary;
  }

  if (_controlSequenceCursor != nullptr) {
    if (*_controlSequenceCursor != 0
        && c == (uint8_t)*_controlSequenceCursor) {
      ++_controlSequenceCursor;
    } else {
      _controlSequenceCursor = nullptr;
    }
  }
  if (_secondaryControlSequenceCursor != nullptr) {
    if (*_secondaryControlSequenceCursor != 0
        && c == (uint8_t)*_secondaryControlSequenceCursor) {
      ++_secondaryControlSequenceCursor;
    } else {
      _secondaryControlSequenceCursor = nullptr;
    }
  }
  return false;
}

#if defined(STM32_PLATFORM) && defined(__GNUC__) && !defined(__clang__)
// Keep repeated host-ownership resets shared on flash-constrained STM32.
__attribute__((noinline, noclone))
#endif
void ArduinoSerialInterface::setPassthroughMode(bool enabled) {
  _passthroughMode = enabled;
  _controlSequenceReceived = false;
  _secondaryControlSequenceReceived = false;
  resetReceiveState();
  resetTransmitState();
}

#if defined(STM32_PLATFORM) && defined(__GNUC__) && !defined(__clang__)
__attribute__((noinline, noclone))
#endif
void ArduinoSerialInterface::resetSessionState() {
  _controlSequenceReceived = false;
  _secondaryControlSequenceReceived = false;
  _has_received_frame = false;
  _last_frame_ms = 0;
  resetReceiveState();
  resetTransmitState();
}

bool ArduinoSerialInterface::takeControlSequence() {
  bool received = _controlSequenceReceived;
  _controlSequenceReceived = false;
  return received;
}

bool ArduinoSerialInterface::takeSecondaryControlSequence() {
  bool received = _secondaryControlSequenceReceived;
  _secondaryControlSequenceReceived = false;
  return received;
}

void ArduinoSerialInterface::enable() {
  _isEnabled = true;
  resetSessionState();
}
void ArduinoSerialInterface::disable() {
  _isEnabled = false;
  resetSessionState();
}

bool ArduinoSerialInterface::isConnected() const {
  if (_serial == nullptr) return false;
  if (_conn_check) return _conn_check();
  return true;   // no way of knowing, so assume yes
}

void ArduinoSerialInterface::loop() {
  serviceReceiveTimeout();
  serviceTransmit();
  updateReceiveCapacityPause(millis());
}

bool ArduinoSerialInterface::isReadBusy() const {
  return _state != RECV_STATE_IDLE;
}

bool ArduinoSerialInterface::isWriteBusy() const {
  if (_passthroughMode || _serial == nullptr) return false;
  if (_flow_ctl && isConnected()) {
    if (_tx_queue_len > 0) return true;
    return const_cast<Stream*>(_serial)->availableForWrite() <= 0;
  }
  // while nobody drains the port the TX buffer stays full, so never report
  // busy in that case: it would stall the paced streams on all interfaces
  return false;
}

bool ArduinoSerialInterface::hasPendingIO() const {
  return isReadBusy() || _tx_queue_len > 0;
}

size_t ArduinoSerialInterface::writeFrame(const uint8_t src[], size_t len) {
  if (src == nullptr || len == 0 || len > MAX_FRAME_SIZE || _serial == nullptr) {
    // invalid frame or an interface which has not begun yet
    return 0;
  }
  if (_passthroughMode) return len;
  if (_flow_ctl) {
    if (!isConnected()) {
      // Best-effort pushes may be discarded while nobody is listening. A
      // command response or required push must report failure so a multi-frame
      // producer can retry it without silently skipping an item.
      return mesh::companionFrameRequiresDelivery(src, len) ? 0 : len;
    }
    if (!enqueueFrame(src, len)) return 0;
    serviceTransmit();
    updateReceiveCapacityPause(millis());
    return len;
  }

  uint8_t frame[MAX_FRAME_SIZE + 3];
  frame[0] = '>';
  frame[1] = (uint8_t)(len & 0xFF);  // LSB
  frame[2] = (uint8_t)(len >> 8);    // MSB
  memcpy(&frame[3], src, len);
  return _serial->write(frame, len + 3) == len + 3 ? len : 0;
}

size_t ArduinoSerialInterface::checkRecvFrame(uint8_t dest[]) {
  if (_serial == nullptr || dest == nullptr) return 0;
  serviceReceiveTimeout();
  serviceTransmit();
  updateReceiveCapacityPause(millis());
  if (_passthroughMode) return 0;
  // Immediate command handlers do not retain rejected replies. Leave the
  // complete input and any partial parser state untouched until one required
  // response can be admitted, rather than executing a command without a reply.
  if (!responseCapacityAvailable()) return 0;

  // Newly arriving bytes belong to the next mesh loop; a continuously writing
  // host must not keep the radio/watchdog loop trapped in this parser.
  int pending = _serial->available();
  while (pending-- > 0) {
    int c = _serial->read();
    if (c < 0) break;
    _last_rx_byte_ms = millis();

    switch (_state) {
      case RECV_STATE_IDLE:
        if (checkControlLineByte((uint8_t)c)) {
          // Leave any following bytes buffered for the passthrough consumer.
          return 0;
        }
        if (c == '<') {
          _state = RECV_STATE_HDR_FOUND;
        }
        break;
      case RECV_STATE_HDR_FOUND:
        _frame_len = (uint8_t)c;   // LSB
        _state = RECV_STATE_LEN1_FOUND;
        break;
      case RECV_STATE_LEN1_FOUND:
        _frame_len |= ((uint16_t)c) << 8;   // MSB
        rx_len = 0;
        _state = _frame_len > 0 ? RECV_STATE_LEN2_FOUND : RECV_STATE_IDLE;
        if (_state == RECV_STATE_IDLE) resetControlSequenceState();
        break;
      default:
        if (rx_len < MAX_FRAME_SIZE) {
          rx_buf[rx_len] = (uint8_t)c;   // rest of frame will be discarded if > MAX
        }
        rx_len++;
        if (rx_len >= _frame_len) {  // received a complete frame?
          if (_frame_len > MAX_FRAME_SIZE) {
            // Consume the entire declared body to preserve framing, but never
            // execute a truncated command or grant Binary-client proof for an
            // invalid frame. A following valid frame may still be dispatched.
            resetReceiveState();
            break;
          }
          // The final read already captured its completion time above.
          const uint32_t completed_at = _last_rx_byte_ms;
          if (_receive_frame_check != nullptr
              && !_receive_frame_check(completed_at)) {
            resetReceiveState();
            return 0;
          }
          memcpy(dest, rx_buf, _frame_len);
          _state = RECV_STATE_IDLE;  // reset state, for next frame
          resetControlSequenceState();
          _last_frame_ms = completed_at;   // a real client is talking to us
          ++_completed_frame_count;
          _has_received_frame = true;
          return _frame_len;
        }
    }
  }
  return 0;
}
