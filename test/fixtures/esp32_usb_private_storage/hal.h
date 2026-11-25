#pragma once
#include <Arduino.h>
#include <cassert>
#include <random>

// Model NVS and the ESP32 startup RNG. Production code provides the cipher,
// identity/ACL formats, flash transactions, and CLI state machine.
namespace fs { using FS = FakeFilesystem; }
inline fs::FS SPIFFS;
struct { void end() {} } inline Serial;
using nvs_handle_t = std::string;
using esp_err_t = int;
enum { ESP_OK, ESP_ERR_NVS_NOT_FOUND, NVS_READONLY, NVS_READWRITE };
using Bytes = std::vector<uint8_t>;
inline std::map<std::string, std::map<std::string, Bytes>> nvs;
inline int nvs_open(const char* name, int mode, nvs_handle_t* h) {
  if (mode == NVS_READONLY && !nvs.count(name)) return ESP_ERR_NVS_NOT_FOUND;
  nvs[name];
  *h = name;
  return ESP_OK;
}
inline void nvs_close(nvs_handle_t) {}
inline int nvs_commit(nvs_handle_t) { return ESP_OK; }
inline int nvs_get_blob(nvs_handle_t h, const char* k, void* data, size_t* len) {
  if (!nvs[h].count(k)) return ESP_ERR_NVS_NOT_FOUND;
  const auto& v = nvs[h][k];
  if (v.size() > *len) return -1;
  memcpy(data, v.data(), v.size());
  *len = v.size();
  return ESP_OK;
}
inline int nvs_set_blob(nvs_handle_t h, const char* k, const void* data, size_t len) {
  auto p = static_cast<const uint8_t*>(data);
  nvs[h][k] = Bytes(p, p + len);
  return ESP_OK;
}
inline int nvs_get_u8(nvs_handle_t h, const char* k, uint8_t* v) {
  size_t len = 1;
  return nvs_get_blob(h, k, v, &len);
}
inline int nvs_set_u8(nvs_handle_t h, const char* k, uint8_t v) { return nvs_set_blob(h, k, &v, 1); }
inline int nvs_erase_key(nvs_handle_t h, const char* k) { return nvs[h].erase(k) ? ESP_OK : ESP_ERR_NVS_NOT_FOUND; }
struct Preferences {
  nvs_handle_t h;
  bool begin(const char* ns, bool ro) { return nvs_open(ns, ro ? NVS_READONLY : NVS_READWRITE, &h) == ESP_OK; }
  bool remove(const char* k) { return nvs_erase_key(h, k) == ESP_OK; }
  void end() {}
};
inline void esp_fill_random(void* out, size_t n) {
  static std::mt19937 generator(419);
  std::generate_n(static_cast<uint8_t*>(out), n, [] { return uint8_t(generator()); });
}
using portMUX_TYPE = int;
#define portMUX_INITIALIZER_UNLOCKED 0
#define portENTER_CRITICAL(p) ((void)(p))
#define portEXIT_CRITICAL(p) ((void)(p))
inline void mbedtls_platform_zeroize(void* p, size_t n) { memset(p, 0, n); }
