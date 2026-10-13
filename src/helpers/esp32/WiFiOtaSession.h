#pragma once

#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

namespace mesh {
namespace wifi {

// Session binding prevents a phone from uploading to a different node sharing
// an OTA URL. It is not authentication: HTTP clients can read /ota/info.
struct OtaSessionIdentity {
  char session[33] = {};
  char device[13] = {};
  char hardware[33] = {};
  uint16_t chip = 0;
  uint32_t slot = 0;

  bool prepared() const { return session[0] != 0; }
  void clear() { memset(this, 0, sizeof(*this)); }

  static bool validHardware(const char* value) {
    if (!value || !value[0]) return false;
    for (size_t i = 0; value[i]; ++i) {
      const unsigned char c = value[i];
      if (i >= 32 || !((c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z') ||
          (c >= '0' && c <= '9') || c == '_' || c == '-' || c == '.')) return false;
    }
    return true;
  }

  static void hex(char* out, const uint8_t* bytes, size_t count) {
    static const char digits[] = "0123456789abcdef";
    for (size_t i = 0; i < count; ++i) {
      out[2 * i] = digits[bytes[i] >> 4];
      out[2 * i + 1] = digits[bytes[i] & 15];
    }
    out[2 * count] = 0;
  }

  bool prepare(const uint8_t nonce[16], const uint8_t mac[6], uint16_t chip_id,
               uint32_t slot_size, const char* hardware_id) {
    if (!nonce || !mac || !slot_size || !validHardware(hardware_id)) return false;
    char mac_text[13];
    hex(mac_text, mac, 6);
    if (strcmp(mac_text, "000000000000") == 0 || strcmp(mac_text, "ffffffffffff") == 0)
      return false;
    if (prepared()) return chip == chip_id && slot == slot_size &&
        strcmp(device, mac_text) == 0 && strcmp(hardware, hardware_id) == 0;
    hex(session, nonce, 16);
    memcpy(device, mac_text, sizeof(device));
    strcpy(hardware, hardware_id);
    chip = chip_id;
    slot = slot_size;
    return true;
  }

  bool writeCli(char* reply, size_t capacity) const {
    if (!prepared() || !reply || !capacity) return false;
    const int count = snprintf(reply, capacity, "OTA1 s=%s d=%s c=%u slot=%lu hw=%s",
        session, device, static_cast<unsigned>(chip), static_cast<unsigned long>(slot), hardware);
    return count >= 0 && static_cast<size_t>(count) < capacity;
  }

  bool writeJson(char* reply, size_t capacity) const {
    if (!prepared() || !reply || !capacity) return false;
    const int count = snprintf(reply, capacity,
        "{\"version\":1,\"protocol\":\"lightweight\",\"session\":\"%s\","
        "\"device\":\"%s\",\"chip\":%u,\"slot\":%lu,\"hardware\":\"%s\"}",
        session, device, static_cast<unsigned>(chip), static_cast<unsigned long>(slot), hardware);
    return count >= 0 && static_cast<size_t>(count) < capacity;
  }
};

// Bounded, allocation-free HTTP framing. Reject malformed/duplicate headers,
// signed/overflow lengths, encoded/chunked bodies and unsupported expectations
// before Update.begin. Only raw binary, one declared length and one binding
// header enter the streaming uploader.
struct OtaRequestHeaders {
  static constexpr size_t kMaxHeaders = 24;
  static constexpr size_t kMaxHeaderName = 48;
  static constexpr size_t kMaxHeaderBytes = 4096;
  char names[kMaxHeaders][kMaxHeaderName] = {};
  size_t header_count = 0;
  size_t header_bytes = 0;
  size_t content_length = 0;
  char session[33] = {};
  bool has_length = false;
  bool has_session = false;
  bool has_binary_type = false;
  bool invalid = false;

  static char lower(char c) { return c >= 'A' && c <= 'Z' ? c + ('a' - 'A') : c; }
  static bool equals(const char* a, const char* b) {
    while (*a && *b) if (lower(*a++) != lower(*b++)) return false;
    return *a == *b;
  }
  static bool tokenChar(char c) {
    return (c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z') ||
        (c >= '0' && c <= '9') || strchr("!#$%&'*+-.^_`|~", c) != nullptr;
  }
  bool reject() { invalid = true; return false; }

  bool add(const char* line) {
    if (invalid || !line || !line[0]) return reject();
    const size_t length = strlen(line);
    if (length > kMaxHeaderBytes - header_bytes || header_count >= kMaxHeaders) return reject();
    header_bytes += length;
    const char* colon = strchr(line, ':');
    if (!colon || colon == line) return reject();
    const size_t name_length = static_cast<size_t>(colon - line);
    if (name_length >= kMaxHeaderName) return reject();
    char name[kMaxHeaderName] = {};
    for (size_t i = 0; i < name_length; ++i) {
      if (!tokenChar(line[i])) return reject();
      name[i] = lower(line[i]);
    }
    for (size_t i = 0; i < header_count; ++i)
      if (strcmp(names[i], name) == 0) return reject();
    strcpy(names[header_count++], name);
    const char* value = colon + 1;
    while (*value == ' ' || *value == '\t') ++value;
    const char* end = line + length;
    while (end > value && (end[-1] == ' ' || end[-1] == '\t')) --end;
    for (const char* p = value; p != end; ++p)
      if (static_cast<unsigned char>(*p) < 32 || static_cast<unsigned char>(*p) > 126) return reject();
    const size_t value_length = static_cast<size_t>(end - value);
    if (strcmp(name, "content-length") == 0) {
      if (!value_length) return reject();
      size_t number = 0;
      for (const char* p = value; p != end; ++p) {
        if (*p < '0' || *p > '9') return reject();
        const unsigned digit = static_cast<unsigned>(*p - '0');
        if (number > (SIZE_MAX - digit) / 10) return reject();
        number = number * 10 + digit;
      }
      content_length = number;
      has_length = true;
    } else if (strcmp(name, "x-meshcore-ota-session") == 0) {
      if (value_length != 32) return reject();
      for (size_t i = 0; i < 32; ++i) {
        const char c = value[i];
        if (!((c >= '0' && c <= '9') || (c >= 'a' && c <= 'f'))) return reject();
        session[i] = c;
      }
      has_session = true;
    } else if (strcmp(name, "content-type") == 0) {
      static const char binary[] = "application/octet-stream";
      has_binary_type = value_length == sizeof(binary) - 1;
      for (size_t i = 0; has_binary_type && i < value_length; ++i)
        has_binary_type = lower(value[i]) == binary[i];
    } else if (strcmp(name, "transfer-encoding") == 0 ||
               strcmp(name, "content-encoding") == 0 || strcmp(name, "expect") == 0) {
      return reject();
    }
    return true;
  }

  bool allowsUpdate(const OtaSessionIdentity& identity, size_t buffered_body) const {
    return !invalid && identity.prepared() && has_length && content_length > 0 &&
        content_length <= identity.slot && buffered_body <= content_length &&
        has_session && has_binary_type && strcmp(session, identity.session) == 0;
  }
};

} // namespace wifi
} // namespace mesh
