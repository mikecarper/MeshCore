#pragma once

#if defined(NRF52_PLATFORM)

#include <InternalFileSystem.h>
#include <flash/flash_nrf5x.h>
#include <helpers/IdentityStore.h>
#include <helpers/nrf52/InternalSecondaryFsRepair.h>

namespace mesh {
namespace storage {

#if defined(NRF52840_XXAA)
static const uint32_t INTERNAL_PRIMARY_FS_START = 0xED000UL;
#else
static const uint32_t INTERNAL_PRIMARY_FS_START = 0x6D000UL;
#endif
static const uint32_t INTERNAL_PRIMARY_FS_SIZE =
    7UL * FLASH_NRF52_PAGE_SIZE;
static const uint8_t INTERNAL_PRIMARY_FS_MOUNT_ATTEMPTS = 3;

// InternalFileSystem::begin() formats after one failed mount. Identity storage
// instead gets several non-destructive mount attempts and is formatted only
// when every word in the complete reserved range is still erased.
inline InternalSecondaryFsBootResult beginInternalPrimaryFilesystemSafely(
    InternalFileSystem& fs, struct lfs_config* config = nullptr) {
  const auto mount_with_retries = [&fs, config]() -> bool {
    for (uint8_t attempt = 0; attempt < INTERNAL_PRIMARY_FS_MOUNT_ATTEMPTS;
         ++attempt) {
      if (attempt != 0) fs.end();
      const bool mounted = config == nullptr
          ? fs.Adafruit_LittleFS::begin()
          : fs.Adafruit_LittleFS::begin(config);
      if (mounted) return true;
    }
    return false;
  };

  InternalSecondaryFsBootResult result = prepareInternalSecondaryFilesystem(
      mount_with_retries,
      []() -> bool {
        return isErasedFlashRange(
            INTERNAL_PRIMARY_FS_START, INTERNAL_PRIMARY_FS_SIZE,
            [](uint32_t address) -> uint32_t {
              return *reinterpret_cast<const volatile uint32_t*>(address);
            });
      },
      [&fs]() -> bool {
        fs.end();
        return fs.format();
      });
  if (result == InternalSecondaryFsBootResult::Mounted
      || result == InternalSecondaryFsBootResult::InitializedBlank) {
    return result;
  }

  // A node which can never leave startup is not useful. Give transient flash
  // or SoftDevice contention three widely spaced chances to clear before the
  // explicitly authorized last resort: erase the unusable primary store.
  for (uint8_t retry = 0; retry < 3; ++retry) {
    delay(3000);
    fs.end();
    const bool mounted = config == nullptr
        ? fs.Adafruit_LittleFS::begin()
        : fs.Adafruit_LittleFS::begin(config);
    if (mounted) return InternalSecondaryFsBootResult::Mounted;
  }

  fs.end();
  if (!fs.format() || !mount_with_retries()) {
    return InternalSecondaryFsBootResult::InitializationFailed;
  }
  return InternalSecondaryFsBootResult::ReinitializedUnreadable;
}

inline bool internalPrimaryFilesystemReady(
    InternalSecondaryFsBootResult result) {
  return result == InternalSecondaryFsBootResult::Mounted
      || result == InternalSecondaryFsBootResult::InitializedBlank
      || result == InternalSecondaryFsBootResult::ReinitializedUnreadable;
}

template <typename LoadIdentity>
IdentityLoadResult loadIdentityWithPrimaryRecovery(
    InternalFileSystem& fs, LoadIdentity load_identity,
    struct lfs_config* config = nullptr) {
  IdentityLoadResult result = load_identity();
  if (result != IdentityLoadResult::Unreadable) return result;

  for (uint8_t retry = 0; retry < 3; ++retry) {
    delay(3000);
    result = load_identity();
    if (result != IdentityLoadResult::Unreadable) return result;
  }

  fs.end();
  if (!fs.format()) return IdentityLoadResult::Unreadable;
  for (uint8_t attempt = 0; attempt < INTERNAL_PRIMARY_FS_MOUNT_ATTEMPTS;
       ++attempt) {
    if (attempt != 0) fs.end();
    const bool mounted = config == nullptr
        ? fs.Adafruit_LittleFS::begin()
        : fs.Adafruit_LittleFS::begin(config);
    if (mounted) return load_identity();
  }
  return IdentityLoadResult::Unreadable;
}

} // namespace storage
} // namespace mesh

#endif // NRF52_PLATFORM
