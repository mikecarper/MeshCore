#include "ArduinoSerialInterface.h"

#define RECV_STATE_IDLE        0
#define RECV_STATE_HDR_FOUND   1
#define RECV_STATE_LEN1_FOUND  2
#define RECV_STATE_LEN2_FOUND  3

void ArduinoSerialInterface::resetReceiveState() {
  _state = RECV_STATE_IDLE;
  _controlSequencePos = 0;
  _frame_len = 0;
  rx_len = 0;
}

bool ArduinoSerialInterface::checkControlSequence(uint8_t c) {
  if (_controlSequence == nullptr || _controlSequence[0] == 0) return false;

  if (c == (uint8_t)_controlSequence[_controlSequencePos]) {
    _controlSequencePos++;
    if (_controlSequence[_controlSequencePos] == 0) {
      _controlSequencePos = 0;
      _controlSequenceReceived = true;
      return true;
    }
  } else {
    // Preserve a possible new match when this byte is also the first byte of
    // the sequence (notably useful for sequences beginning with "+++").
    _controlSequencePos = c == (uint8_t)_controlSequence[0] ? 1 : 0;
  }
  return false;
}

void ArduinoSerialInterface::setPassthroughMode(bool enabled) {
  _passthroughMode = enabled;
  _controlSequenceReceived = false;
  resetReceiveState();
}

bool ArduinoSerialInterface::takeControlSequence() {
  bool received = _controlSequenceReceived;
  _controlSequenceReceived = false;
  return received;
}

void ArduinoSerialInterface::enable() {
  _isEnabled = true;
  _controlSequenceReceived = false;
  resetReceiveState();
}
void ArduinoSerialInterface::disable() {
  _isEnabled = false;
  _controlSequenceReceived = false;
  resetReceiveState();
}

bool ArduinoSerialInterface::isConnected() const { 
  return true;   // no way of knowing, so assume yes
}

bool ArduinoSerialInterface::isReadBusy() const {
  return false;
}

bool ArduinoSerialInterface::isWriteBusy() const {
  return false;
}

size_t ArduinoSerialInterface::writeFrame(const uint8_t src[], size_t len) {
  if (len > MAX_FRAME_SIZE) {
    // frame is too big!
    return 0;
  }
  if (_passthroughMode) return len;

  uint8_t hdr[3];
  hdr[0] = '>';
  hdr[1] = (len & 0xFF);  // LSB
  hdr[2] = (len >> 8);    // MSB

  _serial->write(hdr, 3);
  return _serial->write(src, len);
}

size_t ArduinoSerialInterface::checkRecvFrame(uint8_t dest[]) {
  if (_passthroughMode) return 0;

  while (_serial->available()) {
    int c = _serial->read();
    if (c < 0) break;

    switch (_state) {
      case RECV_STATE_IDLE:
        if (checkControlSequence((uint8_t)c)) {
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
        break;
      default:
        if (rx_len < MAX_FRAME_SIZE) {
          rx_buf[rx_len] = (uint8_t)c;   // rest of frame will be discarded if > MAX
        }
        rx_len++;
        if (rx_len >= _frame_len) {  // received a complete frame?
          if (_frame_len > MAX_FRAME_SIZE) _frame_len = MAX_FRAME_SIZE;    // truncate
          memcpy(dest, rx_buf, _frame_len);
          _state = RECV_STATE_IDLE;  // reset state, for next frame
          return _frame_len;
        }
    }
  }
  return 0;
}
