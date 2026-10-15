#pragma once
#include <cassert>
#include <cstddef>
#include <cstdint>
#include <map>
#include <string>

namespace fake_nvs {
inline std::map<std::string, uint8_t> bytes;
inline int opens = 0, closes = 0, reads = 0, writes = 0;
inline bool open_fails = false, write_fails = false, verify_fails = false;
}

class Preferences {
  bool open = false;
public:
  bool begin(const char* name, bool read_only) {
    assert(std::string(name) == "mesh-wifi" && !read_only);
    ++fake_nvs::opens;
    open = !fake_nvs::open_fails;
    return open;
  }
  void end() { assert(open); open = false; ++fake_nvs::closes; }
  bool isKey(const char* key) { assert(open); return fake_nvs::bytes.count(key); }
  uint8_t getUChar(const char* key, uint8_t fallback) {
    assert(open); ++fake_nvs::reads;
    if (fake_nvs::verify_fails && fallback == 255) return fallback;
    auto found = fake_nvs::bytes.find(key);
    return found == fake_nvs::bytes.end() ? fallback : found->second;
  }
  size_t putUChar(const char* key, uint8_t value) {
    assert(open); ++fake_nvs::writes;
    if (fake_nvs::write_fails) return 0;
    fake_nvs::bytes[key] = value;
    return 1;
  }
};
