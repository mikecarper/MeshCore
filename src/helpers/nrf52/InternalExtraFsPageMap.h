#pragma once

#include <stdint.h>

namespace mesh {
namespace storage {

static const uint32_t INTERNAL_EXTRAFS_PAGE_COUNT = 25;
static const uint32_t INTERNAL_EXTRAFS_PAGE_MASK =
    (1UL << INTERNAL_EXTRAFS_PAGE_COUNT) - 1UL;

inline uint8_t countInternalExtraFsBadPages(uint32_t mask) {
  uint8_t count = 0;
  while (mask != 0) {
    count += static_cast<uint8_t>(mask & 1U);
    mask >>= 1;
  }
  return count;
}

// The old all-good layout is the identity map. Once a confirmed bad page is
// retired, every logical 4 KiB page after it moves to the next good physical
// page. A fixed 128-byte LittleFS block never straddles those page boundaries.
inline uint32_t internalExtraFsPhysicalPage(uint32_t bad_mask,
                                            uint32_t logical_page) {
  if ((bad_mask & ~INTERNAL_EXTRAFS_PAGE_MASK) != 0
      || logical_page >= INTERNAL_EXTRAFS_PAGE_COUNT
          - countInternalExtraFsBadPages(bad_mask)) {
    return UINT32_MAX;
  }
  for (uint32_t physical = 0; physical < INTERNAL_EXTRAFS_PAGE_COUNT;
       physical++) {
    if ((bad_mask & (1UL << physical)) != 0) continue;
    if (logical_page == 0) return physical;
    logical_page--;
  }
  return UINT32_MAX;
}

} // namespace storage
} // namespace mesh
