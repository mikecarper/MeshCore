#pragma once

#include <stddef.h>
#include <stdint.h>
#include <string.h>

// Read the bootloader's OTA capability marker so the app can decide, BEFORE staging+approving+rebooting,
// whether THIS device's bootloader can actually apply a `.mota`. Without this the app would reboot into a
// bootloader that silently can't apply (stock Adafruit, or an OLDER OTAFIX predating a `.mota` format
// change) and the device would just come back up unchanged.
//
// Mirror of Adafruit_nRF52_Bootloader_OTAFIX/src/ota_bl_info.h - keep byte-identical.
// nRF52 only (the bootloader flash is memory-mapped + readable by the app); a no-op elsewhere.

#if defined(NRF52_PLATFORM)
  #include "OtaFlashLayout_nrf52.h"
#endif

namespace mesh {
namespace ota {

// 16-byte marker: magic[8] "MOTABLDR" + apply_abi(2) + codec_mask(2) + storage flags/reserved(4).
static const uint8_t OTA_BL_MAGIC[8] = { 'M','O','T','A','B','L','D','R' };

struct OtaBlCaps {
  bool     present = false;
  uint16_t apply_abi = 0;    // max .mota format_ver the bootloader can apply
  uint16_t codec_mask = 0;   // bit i set => can apply codec_id i (in-place delta = bit 2)
  uint8_t  storage_flags = 0; // OTA_BL_STORAGE_* capability bits
};

static const uint8_t OTA_BL_STORAGE_SD            = 0x01;
static const uint8_t OTA_BL_STORAGE_STAGE_CEILING = 0x02;
static const uint8_t OTA_BL_STORAGE_QSPI          = 0x04;
static const uint8_t OTA_BL_STORAGE_BOOT_UPDATE    = 0x08;
static const uint8_t OTA_BL_STORAGE_KNOWN          = 0x0F;
static const uint8_t OTA_BL_PROFILE_SD_BOOT_UPDATE =
    OTA_BL_STORAGE_SD | OTA_BL_STORAGE_BOOT_UPDATE;
static const uint8_t OTA_BL_PROFILE_INTERNAL_BOOT_UPDATE =
    OTA_BL_STORAGE_STAGE_CEILING | OTA_BL_STORAGE_BOOT_UPDATE;
static const uint8_t OTA_BL_PROFILE_QSPI_BOOT_UPDATE =
    OTA_BL_STORAGE_STAGE_CEILING | OTA_BL_STORAGE_QSPI |
    OTA_BL_STORAGE_BOOT_UPDATE;
// A successor must retain both application update paths used by qualified
// external stores: bit 0 CODEC_FULL and bit 2 CODEC_DETOOLS_INPLACE.
static const uint16_t OTA_BL_REQUIRED_APP_CODEC_MASK = 0x0005u;

inline uint8_t ota_bootloader_update_storage_flags() {
#if defined(OTA_INTERNAL_BOOTLOADER_UPDATE)
  // No external-storage bit means the ordinary internal flash store. The
  // same store holds either an app delta or a boot package; BOOT_UPDATE marks
  // only the privileged package capability, not a second storage backend.
  return OTA_BL_PROFILE_INTERNAL_BOOT_UPDATE;
#elif defined(OTA_QSPI_BOOTLOADER_UPDATE)
  return OTA_BL_PROFILE_QSPI_BOOT_UPDATE;
#elif defined(OTA_SD_BOOTLOADER_UPDATE)
  // The complete container remains in the contiguous SD staging file. No
  // internal stage-ceiling capability is implied by this external store.
  return OTA_BL_PROFILE_SD_BOOT_UPDATE;
#else
  return 0;
#endif
}

inline bool ota_bootloader_self_update_caps_valid(const OtaBlCaps& c) {
  const uint8_t required = ota_bootloader_update_storage_flags();
  return required != 0 && c.present && c.apply_abi >= 3u &&
         (c.codec_mask & OTA_BL_REQUIRED_APP_CODEC_MASK) ==
             OTA_BL_REQUIRED_APP_CODEC_MASK &&
         c.storage_flags == required;
}

// Prefer a continuity-capable marker over a numerically newer legacy-looking candidate. Bootloader
// images contain the marker magic in code/literal data as well as in the real structure, so the scan
// must not let a later plausible literal shadow the privileged marker merely because its following
// bytes decode as a larger ABI.
inline bool ota_bl_caps_prefer(const OtaBlCaps& current, uint16_t candidate_abi,
                               uint8_t candidate_storage, bool require_boot_update) {
  if (!current.present) return true;
  if (require_boot_update) {
    const bool candidate_boot = (candidate_storage & OTA_BL_STORAGE_BOOT_UPDATE) != 0;
    const bool current_boot = (current.storage_flags & OTA_BL_STORAGE_BOOT_UPDATE) != 0;
    if (candidate_boot != current_boot) return candidate_boot;
  }
  return candidate_abi > current.apply_abi;
}

// Scan a region whose offset zero is word-aligned. The marker structure contains
// uint16_t fields and OTAFIX emits it aligned(4); accepting a byte-shifted magic
// occurrence would let instruction/literal bytes masquerade as capabilities.
inline OtaBlCaps ota_bl_caps_scan_aligned(const uint8_t* bytes, size_t len,
                                          bool require_boot_update,
                                          uint8_t exact_storage_flags = 0) {
  OtaBlCaps c;
  if (!bytes) return c;
  uint8_t privileged_count = 0;
  bool exact_privileged = false;
  for (size_t off = 0; off + 16u <= len; off += 4u) {
    const uint8_t* p = bytes + off;
    if (p[0] != OTA_BL_MAGIC[0] || memcmp(p, OTA_BL_MAGIC, 8) != 0) continue;
    const uint16_t abi = (uint16_t)(p[8] | ((uint16_t)p[9] << 8));
    const uint16_t codecs = (uint16_t)(p[10] | ((uint16_t)p[11] << 8));
    const uint8_t storage = p[12];
    if (abi == 0 || abi == 0xFFFFu || codecs == 0 || (storage & ~OTA_BL_STORAGE_KNOWN) != 0 ||
        p[13] != 0 || p[14] != 0 || p[15] != 0) continue;
    const bool exact_profile = exact_storage_flags == 0 || storage == exact_storage_flags;
    if (require_boot_update && abi >= 3u &&
        (codecs & OTA_BL_REQUIRED_APP_CODEC_MASK) == OTA_BL_REQUIRED_APP_CODEC_MASK &&
        (storage & OTA_BL_STORAGE_BOOT_UPDATE) != 0) {
      // A self-update build must have one unambiguous privileged marker. Do
      // not silently choose between two otherwise valid structures, even if
      // one claims a different known storage profile (or only BOOT_UPDATE).
      // Malformed magic/literal decoys were filtered above and do not count.
      if (++privileged_count != 1u) return OtaBlCaps();
      if (exact_profile) {
        exact_privileged = true;
        c.present = true;
        c.apply_abi = abi;
        c.codec_mask = codecs;
        c.storage_flags = storage;
      }
      continue;
    }
    if (ota_bl_caps_prefer(c, abi, storage, require_boot_update)) {
      c.present = true;
      c.apply_abi = abi;
      c.codec_mask = codecs;
      c.storage_flags = storage;
    }
  }
  if (require_boot_update &&
      (privileged_count != 1u || !exact_privileged)) return OtaBlCaps();
  return c;
}

// Scan the bootloader flash region for the marker. Returns {present=false} if not found / non-nRF52.
inline OtaBlCaps ota_bootloader_caps() {
#if defined(NRF52_PLATFORM)
  const uint8_t* lo = (const uint8_t*)(uintptr_t)MOTA_NRF52_BL_START;
  const uint8_t* hi = (const uint8_t*)(uintptr_t)MOTA_NRF52_BL_END;
  return ota_bl_caps_scan_aligned(lo, (size_t)(hi - lo),
#if defined(OTA_QSPI_BOOTLOADER_UPDATE) || defined(OTA_INTERNAL_BOOTLOADER_UPDATE) || \
    defined(OTA_SD_BOOTLOADER_UPDATE)
                                  true, ota_bootloader_update_storage_flags()
#else
                                  false
#endif
                                  );
#else
  return OtaBlCaps();
#endif
}

// True if this device's bootloader can apply a .mota of the given format_ver + codec_id.
inline bool ota_bootloader_can_apply(uint8_t format_ver, uint8_t codec_id) {
  OtaBlCaps c = ota_bootloader_caps();
  return c.present && c.apply_abi >= format_ver && (c.codec_mask & (1u << codec_id)) != 0;
}

#if defined(NRF52_PLATFORM)
// Use the layout's larger ceiling only when the installed bootloader advertises the matching
// GPREGRET2 handoff. New applications remain filesystem-safe with older OTAFIX bootloaders by retaining
// the legacy 0xD4000 ceiling. Packages sized for the expanded window still require the newer bootloader.
inline uint32_t ota_nrf52_effective_stage_ceiling(const OtaBlCaps& c) {
  const uint32_t desired = mota_nrf52_layout_stage_ceiling();
#if defined(OTA_INTERNAL_BOOTLOADER_UPDATE)
  if (desired != MOTA_NRF52_STAGE_CEILING_EXPANDED ||
      !ota_bootloader_self_update_caps_valid(c))
    return MOTA_NRF52_STAGE_CEILING_LEGACY;
  return desired;
#else
  if (desired == MOTA_NRF52_STAGE_CEILING_EXPANDED) {
    if (!c.present || !(c.storage_flags & OTA_BL_STORAGE_STAGE_CEILING))
      return MOTA_NRF52_STAGE_CEILING_LEGACY;
  }
  return desired;
#endif
}

inline uint32_t ota_nrf52_effective_stage_ceiling() {
  return ota_nrf52_effective_stage_ceiling(ota_bootloader_caps());
}
#endif

} // namespace ota
} // namespace mesh
