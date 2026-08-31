#pragma once

#include <stddef.h>
#include <stdint.h>

namespace mesh {

// Bounded, allocation-free proof that a UART stream contains a standard GPS
// NMEA sentence. Merely seeing one byte is not enough: RS-232 traffic or line
// noise must not be allowed to claim the GPS UART.
class NmeaSentenceProbe {
 public:
  bool ingest(uint8_t byte) {
    if (_found) return true;

    if (byte == '$') {
      resetSentence();
      _state = State::Body;
      return false;
    }

    switch (_state) {
      case State::Idle:
        return false;
      case State::Body:
        if (byte == '*') {
          if (_body_length < 6 || !_header_valid) {
            resetSentence();
          } else {
            _state = State::ChecksumHigh;
          }
          return false;
        }
        if (byte < 0x20 || byte > 0x7e || _body_length >= kMaxBodyLength) {
          resetSentence();
          return false;
        }
        validateHeaderByte(byte);
        _checksum ^= byte;
        ++_body_length;
        return false;
      case State::ChecksumHigh: {
        const int nibble = hexNibble(byte);
        if (nibble < 0) {
          resetSentence();
        } else {
          _expected_checksum = static_cast<uint8_t>(nibble << 4);
          _state = State::ChecksumLow;
        }
        return false;
      }
      case State::ChecksumLow: {
        const int nibble = hexNibble(byte);
        if (nibble >= 0) {
          _expected_checksum |= static_cast<uint8_t>(nibble);
          _found = _header_valid && recognizedTalker()
                   && _expected_checksum == _checksum;
        }
        resetSentence();
        return _found;
      }
    }
    return false;
  }

  bool found() const { return _found; }

 private:
  enum class State : uint8_t { Idle, Body, ChecksumHigh, ChecksumLow };
  static constexpr size_t kMaxBodyLength = 79;

  State _state = State::Idle;
  uint8_t _checksum = 0;
  uint8_t _expected_checksum = 0;
  size_t _body_length = 0;
  char _header[5] = {};
  bool _header_valid = true;
  bool _found = false;

  static int hexNibble(uint8_t byte) {
    if (byte >= '0' && byte <= '9') return byte - '0';
    if (byte >= 'A' && byte <= 'F') return byte - 'A' + 10;
    if (byte >= 'a' && byte <= 'f') return byte - 'a' + 10;
    return -1;
  }

  void validateHeaderByte(uint8_t byte) {
    if (_body_length < 5) {
      if (byte < 'A' || byte > 'Z') _header_valid = false;
      _header[_body_length] = static_cast<char>(byte);
    } else if (_body_length == 5 && byte != ',') {
      _header_valid = false;
    }
  }

  bool recognizedTalker() const {
    if (_header[0] == 'G') {
      switch (_header[1]) {
        case 'A':  // Galileo
        case 'B':  // BeiDou
        case 'L':  // GLONASS
        case 'N':  // combined GNSS
        case 'P':  // GPS
        case 'Q':  // QZSS
          return true;
      }
    }
    return _header[0] == 'B' && _header[1] == 'D';
  }

  void resetSentence() {
    _state = State::Idle;
    _checksum = 0;
    _expected_checksum = 0;
    _body_length = 0;
    _header_valid = true;
    for (char& byte : _header) byte = 0;
  }
};

}  // namespace mesh
