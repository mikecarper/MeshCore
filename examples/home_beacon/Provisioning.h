#pragma once

#include <stddef.h>
#include <stdint.h>
#include <string.h>

namespace mesh { namespace home_beacon {

struct Settings {
  uint32_t magic;
  char ssid[33];
  char password[64];
  uint8_t id[4];
  uint8_t key[32];
  uint8_t key_len;
};
static constexpr uint32_t kSettingsMagic = 0x3142484d; // MHB1

inline bool boundedString(const char* s, size_t cap, size_t min_len) {
  for (size_t i = 0; i < cap; ++i) {
    if (!s[i]) return i >= min_len;
    const uint8_t c = uint8_t(s[i]);
    if (c < 32 || c == 127) return false;
  }
  return false;
}

inline bool validSettings(const Settings& s) {
  if (s.magic != kSettingsMagic || !boundedString(s.ssid, sizeof(s.ssid), 1) ||
      !boundedString(s.password, sizeof(s.password), 8) ||
      (s.key_len != 16 && s.key_len != 32)) return false;
  uint8_t any = 0;
  for (size_t i = 0; i < s.key_len; ++i) any |= s.key[i];
  return any != 0;
}

class Provisioning {
 public:
  enum class Stage { Idle, Ssid, Password, Id, Key, Complete };
  enum class Result { Accepted, Invalid, Complete };
  void begin() { clear(); stage_ = Stage::Ssid; }
  void clear() {
    volatile uint8_t* p = reinterpret_cast<volatile uint8_t*>(&settings_);
    for (size_t i = 0; i < sizeof(settings_); ++i) p[i] = 0;
    stage_ = Stage::Idle;
  }
  Stage stage() const { return stage_; }
  const Settings& settings() const { return settings_; }

  // Input is raw UTF-8 bytes, with CR/LF removed by the serial transport.
  // The caller never echoes input and must erase its line buffer after use.
  Result line(const char* input, size_t n) {
    if (!input) return Result::Invalid;
    switch (stage_) {
      case Stage::Ssid:
        if (!copyLine(settings_.ssid, sizeof(settings_.ssid), input, n, 1)) return Result::Invalid;
        stage_ = Stage::Password; return Result::Accepted;
      case Stage::Password:
        if (!copyLine(settings_.password, sizeof(settings_.password), input, n, 8)) return Result::Invalid;
        stage_ = Stage::Id; return Result::Accepted;
      case Stage::Id:
        if (n != 8 || !hex(input, n, settings_.id)) return Result::Invalid;
        stage_ = Stage::Key; return Result::Accepted;
      case Stage::Key:
        if ((n != 32 && n != 64) || !hex(input, n, settings_.key)) return Result::Invalid;
        settings_.key_len = uint8_t(n / 2);
        settings_.magic = kSettingsMagic;
        if (!validSettings(settings_)) return Result::Invalid;
        stage_ = Stage::Complete; return Result::Complete;
      default: return Result::Invalid;
    }
  }
 private:
  static bool copyLine(char* out, size_t cap, const char* in, size_t n, size_t min_len) {
    if (n < min_len || n >= cap) return false;
    for (size_t i = 0; i < n; ++i)
      if (uint8_t(in[i]) < 32 || uint8_t(in[i]) == 127) return false;
    memcpy(out, in, n); out[n] = 0; return true;
  }
  static int nibble(char c) {
    if (c >= '0' && c <= '9') return c - '0';
    if (c >= 'a' && c <= 'f') return c - 'a' + 10;
    if (c >= 'A' && c <= 'F') return c - 'A' + 10;
    return -1;
  }
  static bool hex(const char* in, size_t n, uint8_t* out) {
    for (size_t i = 0; i < n; ++i) if (nibble(in[i]) < 0) return false;
    for (size_t i = 0; i < n / 2; ++i) out[i] = uint8_t((nibble(in[2*i]) << 4) | nibble(in[2*i+1]));
    return true;
  }
  Settings settings_{};
  Stage stage_ = Stage::Idle;
};

}} // namespace mesh::home_beacon
