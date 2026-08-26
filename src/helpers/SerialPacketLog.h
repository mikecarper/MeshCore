#pragma once

#include <Arduino.h>
#include <stdarg.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>

namespace mesh {

// A USB CDC host can leave a port open without draining it. Some platform
// implementations then wait indefinitely inside write(), which stalls the
// mesh loop. Packet log lines use this bounded writer so a stalled host costs
// a diagnostic line instead of radio service.
#ifndef SERIAL_LOG_LINE_MAX
  #define SERIAL_LOG_LINE_MAX 640
#endif

#ifndef SERIAL_LOG_WRITE_BUDGET_MS
  #define SERIAL_LOG_WRITE_BUDGET_MS 20
#endif

inline uint32_t& serialLogDroppedCount() {
  static uint32_t count = 0;
  return count;
}

inline bool& serialLogPortSeen() {
  static bool seen = false;
  return seen;
}

template <class T>
bool serialLogEmit(T& out, const char* data, size_t len) {
  if (out.availableForWrite() <= 0) return false;

  const uint32_t start = millis();
  size_t sent = 0;
  while (sent < len) {
    int space = out.availableForWrite();
    if (space <= 0) {
      if ((uint32_t)(millis() - start) >= SERIAL_LOG_WRITE_BUDGET_MS) {
        return false;
      }
      delay(1);
      continue;
    }

    size_t chunk = (size_t)space;
    if (chunk > len - sent) chunk = len - sent;
    size_t written = out.write(
        reinterpret_cast<const uint8_t*>(data + sent), chunk);
    if (written == 0) return false;
    sent += written;
  }
  serialLogPortSeen() = true;
  return true;
}

template <size_t CAP = SERIAL_LOG_LINE_MAX>
class SerialLogLine {
 public:
  static_assert(CAP >= 3, "SerialLogLine needs room for text and CRLF");

  void printf(const char* format, ...) {
    size_t room = capacity() - _len;
    if (room == 0) {
      _truncated = true;
      return;
    }

    va_list args;
    va_start(args, format);
    int count = vsnprintf(&_buffer[_len], room, format, args);
    va_end(args);
    if (count < 0) return;
    if ((size_t)count >= room) {
      _len = capacity() - 1;
      _truncated = true;
    } else {
      _len += (size_t)count;
    }
  }

  void hex(const uint8_t* source, size_t len) {
    static const char digits[] = "0123456789ABCDEF";
    while (len > 0) {
      if (_len + 2 > capacity()) {
        _truncated = true;
        return;
      }
      uint8_t value = *source++;
      _buffer[_len++] = digits[value >> 4];
      _buffer[_len++] = digits[value & 0x0F];
      len--;
    }
  }

  // Preserve the compact packet logger's LF-only wire format when requested.
  template <class T>
  bool flush(T& out, bool use_crlf = true) {
    bool complete = !_truncated;
    if (use_crlf) _buffer[_len++] = '\r';
    _buffer[_len++] = '\n';
    const size_t line_len = _len;
    _len = 0;
    _truncated = false;

    uint32_t& dropped = serialLogDroppedCount();
    if (dropped > 0) {
      char marker[24];
      int count = snprintf(marker, sizeof(marker), "DROP:%u\r\n",
                           (unsigned)dropped);
      if (count > 0 && serialLogEmit(out, marker, (size_t)count)) dropped = 0;
    }

    if (!serialLogEmit(out, _buffer, line_len)) complete = false;
    if (!complete && serialLogPortSeen()) dropped++;
    return complete;
  }

 private:
  size_t capacity() const { return CAP - 2; }

  char _buffer[CAP];
  size_t _len = 0;
  bool _truncated = false;
};

inline void serialLogBegin() {
#if defined(ESP32_PLATFORM) && defined(ARDUINO_USB_CDC_ON_BOOT) && \
    (ARDUINO_USB_CDC_ON_BOOT == 1)
  Serial.setTxTimeoutMs(0);
#endif
}

}  // namespace mesh
