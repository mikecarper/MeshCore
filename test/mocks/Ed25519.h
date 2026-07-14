#pragma once

#include <cstdint>

class Ed25519 {
public:
  static bool verify(const uint8_t*, const uint8_t*, const uint8_t*, int) {
    return true;
  }
};
