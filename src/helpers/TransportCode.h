#pragma once

#include <SHA256.h>
#include <stdint.h>

namespace mesh {
inline uint16_t calculateTransportCode(const uint8_t (&key)[16], uint8_t type,
                                       const uint8_t* payload, size_t length) {
  uint16_t code;
  SHA256 sha;
  sha.resetHMAC(key, sizeof(key));
  sha.update(&type, 1);
  sha.update(payload, length);
  sha.finalizeHMAC(key, sizeof(key), reinterpret_cast<uint8_t*>(&code), sizeof(code));
  if (code == 0) return 1;
  if (code == 0xFFFF) return 0xFFFE;
  return code;
}
}
