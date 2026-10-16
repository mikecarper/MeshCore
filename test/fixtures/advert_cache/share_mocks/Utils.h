#pragma once

#include <stddef.h>
#include <stdint.h>

namespace mesh {
class Utils {
public:
  static void toHex(char* dest, const uint8_t* bytes, size_t length) {
    static const char hex[] = "0123456789ABCDEF";
    for (size_t i = 0; i < length; ++i) {
      dest[2 * i] = hex[bytes[i] >> 4];
      dest[2 * i + 1] = hex[bytes[i] & 15];
    }
    dest[2 * length] = 0;
  }
};
}
