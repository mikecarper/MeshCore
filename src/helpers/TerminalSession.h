#pragma once

#include <stdint.h>
#include <stddef.h>
#include <string.h>

namespace mesh {

// The USB and TCP input buffers hold MAX_TRANS_UNIT * 2 + 32 bytes. Contact
// cards need almost twice a radio packet's size after hexadecimal encoding.
static const size_t kTerminalCommandCapacity = 255 * 2 + 32;
#ifndef MESH_TERMINAL_INITIAL_OUTPUT_CAPACITY
#define MESH_TERMINAL_INITIAL_OUTPUT_CAPACITY 4096
#endif
static_assert(MESH_TERMINAL_INITIAL_OUTPUT_CAPACITY >= 1024 &&
              MESH_TERMINAL_INITIAL_OUTPUT_CAPACITY <= 32768 &&
              (MESH_TERMINAL_INITIAL_OUTPUT_CAPACITY & (MESH_TERMINAL_INITIAL_OUTPUT_CAPACITY - 1)) == 0,
              "Terminal initial capacity must be a power of two from 1024 to 32768");
static const size_t kTerminalInitialOutputCapacity = MESH_TERMINAL_INITIAL_OUTPUT_CAPACITY;
static const size_t kTerminalOutputCapacity = 32768;

// A bounded scrollback with absolute cursors. Reads do not consume data, so a
// lost HTTP response can be fetched again without skipping or repeating text.
// The caller serializes access and supplies storage only for a live session.
class TerminalOutputBuffer {
  char* _data;
  size_t _capacity;
  uint64_t _end = 0;
  uint64_t _first = 0;

public:
  TerminalOutputBuffer(char* data, size_t capacity)
      : _data(data), _capacity(capacity) {}
  uint64_t end() const { return _end; }
  void clear() { _end = _first = 0; }
  void rebind(char* data, size_t capacity) {
    const uint64_t first = _end > capacity && _end - capacity > _first
        ? _end - capacity : _first;
    for (uint64_t i = first; i < _end; ++i) data[i % capacity] = _data[i % _capacity];
    _data = data;
    _capacity = capacity;
    _first = first;
  }
  void append(const uint8_t* data, size_t len) {
    for (size_t i = 0; i < len; ++i) {
      if (_capacity) _data[_end % _capacity] = data[i];
      ++_end;
    }
    if (_end > _capacity && _end - _capacity > _first) _first = _end - _capacity;
  }
  size_t read(uint64_t& cursor, char* out, size_t size, bool& lost) const {
    const uint64_t first = _first;
    lost = cursor < first;
    if (cursor < first) cursor = first;
    if (cursor > _end) cursor = _end;
    size_t n = 0;
    while (n + 1 < size && cursor < _end) {
      out[n++] = _data[cursor++ % _capacity];
    }
    // JSON strings must contain complete UTF-8 characters even when a page
    // boundary lands inside a multibyte message or contact name.
    if (n) {
      size_t lead = n - 1;
      while (lead > 0 && (static_cast<uint8_t>(out[lead]) & 0xC0) == 0x80) --lead;
      const uint8_t byte = static_cast<uint8_t>(out[lead]);
      const size_t width = byte >= 0xF0 ? 4 : byte >= 0xE0 ? 3 : byte >= 0xC0 ? 2 : 1;
      if (n - lead < width) { cursor -= n - lead; n = lead; }
    }
    if (size) out[n] = 0;
    return n;
  }
};

// Exactly one command can be pending. A monotonically increasing sequence
// makes retries idempotent even after later commands have completed.
class TerminalCommandQueue {
public:
  enum Result { Accepted, Replay, Busy, OutOfOrder, Invalid };
  char command[kTerminalCommandCapacity] = {0};
  uint32_t sequence = 0;
  bool pending = false;

  static bool valid(const char* text, size_t length) {
    return text && length && length < kTerminalCommandCapacity
        && strlen(text) == length && !memchr(text, '\r', length)
        && !memchr(text, '\n', length);
  }

  Result submit(uint32_t seq, const char* text, size_t length) {
    if (!valid(text, length)) return Invalid;
    if (seq == 0) return OutOfOrder;
    if (seq <= sequence) return Replay;
    if (pending) return Busy;
    if (seq != sequence + 1 || sequence == UINT32_MAX) return OutOfOrder;
    memcpy(command, text, length);
    command[length] = 0;
    sequence = seq;
    pending = true;
    return Accepted;
  }
  void finish() {
    memset(command, 0, sizeof(command));
    pending = false;
  }
};

}  // namespace mesh
