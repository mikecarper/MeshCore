#pragma once

#include <stdint.h>
#include "CompanionFrameLimits.h"

namespace mesh {

// Share one admission implementation across transports without changing their
// byte-sized length or fixed-capacity queue storage.
struct CompanionTxFrame {
  uint8_t len;
  uint8_t buf[MAX_FRAME_SIZE];
};

}  // namespace mesh
