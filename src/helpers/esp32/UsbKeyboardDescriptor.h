#pragma once

#include <stddef.h>
#include <stdint.h>

namespace mesh { namespace usbkeyboard {

struct BootEndpoint {
  uint8_t interface_number = 0;
  uint8_t endpoint = 0;
  uint16_t packet_size = 0;
};

// Find an interrupt-IN endpoint within a boot-keyboard interface. Descriptors
// from mice and other interfaces must never be mistaken for a keyboard.
inline bool findBootKeyboard(const uint8_t* bytes, size_t total, BootEndpoint& result) {
  if (!bytes || total < 9 || total > 4096) return false;
  bool keyboard = false;
  uint8_t interface_number = 0;
  for (size_t pos = 0; pos + 2 <= total;) {
    const size_t length = bytes[pos];
    const uint8_t type = bytes[pos + 1];
    if (length < 2 || pos + length > total) return false;
    const uint8_t* d = bytes + pos;
    if (type == 4 && length >= 9) {
      keyboard = d[3] == 0 && d[5] == 3 && d[6] == 1 && d[7] == 1;
      if (keyboard) interface_number = d[2];
    } else if (keyboard && type == 5 && length >= 7
               && (d[2] & 0x80) && (d[3] & 3) == 3) {
      const uint16_t mps = (static_cast<uint16_t>(d[4]) | (static_cast<uint16_t>(d[5]) << 8)) & 0x7ff;
      if (mps >= 8 && mps <= 64) {
        result.interface_number = interface_number;
        result.endpoint = d[2];
        result.packet_size = mps;
        return true;
      }
    }
    pos += length;
  }
  return false;
}

} }
