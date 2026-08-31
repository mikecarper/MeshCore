#pragma once

#include <stddef.h>
#include <stdint.h>
#include <string.h>

namespace mesh {

static const uint8_t CLIENT_ACL_CRC_MAGIC[4] = {'M', 'C', 'A', '1'};

inline uint32_t updateClientACLCRC(uint32_t crc, const uint8_t* data,
                                   size_t length) {
  while (length-- != 0) {
    crc ^= *data++;
    for (uint8_t bit = 0; bit < 8; bit++) {
      crc = (crc >> 1) ^ (0xEDB88320UL & (uint32_t)-(int32_t)(crc & 1U));
    }
  }
  return crc;
}

enum ClientACLImageKind {
  CLIENT_ACL_IMAGE_INVALID,
  CLIENT_ACL_IMAGE_LEGACY,
  CLIENT_ACL_IMAGE_CRC,
};

inline ClientACLImageKind classifyClientACLImage(
    const uint8_t* image, size_t size,
    size_t current_record_size, size_t legacy_record_size) {
  if (size >= 8 && (size - 8) % current_record_size == 0) {
    const size_t payload_size = size - 8;
    uint32_t stored_crc;
    memcpy(&stored_crc, image + payload_size + 4, sizeof(stored_crc));
    uint32_t crc = updateClientACLCRC(0xFFFFFFFFUL, image, payload_size)
        ^ 0xFFFFFFFFUL;
    if (memcmp(image + payload_size, CLIENT_ACL_CRC_MAGIC, 4) == 0
        && stored_crc == crc) {
      return CLIENT_ACL_IMAGE_CRC;
    }
  }
  if (size % current_record_size == 0
      || size % legacy_record_size == 0) {
    return CLIENT_ACL_IMAGE_LEGACY;
  }
  return CLIENT_ACL_IMAGE_INVALID;
}

} // namespace mesh
