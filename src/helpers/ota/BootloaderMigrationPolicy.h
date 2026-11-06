#pragma once

#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include "OtaBootloaderUpdate.h"

namespace mesh {
namespace ota {
namespace migration {

static const uint32_t APP_CEILING = 0xED000u;
static const uint32_t BOOT_BASE = 0xF4000u;
static const uint32_t BOOT_SIZE = 0xA000u;
static const uint32_t PARAMS_BASE = 0xFE000u;
static const uint32_t SETTINGS_BASE = 0xFF000u;
static const uint32_t ACL_READ_DENIED = 1u << 2;
static const uint32_t ACL_WRITE_DENIED = 1u << 1;

enum class InstalledVariant { Unknown, Normal, Recovery };

inline bool boot_text_matches(const uint8_t *text, size_t available,
                              const char *expected, size_t length) {
  return available >= length && memcmp(text, expected, length) == 0;
}

// Recovery builds deliberately retain the normal board identity and BLM2
// flags. Their on-device version text is the only published discriminator.
// Older recovery images can omit that text entirely, so automatic installation
// also requires one complete, recognized normal INFO_UF2 version record.
// The caller must separately verify the image CRC, identity and platform.
inline InstalledVariant installed_variant(const uint8_t *image, size_t size,
                                          uint32_t version) {
  if (!image || size != BOOT_SIZE)
    return InstalledVariant::Unknown;
  static const char header[] = "UF2 Bootloader ";
  static const char recovery[] = "R_0x";
  static const char old_recovery[] = "-recovery-allow-all";
  static const char recovery_header[] = "UF2 Bootloader R_";
  for (size_t i = 0; i < size; ++i) {
    const size_t left = size - i;
    if (boot_text_matches(image + i, left, recovery, sizeof(recovery) - 1) ||
        boot_text_matches(image + i, left, old_recovery, sizeof(old_recovery) - 1) ||
        boot_text_matches(image + i, left, recovery_header, sizeof(recovery_header) - 1))
      return InstalledVariant::Recovery;
  }
  if (!ota_bootloader_version_valid(version)) return InstalledVariant::Unknown;
  char normal[48], packed[16];
  const unsigned preview = version & 0xFFu;
  snprintf(normal, sizeof(normal),
           preview == 0xFFu ? "0.11.0-OTAFIX%u.%u.%u\r\n" :
                              "0.11.0-OTAFIX%u.%u.%u-preview.%u\r\n",
           unsigned(version >> 24), unsigned((version >> 16) & 0xFFu),
           unsigned((version >> 8) & 0xFFu), preview);
  snprintf(packed, sizeof(packed), "BL_0x%08lX\r\n", (unsigned long)version);
  unsigned records = 0;
  for (size_t i = 0; i + sizeof(header) - 1 < size; ++i) {
    if (!boot_text_matches(image + i, size - i, header, sizeof(header) - 1)) continue;
    const uint8_t *label = image + i + sizeof(header) - 1;
    size_t left = size - i - (sizeof(header) - 1);
    bool matches = boot_text_matches(label, left, packed, strlen(packed));
    if (*label == 'v') { ++label; --left; }
    if (!matches && !boot_text_matches(label, left, normal, strlen(normal)))
      return InstalledVariant::Unknown;
    if (++records > 1) return InstalledVariant::Unknown;
  }
  return records == 1 ? InstalledVariant::Normal : InstalledVariant::Unknown;
}

struct AclRegion {
  uint32_t address;
  uint32_t size;
  uint32_t permissions;
};

struct Snapshot {
  uint32_t part;
  uint32_t flash_page_size;
  uint32_t flash_pages;
  uint32_t softdevice_size;
  uint16_t softdevice_fwid;
  uint32_t app_base;
  uint32_t app_ceiling;
  uint16_t expected_softdevice_fwid;
  uint32_t mbr_boot_override;
  uint32_t mbr_params_override;
  uint32_t uicr_boot;
  uint32_t uicr_params;
  uint32_t source;
  uint32_t source_size;
  uint32_t app_body_size;
  uint32_t app_image_size;
  AclRegion acl[8];
};

enum class Gate { Ok, Chip, Softdevice, BootAddress, ParamsAddress, Source, Protection };

inline bool overlaps(uint32_t start, uint32_t size, uint32_t lo, uint32_t hi) {
  return size && uint64_t(start) < hi && uint64_t(start) + size > lo;
}

inline Gate preflight(const Snapshot &s) {
  if (s.part != 0x52840u || s.flash_page_size != 4096u || s.flash_pages != 256u) return Gate::Chip;
  if ((s.app_base != 0x26000u && s.app_base != 0x27000u) ||
      s.app_ceiling > APP_CEILING || s.app_ceiling <= s.app_base ||
      s.softdevice_size != s.app_base || s.softdevice_fwid != s.expected_softdevice_fwid ||
      (s.app_base == 0x26000u ? s.softdevice_fwid != 0xB6u : s.softdevice_fwid != 0x123u))
    return Gate::Softdevice;
  const uint32_t boot = s.mbr_boot_override == UINT32_MAX ? s.uicr_boot : s.mbr_boot_override;
  const uint32_t params = s.mbr_params_override == UINT32_MAX ? s.uicr_params : s.mbr_params_override;
  if (boot != BOOT_BASE) return Gate::BootAddress;
  if (params != PARAMS_BASE) return Gate::ParamsAddress;
  // The source is a linked application resource, never SRAM, external memory,
  // a filesystem, the live bootloader, or a dynamically chosen scratch bank.
  if (s.source_size != BOOT_SIZE || (s.source & 3u) || s.source < s.app_base || !s.app_body_size ||
      s.app_body_size > s.app_ceiling - s.app_base ||
      s.app_image_size != uint64_t(s.app_body_size) + ENDF_LEN ||
      s.app_image_size > s.app_ceiling - s.app_base ||
      uint64_t(s.source) + BOOT_SIZE > uint64_t(s.app_base) + s.app_body_size)
    return Gate::Source;
  for (const auto &r : s.acl) {
    // nRF52840 ACL bit 2 denies reads; bit 1 denies writes. COPY_BL also needs its
    // parameter page writable. Never try to unlock an existing ACL region.
    if ((r.permissions & ACL_READ_DENIED) &&
        (overlaps(r.address, r.size, s.app_base, s.app_base + s.app_body_size) ||
         overlaps(r.address, r.size, BOOT_BASE, SETTINGS_BASE)))
      return Gate::Protection;
    if ((r.permissions & ACL_WRITE_DENIED) && overlaps(r.address, r.size, BOOT_BASE, SETTINGS_BASE))
      return Gate::Protection;
  }
  return Gate::Ok;
}

inline bool candidate_matches_resource(const OtaBootloaderIdentity &candidate,
                                      uint32_t board_id, const char name[16],
                                      uint32_t version, uint32_t app_base, uint16_t fwid) {
  return ota_bootloader_candidate_identity_canonical(candidate) &&
         candidate.board_id == board_id && memcmp(candidate.device_name, name, 16) == 0 &&
         candidate.boot_version == version &&
         ota_bootloader_continuity_matches_runtime(candidate, 140, fwid, app_base, 1);
}

inline bool installed_can_migrate(const OtaBootloaderIdentity &installed,
                                 const OtaBootloaderIdentity &candidate,
                                 bool has_legacy, bool legacy_hash_matches,
                                 uint32_t app_base, uint16_t fwid) {
  // The installed identity describes the bootloader, not the physical XIAO.
  // Base and Sense modules can run the same application and can have had the
  // other variant's bootloader installed already. No independent hardware
  // discriminator is available here, so never replace either XIAO loader.
  if (!ota_bootloader_install_identity_unambiguous(candidate)) return false;
  if (!installed.present)
    return has_legacy && legacy_hash_matches &&
           ota_bootloader_continuity_matches_runtime(candidate, 140, fwid, app_base, 1);
  return installed.crc_ok && installed.continuity_present &&
         ota_bootloader_identity_matches(installed, candidate) &&
         ota_bootloader_continuity_gate(installed, candidate, candidate.boot_version,
                                       140, fwid, app_base, 1) == OTA_BOOT_CONTINUITY_OK;
}

inline bool installed_matches_resource(const OtaBootloaderIdentity &installed,
                                       uint32_t board_id, const char name[16],
                                       uint32_t app_base, uint16_t fwid) {
  return installed.present && installed.crc_ok &&
         installed.image_start == BOOT_BASE && installed.image_size == BOOT_SIZE &&
         installed.board_id == board_id && memcmp(installed.device_name, name, 16) == 0 &&
         ota_bootloader_continuity_matches_runtime(installed, 140, fwid, app_base, 1);
}

inline bool legacy_digest_allowed(const uint8_t digest[32],
                                  const uint8_t (*approved)[32], size_t count) {
  if (!digest || !approved || count > 32u) return false;
  for (size_t i = 0; i < count; ++i)
    if (memcmp(digest, approved[i], 32) == 0) return true;
  return false;
}

// Automatic, manual arming and the final pre-copy gate use the same source
// policy. Stock names/versions and shared USB IDs are never authorization.
// Valid modern metadata cannot fall through to the legacy exception, even
// when its board, continuity, CRC or version record does not match.
inline bool installed_source_allowed(const OtaBootloaderIdentity &installed,
                                     InstalledVariant variant, bool legacy_hash_matches,
                                     uint32_t board_id, const char name[16],
                                     uint32_t app_base, uint16_t fwid) {
  if (!ota_bootloader_install_identity_unambiguous(board_id,
          reinterpret_cast<const uint8_t *>(name))) return false;
  if (variant == InstalledVariant::Recovery) return false;
  if (installed.present)
    return variant == InstalledVariant::Normal &&
           installed_matches_resource(installed, board_id, name, app_base, fwid);
  return legacy_hash_matches;
}

inline bool installed_up_to_date(const OtaBootloaderIdentity &installed,
                                 uint32_t board_id, const char name[16],
                                 uint32_t version, uint32_t app_base, uint16_t fwid) {
  return installed_matches_resource(installed, board_id, name, app_base, fwid) &&
         installed.boot_version >= version;
}

} // namespace migration
} // namespace ota
} // namespace mesh
