#pragma once

#include <stddef.h>
#include <stdint.h>
#include <string.h>

namespace mesh { namespace ethernet {

static constexpr size_t MinimumFrameSize = 14;
static constexpr size_t MaxFrameSize = 255;
static constexpr size_t QueueDepth = 2;
static constexpr size_t MaxEncodedSize = 2 * MaxFrameSize + 3;
static constexpr uint8_t Fend = 0xc0;
static constexpr uint8_t Fesc = 0xdb;
static constexpr uint8_t Tfend = 0xdc;
static constexpr uint8_t Tfesc = 0xdd;

// Only standard port-0 DATA frames are accepted. Ethernet bytes are not
// interpreted, wrapped, padded or truncated here: ARP and all IP fragments
// must traverse the same link as the TCP connection they support.
class KissCodec {
  struct Frame {
    uint16_t length = 0;
    uint8_t bytes[MaxFrameSize];
  };
  struct EncodedFrame {
    uint16_t length = 0;
    uint8_t bytes[MaxEncodedSize];
  };
  Frame _transmit[QueueDepth];
  EncodedFrame _receive[QueueDepth];
  uint8_t _transmitHead = 0, _transmitCount = 0;
  uint8_t _receiveHead = 0, _receiveCount = 0;
  uint16_t _outputOffset = 0;
  uint8_t _input[MaxFrameSize];
  uint16_t _inputLength = 0;
  bool _framed = false, _command = false, _discard = false, _escaped = false;
  uint32_t _receivedFrames = 0, _sentFrames = 0, _malformed = 0, _dropped = 0;

  void rejectInput() {
    if (!_discard) ++_malformed;
    _discard = true;
  }

public:
  // Session resets deliberately preserve lifetime diagnostic counters.
  void resetPending() {
    _transmitHead = _transmitCount = _receiveHead = _receiveCount = 0;
    _outputOffset = _inputLength = 0;
    _framed = _command = _discard = _escaped = false;
  }

  bool canAcceptByte() const { return _transmitCount < QueueDepth; }

  // False means the caller must retain this byte and apply TCP backpressure.
  // The socket adapter checks canAcceptByte() BEFORE consuming a socket byte.
  bool feed(uint8_t byte) {
    if (!canAcceptByte()) return false;
    if (byte == Fend) {
      if (_framed && _command && !_discard) {
        if (_escaped || (_inputLength && _inputLength < MinimumFrameSize)) {
          rejectInput();
        } else if (_inputLength >= MinimumFrameSize) {
          Frame& frame = _transmit[(_transmitHead + _transmitCount) % QueueDepth];
          memcpy(frame.bytes, _input, _inputLength);
          frame.length = _inputLength;
          ++_transmitCount;
          ++_receivedFrames;
        }
      }
      // One FEND can close the last frame and start the next one.
      _framed = true;
      _command = _discard = _escaped = false;
      _inputLength = 0;
      return true;
    }
    if (!_framed || _discard) return true;
    if (!_command) {
      _command = true;
      // Other ports and control commands are ignored until the next boundary.
      if (byte != 0) _discard = true;
      return true;
    }
    if (_escaped) {
      _escaped = false;
      if (byte == Tfend) byte = Fend;
      else if (byte == Tfesc) byte = Fesc;
      else { rejectInput(); return true; }
    } else if (byte == Fesc) {
      _escaped = true;
      return true;
    }
    if (_inputLength >= MaxFrameSize) { rejectInput(); return true; }
    _input[_inputLength++] = byte;
    return true;
  }

  bool peekTransmit(const uint8_t*& bytes, size_t& length) const {
    if (!_transmitCount) { bytes = nullptr; length = 0; return false; }
    bytes = _transmit[_transmitHead].bytes;
    length = _transmit[_transmitHead].length;
    return true;
  }

  void consumeTransmit() {
    if (!_transmitCount) return;
    _transmitHead = (_transmitHead + 1) % QueueDepth;
    --_transmitCount;
  }

  bool queueReceive(const uint8_t* bytes, size_t length) {
    if (!bytes || length < MinimumFrameSize || length > MaxFrameSize
        || _receiveCount >= QueueDepth) { ++_dropped; return false; }
    EncodedFrame& frame = _receive[(_receiveHead + _receiveCount) % QueueDepth];
    size_t at = 0;
    frame.bytes[at++] = Fend;
    frame.bytes[at++] = 0;  // KISS port-0 DATA, never a companion binary frame.
    for (size_t i = 0; i < length; ++i) {
      const uint8_t byte = bytes[i];
      if (byte == Fend || byte == Fesc) {
        frame.bytes[at++] = Fesc;
        frame.bytes[at++] = byte == Fend ? Tfend : Tfesc;
      } else frame.bytes[at++] = byte;
    }
    frame.bytes[at++] = Fend;
    frame.length = static_cast<uint16_t>(at);
    ++_receiveCount;
    return true;
  }

  bool peekOutput(const uint8_t*& bytes, size_t& length) const {
    if (!_receiveCount) { bytes = nullptr; length = 0; return false; }
    bytes = _receive[_receiveHead].bytes + _outputOffset;
    length = _receive[_receiveHead].length - _outputOffset;
    return true;
  }

  void consumeOutput(size_t length) {
    if (!_receiveCount) return;
    const size_t remaining = _receive[_receiveHead].length - _outputOffset;
    if (length >= remaining) {
      _outputOffset = 0;
      _receiveHead = (_receiveHead + 1) % QueueDepth;
      --_receiveCount;
      ++_sentFrames;
    } else _outputOffset += static_cast<uint16_t>(length);
  }

  uint8_t pendingTransmit() const { return _transmitCount; }
  uint8_t pendingReceive() const { return _receiveCount; }
  uint32_t receivedFrames() const { return _receivedFrames; }
  uint32_t sentFrames() const { return _sentFrames; }
  uint32_t malformedFrames() const { return _malformed; }
  uint32_t droppedFrames() const { return _dropped; }
};

} }  // namespace mesh::ethernet
