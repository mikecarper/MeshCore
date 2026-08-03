#pragma once

#include <stddef.h>
#include <stdint.h>

namespace mesh {

// A damaged-header condition can make the LR1110 place every subsequent
// packet four bytes beyond the offset reported by GetRxBufferStatus. Reading
// from the reported offset then returns four zero bytes before the real frame.
//
// This helper is intentionally consumed only by CustomLR1110. On the MeshCore
// wire, these bytes would otherwise decode as a transport-flood request whose
// first transport code is the reserved value 0000.
static constexpr size_t LR1110_RX_BUFFER_SHIFT_BYTES = 4;

inline bool hasLR1110RxBufferShiftSignature(const uint8_t* data, size_t len) {
  if (data == nullptr || len <= LR1110_RX_BUFFER_SHIFT_BYTES) return false;

  for (size_t i = 0; i < LR1110_RX_BUFFER_SHIFT_BYTES; ++i) {
    if (data[i] != 0) return false;
  }
  return true;
}

}  // namespace mesh
