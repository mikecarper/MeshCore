#pragma once

#include <ArduinoJson.h>
#include <stdint.h>
#include <stddef.h>
#include <stdlib.h>
#include <string.h>
#include <stdio.h>

namespace mesh {

// JsonDocument v7 is dynamically allocated. Bound actual allocations rather
// than relying on the deprecated StaticJsonDocument capacity shim.
class RoomJsonAllocator : public ArduinoJson::Allocator {
  union Header { max_align_t alignment; size_t bytes; };
  size_t used_ = 0;
public:
  static constexpr size_t BUDGET = 8192;
  void* allocate(size_t bytes) override {
    if (bytes > BUDGET - used_ || sizeof(Header) > BUDGET - used_ - bytes) return nullptr;
    auto* header = static_cast<Header*>(malloc(sizeof(Header) + bytes));
    if (!header) return nullptr;
    header->bytes = bytes; used_ += sizeof(Header) + bytes; return header + 1;
  }
  void deallocate(void* ptr) override {
    if (!ptr) return;
    auto* header = static_cast<Header*>(ptr) - 1;
    used_ -= sizeof(Header) + header->bytes; free(header);
  }
  void* reallocate(void* ptr, size_t bytes) override {
    if (!ptr) return allocate(bytes);
    auto* header = static_cast<Header*>(ptr) - 1;
    const size_t remaining = used_ - sizeof(Header) - header->bytes;
    if (bytes > BUDGET - remaining || sizeof(Header) > BUDGET - remaining - bytes) return nullptr;
    header = static_cast<Header*>(realloc(header, sizeof(Header) + bytes));
    if (!header) return nullptr;
    header->bytes = bytes; used_ = remaining + sizeof(Header) + bytes; return header + 1;
  }
};

// Replies use their fixed mailbox buffer, with no second JSON heap pool.
class RoomJsonWriter {
  char* output_;
  size_t capacity_, used_ = 0;
  bool valid_ = true;
public:
  RoomJsonWriter(char* output, size_t capacity) : output_(output), capacity_(capacity) {
    if (!output || !capacity) valid_ = false;
    else output[0] = 0;
  }
  void raw(const char* text) {
    const size_t length = strlen(text);
    if (!valid_ || length >= capacity_ - used_) { valid_ = false; return; }
    memcpy(output_ + used_, text, length + 1); used_ += length;
  }
  void number(uint32_t value) { char text[12]; snprintf(text, sizeof(text), "%lu", (unsigned long)value); raw(text); }
  void string(const char* text) {
    raw("\"");
    for (const auto* p = reinterpret_cast<const uint8_t*>(text); valid_ && *p; ++p) {
      char escaped[7] = {};
      if (*p == '"' || *p == '\\') { escaped[0] = '\\'; escaped[1] = *p; }
      else if (*p < 0x20) snprintf(escaped, sizeof(escaped), "\\u%04x", unsigned(*p));
      else escaped[0] = *p;
      raw(escaped);
    }
    raw("\"");
  }
  bool finish() {
    if (!valid_ && output_ && capacity_ >= 29) strcpy(output_, "{\"error\":\"reply too large\"}");
    return valid_;
  }
};

inline void roomEncodeBase64(const uint8_t* bytes, size_t length, char* output) {
  static const char alphabet[] = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
  size_t used = 0;
  for (size_t i = 0; i < length; i += 3) {
    const uint32_t value = (uint32_t(bytes[i]) << 16)
        | (i + 1 < length ? uint32_t(bytes[i + 1]) << 8 : 0)
        | (i + 2 < length ? bytes[i + 2] : 0);
    output[used++] = alphabet[(value >> 18) & 63]; output[used++] = alphabet[(value >> 12) & 63];
    output[used++] = i + 1 < length ? alphabet[(value >> 6) & 63] : '=';
    output[used++] = i + 2 < length ? alphabet[value & 63] : '=';
  }
  output[used] = 0;
}

inline bool roomDecodeBase64(const char* text, uint8_t* bytes, size_t capacity, size_t& length) {
  length = 0;
  const size_t encoded = strlen(text);
  if (encoded % 4 != 0 || encoded > ((capacity + 2) / 3) * 4) return false;
  auto digit = [](char c) -> int {
    if (c >= 'A' && c <= 'Z') return c - 'A'; if (c >= 'a' && c <= 'z') return c - 'a' + 26;
    if (c >= '0' && c <= '9') return c - '0' + 52; if (c == '+') return 62; if (c == '/') return 63;
    return -1;
  };
  for (size_t i = 0; i < encoded; i += 4) {
    int values[4];
    for (size_t j = 0; j < 4; ++j) values[j] = text[i + j] == '=' ? 0 : digit(text[i + j]);
    if (values[0] < 0 || values[1] < 0 || values[2] < 0 || values[3] < 0
        || text[i] == '=' || text[i + 1] == '='
        || (text[i + 2] == '=' && text[i + 3] != '=')
        || (i + 4 != encoded && (text[i + 2] == '=' || text[i + 3] == '='))) return false;
    const size_t n = text[i + 2] == '=' ? 1 : (text[i + 3] == '=' ? 2 : 3);
    if (length + n > capacity || (n == 1 && (values[1] & 15)) || (n == 2 && (values[2] & 3))) return false;
    const uint32_t value = (uint32_t(values[0]) << 18) | (uint32_t(values[1]) << 12)
        | (uint32_t(values[2]) << 6) | uint32_t(values[3]);
    for (size_t j = 0; j < n; ++j) bytes[length++] = uint8_t(value >> (16 - 8 * j));
  }
  return true;
}
} // namespace mesh
