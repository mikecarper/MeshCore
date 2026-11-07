#pragma once

#include <stdint.h>

namespace mesh {
namespace companion {

// PINs are encoded as integers; zero represents the six digits 000000.
inline bool usingCommonBlePin(uint32_t active_pin) {
  return active_pin == 123456 || active_pin == 111111 || active_pin == 0;
}

} // namespace companion
} // namespace mesh
