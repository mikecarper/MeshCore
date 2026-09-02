#pragma once

#include <stdint.h>

namespace mesh {
namespace storage {

static const uint32_t INTERNAL_EXTRAFS_START = 0xD4000UL;
static const uint32_t INTERNAL_EXTRAFS_SIZE = 0x19000UL;
static const uint32_t INTERNAL_EXTRAFS_BLOCK_SIZE = 128UL;

inline bool isExpectedInternalExtraFsGeometry(uint32_t start, uint32_t size,
                                              uint32_t block_size) {
  return start == INTERNAL_EXTRAFS_START
      && size == INTERNAL_EXTRAFS_SIZE
      && block_size == INTERNAL_EXTRAFS_BLOCK_SIZE;
}

inline bool isInternalExtraFsReservedByApplication(uint32_t application_end) {
  return application_end <= INTERNAL_EXTRAFS_START;
}

enum class InternalSecondaryFsBootResult {
  Mounted,
  InitializedBlank,
  PreservedNonBlank,
  InitializationFailed,
};

// CustomLFS::begin() automatically formats after a failed mount. Use the base
// non-formatting mount through this policy instead: an erased factory region
// may be initialized, while nonblank media is preserved for explicit repair.
template <typename Mount, typename IsBlank, typename Format>
InternalSecondaryFsBootResult prepareInternalSecondaryFilesystem(
    Mount mount, IsBlank is_blank, Format format) {
  if (mount()) return InternalSecondaryFsBootResult::Mounted;
  if (!is_blank()) return InternalSecondaryFsBootResult::PreservedNonBlank;
  if (!format() || !mount()) {
    return InternalSecondaryFsBootResult::InitializationFailed;
  }
  return InternalSecondaryFsBootResult::InitializedBlank;
}

enum class InternalSecondaryFsRepairResult {
  Repaired,
  FormatFailed,
  MountFailed,
  ValidationFailed,
};

// Repair is deliberately separate from the normal boot path. A filesystem
// which mounts but fails a full metadata traversal may still be forensically
// recoverable, so erasing it requires an explicit local operator action.
template <typename Format, typename Mount, typename Validate>
InternalSecondaryFsRepairResult repairInternalSecondaryFilesystem(
    Format format, Mount mount, Validate validate) {
  if (!format()) return InternalSecondaryFsRepairResult::FormatFailed;
  if (!mount()) return InternalSecondaryFsRepairResult::MountFailed;
  if (!validate()) return InternalSecondaryFsRepairResult::ValidationFailed;
  return InternalSecondaryFsRepairResult::Repaired;
}

} // namespace storage
} // namespace mesh
