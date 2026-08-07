#pragma once

#include <stddef.h>
#include <stdint.h>
#include <string.h>

namespace mesh {

// Backward-compatible logical request identity for remote CLI retries. The
// on-air timestamp remains fresh for legacy replay guards. New servers find
// this authenticated extension after the command's NUL; old servers stop at
// that NUL and continue to see the original command text.
class RemoteCliRequest {
public:
  static constexpr size_t EXTENSION_SIZE = 9;  // NUL + "MCR1" + uint32 id

  static size_t append(uint8_t* payload, size_t capacity,
                       size_t command_offset, size_t command_len,
                       uint32_t logical_id) {
    if (payload == NULL || logical_id == 0
        || command_offset + command_len + EXTENSION_SIZE > capacity) {
      return 0;
    }
    size_t pos = command_offset + command_len;
    payload[pos++] = 0;
    payload[pos++] = 'M';
    payload[pos++] = 'C';
    payload[pos++] = 'R';
    payload[pos++] = '1';
    memcpy(payload + pos, &logical_id, sizeof(logical_id));
    return pos + sizeof(logical_id);
  }

  static bool parse(const uint8_t* payload, size_t payload_len,
                    size_t command_offset, uint32_t& logical_id) {
    if (payload == NULL || command_offset >= payload_len) return false;
    const uint8_t* terminator = (const uint8_t*)memchr(
        payload + command_offset, 0, payload_len - command_offset);
    if (terminator == NULL) return false;
    size_t pos = (size_t)(terminator - payload) + 1;
    if (pos + 8 > payload_len
        || payload[pos] != 'M' || payload[pos + 1] != 'C'
        || payload[pos + 2] != 'R' || payload[pos + 3] != '1') {
      return false;
    }
    memcpy(&logical_id, payload + pos + 4, sizeof(logical_id));
    return logical_id != 0;
  }
};

}  // namespace mesh
