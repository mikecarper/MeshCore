#include "SerialEthernetInterface.h"
#include "../CompanionFrameQueue.h"

#define RECV_STATE_IDLE        0
#define RECV_STATE_HDR_FOUND   1
#define RECV_STATE_LEN1_FOUND  2
#define RECV_STATE_LEN2_FOUND  3

#if !ETHERNET_RAW_LINE
static constexpr uint32_t ETHERNET_FRAME_TIMEOUT_MS = 5000;
#endif

bool SerialEthernetInterface::begin() {
  return true;
}

void SerialEthernetInterface::enable() {
  if (_isEnabled) return;
  _isEnabled = true;
  clearBuffers();
}

void SerialEthernetInterface::disable() {
  _isEnabled = false;
  disconnectClient();
  onClientDisconnected();
  clearBuffers();
}

bool SerialEthernetInterface::isConnected() const {
  return _isEnabled && session_active;
}

size_t SerialEthernetInterface::writeFrame(const uint8_t src[], size_t len) {
  if (len > MAX_FRAME_SIZE) {
    ETHERNET_DEBUG_PRINTLN("writeFrame(), frame too big, len=%d\n", len);
    return 0;
  }

  if (_isEnabled && isConnected() && src != nullptr && len > 0) {
    // A partly written frame must remain at the head even if a new response
    // displaces or reorders lower-priority frames in the waiting tail.
    const int pinned = send_offset != 0 ? 1 : 0;
    int tail_len = send_queue_len - pinned;
    if (!mesh::enqueueCompanionFrame(send_queue + pinned, tail_len, FRAME_QUEUE_SIZE - pinned,
                                     src, len)) {
      ETHERNET_DEBUG_PRINTLN("writeFrame(), send_queue is full!");
      return 0;
    }
    send_queue_len = tail_len + pinned;
    return len;
  }
  return 0;
}

bool SerialEthernetInterface::isWriteBusy() const {
  return send_queue_len >= (FRAME_QUEUE_SIZE * 2 / 3);
}

bool SerialEthernetInterface::isReadBusy() const {
  return _state != RECV_STATE_IDLE || _rx_len > 0;
}

bool SerialEthernetInterface::hasPendingIO() const {
  // Ethernet is polled, so an active connection must keep receiving loop
  // service even when no complete frame is queued yet.
  return _isEnabled && isConnected();
}

void SerialEthernetInterface::onClientConnected(uint32_t remote_ip) {
  const bool different_owner = queue_has_ip && queue_ip != remote_ip;
  if (different_owner) {
    // Cancel response producers while the displaced route is unavailable.
    // Ethernet has only its original four-frame queue: unlike WiFi there is
    // no spare queue for a different IP's backlog, so discard it on takeover.
    onClientDisconnected();
    send_queue_len = 0;
  }
  resetInput();
  send_offset = 0; // Replay a complete frame on a fresh TCP stream.
  queue_ip = remote_ip;
  queue_has_ip = true;
  session_active = true;
}

void SerialEthernetInterface::onClientDisconnected() {
  const bool was_active = session_active;
  session_active = false;
  resetInput();
  send_offset = 0;
  if (was_active && session_changed != nullptr) session_changed(session_context);
}

void SerialEthernetInterface::rejectInput() {
  disconnectClient();
  onClientDisconnected();
}

size_t SerialEthernetInterface::checkRecvFrame(uint8_t dest[]) {
  if (!_isEnabled) return 0;
  if (isConnected()) {
#if !ETHERNET_RAW_LINE
    if (isReadBusy() && (uint32_t)((uint32_t)millis() - _rx_started) >= ETHERNET_FRAME_TIMEOUT_MS) {
      rejectInput();
      return 0;
    }
#endif
    if (send_queue_len > 0) {   // first, check send queue

      _last_write = millis();
      int len = send_queue[0].len;

      uint8_t pkt[MAX_FRAME_SIZE + 3];
#if ETHERNET_RAW_LINE
      ETHERNET_DEBUG_PRINTLN("TX line len=%d", len);
      memcpy(pkt, send_queue[0].buf, len);
      pkt[len] = '\r';
      pkt[len + 1] = '\n';
      const size_t total = len + 2U;
#else
      pkt[0] = '>';
      pkt[1] = (len & 0xFF);  // LSB
      pkt[2] = (len >> 8);    // MSB
      memcpy(&pkt[3], send_queue[0].buf, send_queue[0].len);
      ETHERNET_DEBUG_PRINTLN("Sending frame len=%d", len);
      #if ETHERNET_DEBUG_LOGGING && ARDUINO
      ETHERNET_DEBUG_PRINTLN("TX frame len=%d", len);
      #endif
      const size_t total = len + 3U;
#endif
      const size_t remaining = total - send_offset;
      const size_t written = write(pkt + send_offset, remaining);
      send_offset += written < remaining ? written : remaining;
      if (send_offset < total) return 0;
      send_offset = 0;
      send_queue_len--;
      for (int i = 0; i < send_queue_len; i++) {   // delete top item from queue
        send_queue[i] = send_queue[i + 1];
      }
    } else {
      while (available()) {
        int c = read();
        if (c < 0) break;

#if ETHERNET_RAW_LINE
        if (c == '\r' || c == '\n') {
          if (_rx_len == 0) {
            continue;
          }
          uint16_t out_len = _rx_len;
          if (dest == nullptr) { rejectInput(); return 0; }
          memcpy(dest, _rx_buf, out_len);
          resetInput();
          return out_len;
        }
        if (_rx_len < MAX_FRAME_SIZE) {
          if (_rx_len == 0) _rx_started = (uint32_t)millis();
          _rx_buf[_rx_len] = (uint8_t)c;
          _rx_len++;
        } else {
          rejectInput(); // Never execute the prefix of an overlong command.
          return 0;
        }
#else
        switch (_state) {
          case RECV_STATE_IDLE:
            if (c == '<') {
              _state = RECV_STATE_HDR_FOUND;
              _rx_started = (uint32_t)millis();
            }
            break;
          case RECV_STATE_HDR_FOUND:
            _frame_len = (uint8_t)c;
            _state = RECV_STATE_LEN1_FOUND;
            break;
          case RECV_STATE_LEN1_FOUND:
            _frame_len |= ((uint16_t)c) << 8;
            if (_frame_len == 0 || _frame_len > MAX_FRAME_SIZE) {
              rejectInput();
              return 0;
            }
            _rx_len = 0;
            _state = RECV_STATE_LEN2_FOUND;
            break;
          default:
            if (_rx_len < MAX_FRAME_SIZE) {
              _rx_buf[_rx_len] = (uint8_t)c;
            }
            _rx_len++;
            if (_rx_len >= _frame_len) {
              #if ETHERNET_DEBUG_LOGGING && ARDUINO
              ETHERNET_DEBUG_PRINTLN("RX frame len=%d", _frame_len);
              #endif
              if (dest == nullptr) { rejectInput(); return 0; }
              const size_t out_len = _frame_len;
              memcpy(dest, _rx_buf, out_len);
              resetInput();
              return out_len;
            }
        }
#endif
      }
    }
  }

  return 0;
}
