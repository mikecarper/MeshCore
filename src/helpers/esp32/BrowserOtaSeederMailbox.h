#pragma once

#include <stddef.h>
#include <stdint.h>
#include <string.h>

// Read-only MotaSource mailbox with request correlation for an HTTP bridge.
// The wire protocol has no sequence number; the browser-facing API MUST carry
// the sequence returned by request() in each response(). Every method requires
// the same external lock. Authentication and leases are the owner's job.
namespace mesh { namespace ota {

class BrowserOtaSeederMailbox {
public:
  static constexpr size_t kMaxRequest = 11;
  static constexpr size_t kMaxResponse = 197;

  bool submit(const uint8_t* frame, size_t length) {
    uint8_t op = 0;
    size_t payload = 0;
    if (!validateRequest(frame, length, op, payload)) return false;
    // A fresh source request supersedes a timed-out transaction. Even if its
    // operation is the same, an old HTTP reply cannot satisfy the new one.
    if (++_sequence == 0) ++_sequence;
    memcpy(_request, frame, length);
    _request_length = length;
    _operation = op;
    _payload_length = payload;
    _response_length = _response_read = 0;
    _pending = true;
    return true;
  }

  // Idempotent: retries may observe the same request until one response is
  // accepted. Callers also bind the API session to a connection epoch.
  bool request(uint32_t& sequence, uint8_t* output, size_t capacity,
               size_t& length) const {
    length = 0;
    if (!_pending || !output || capacity < _request_length) return false;
    sequence = _sequence;
    length = _request_length;
    memcpy(output, _request, length);
    return true;
  }

  bool response(uint32_t sequence, const uint8_t* frame, size_t length) {
    if (!_pending || sequence != _sequence || !frame || length < 5
        || frame[0] != 'm' || frame[1] != 's' || frame[2] != _operation
        || frame[3] > 1) return false;
    const size_t payload = frame[3] == 0 ? _payload_length : 0;
    if (length != payload + 5 || length > kMaxResponse) return false;
    uint8_t check = 0;
    for (size_t i = 0; i < length - 1; ++i) check ^= frame[i];
    if (check != frame[length - 1]) return false;
    memcpy(_response, frame, length);
    _response_length = length;
    _response_read = 0;
    _pending = false;
    return true;
  }

  size_t available() const { return _response_length - _response_read; }
  int peek() const { return available() ? _response[_response_read] : -1; }
  int read() { return available() ? _response[_response_read++] : -1; }
  void reset() {
    _request_length = _response_length = _response_read = _payload_length = 0;
    _operation = 0;
    _pending = false;
    // Invalidate requests held by a browser across disconnect/reconnect.
    if (++_sequence == 0) ++_sequence;
  }

private:
  static bool validateRequest(const uint8_t* frame, size_t length,
                              uint8_t& op, size_t& payload) {
    if (!frame || length < 4 || length > kMaxRequest ||
        frame[0] != 'M' || frame[1] != 'S') return false;
    op = frame[2];
    const size_t args = op == 1 ? 0 : op == 2 ? 1 :
        (op == 3 || op == 9) ? 7 : kMaxRequest;
    if (length != args + 4) return false;
    uint8_t check = 0;
    for (size_t i = 2; i < length - 1; ++i) check ^= frame[i];
    if (check != frame[length - 1]) return false;
    if (op == 1) payload = 1;
    else if (op == 2) payload = 38;
    else {
      const uint16_t requested = static_cast<uint16_t>(frame[8]) |
          (static_cast<uint16_t>(frame[9]) << 8);
      if (op == 3 && (!requested || requested > 192)) return false;
      if (op == 9 && requested > 190) return false;
      if (op == 9 && !requested && (frame[6] || frame[7])) return false;
      payload = requested + (op == 9 ? 2 : 0);
    }
    return true;
  }

  uint8_t _request[kMaxRequest] = {}, _response[kMaxResponse] = {};
  uint32_t _sequence = 0;
  size_t _request_length = 0, _response_length = 0, _response_read = 0;
  size_t _payload_length = 0;
  uint8_t _operation = 0;
  bool _pending = false;
};

}} // namespace mesh::ota
