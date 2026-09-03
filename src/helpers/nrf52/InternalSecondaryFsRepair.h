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

// Read flash through an injected word reader so the erased-media decision can
// be covered by native tests. Refuse malformed/empty ranges: only a complete,
// word-aligned scan can authorize a boot-time format.
template <typename ReadWord>
bool isErasedFlashRange(uint32_t start, uint32_t size, ReadWord read_word) {
  if (size == 0 || (start % sizeof(uint32_t)) != 0
      || (size % sizeof(uint32_t)) != 0) {
    return false;
  }
  for (uint32_t offset = 0; offset < size; offset += sizeof(uint32_t)) {
    if (read_word(start + offset) != 0xFFFFFFFFUL) return false;
  }
  return true;
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

// The caller must authorize and bound the erase. In particular, automatic
// recovery is only for the reserved secondary region, never identity storage.
template <typename Format, typename Mount, typename Validate>
InternalSecondaryFsRepairResult repairInternalSecondaryFilesystem(
    Format format, Mount mount, Validate validate) {
  if (!format()) return InternalSecondaryFsRepairResult::FormatFailed;
  if (!mount()) return InternalSecondaryFsRepairResult::MountFailed;
  if (!validate()) return InternalSecondaryFsRepairResult::ValidationFailed;
  return InternalSecondaryFsRepairResult::Repaired;
}

enum class InternalSecondaryFsRecoveryResult {
  Ready,
  Remounted,
  Reinitialized,
  Failed,
};

// A failed initial mount/traversal may be transient. Retry non-destructively
// before rebuilding, and attempt the destructive repair at most once. Repair
// must include its own mount and validation before reporting success.
template <typename IsReady, typename Remount, typename Repair>
InternalSecondaryFsRecoveryResult recoverInternalSecondaryFilesystem(
    IsReady is_ready, Remount remount, Repair repair) {
  if (is_ready()) return InternalSecondaryFsRecoveryResult::Ready;
  if (remount() && is_ready()) {
    return InternalSecondaryFsRecoveryResult::Remounted;
  }
  if (repair()) return InternalSecondaryFsRecoveryResult::Reinitialized;
  return InternalSecondaryFsRecoveryResult::Failed;
}

} // namespace storage
} // namespace mesh
