#pragma once

#include <SHA256.h>
#include <stddef.h>
#include <stdint.h>

namespace mesh { namespace tracker {

// Adapter for the project's existing rweather/Crypto SHA256 implementation.
inline bool homeBeaconHmacSha256(const uint8_t* key, size_t key_len,
                                 const uint8_t* data, size_t data_len,
                                 uint8_t out[32]) {
  SHA256 sha;
  sha.resetHMAC(key, key_len);
  sha.update(data, data_len);
  sha.finalizeHMAC(key, key_len, out, 32);
  return true;
}

}} // namespace mesh::tracker
