#pragma once

// Shared OTA flash-layout constants for the nRF52840 single-slot delta-apply path.
//
// The running app occupies [APP_BASE, app_end]. Internal ExtraFS, when actually used, starts at
// 0xD4000; primary InternalFS starts at 0xED000. MeshCore derives the safe staging ceiling from the
// linked application region and secondary-storage type, bottom-aligns the `.mota` below it, and passes
// the choice to a capable bootloader in GPREGRET2. APP_BASE is obtained from the linker so S140 v6
// (0x26000) and v7 (0x27000) both work. The bootloader independently uses DFU_BANK_0_REGION_START.

#include <stdint.h>

namespace mesh {
namespace ota {

static const uint32_t MOTA_NRF52_APP_BASE_S140_V6 = 0x00026000u;
static const uint32_t MOTA_NRF52_APP_BASE_S140_V7 = 0x00027000u;
#if defined(NRF52_PLATFORM)
extern "C" uint32_t __flash_arduino_start[];       // nrf52_common.ld: ORIGIN(FLASH)
extern "C" uint32_t __flash_arduino_end[];         // nrf52_common.ld: ORIGIN(FLASH) + LENGTH(FLASH)
extern "C" const uint8_t g_meshcore_internal_extrafs __attribute__((weak));
inline uint32_t mota_nrf52_app_base() {
  return (uint32_t)(uintptr_t)__flash_arduino_start;
}
#else
// Native geometry tests have no linker script; default their runtime helper to the v6 layout.
inline uint32_t mota_nrf52_app_base() { return MOTA_NRF52_APP_BASE_S140_V6; }
#endif
static const uint32_t MOTA_NRF52_EXTRAFS_START = 0x000D4000u;
static const uint32_t MOTA_NRF52_APP_END        = 0x000ED000u;
static const uint32_t MOTA_NRF52_STAGE_CEILING_LEGACY   = MOTA_NRF52_EXTRAFS_START;
static const uint32_t MOTA_NRF52_STAGE_CEILING_EXPANDED = MOTA_NRF52_APP_END;
// Compatibility name for code that refers to the fixed lower boundary. New staging code must use an
// explicit ceiling so it cannot accidentally cross an Internal ExtraFS.
static const uint32_t MOTA_NRF52_FS_START   = MOTA_NRF52_EXTRAFS_START;
static const uint32_t MOTA_NRF52_FLASH_PAGE = 4096u;
static const uint8_t  GPREGRET_OTA_APPLY    = 0x6Au;        // distinct from DFU magics 0x57/0x4E/0xA8
static const uint8_t  GPREGRET2_OTA_STAGE_LEGACY   = 0xD4u;
static const uint8_t  GPREGRET2_OTA_STAGE_EXPANDED = 0xEDu;

// Firmware without a valid EndF and older host tooling fall back to this conservative apply workspace.
// New motatool builds read the firmware's appended layout record and derive memory_size from the actual
// staged-container start; the app and bootloader both validate that per-patch value before writing.
static const uint32_t MOTA_NRF52_FALLBACK_INPLACE_MEMORY = 0x00098000u;  // 608 KB

// Bootloader flash region (nRF52840: 39 KB ending just below the CF2/MBR-params pages). The app scans
// this for the bootloader capability marker (OtaBlInfo.h) to know whether THIS device's bootloader can
// actually apply a .mota before staging+approving+rebooting.
static const uint32_t MOTA_NRF52_BL_START = 0x000F4000u;
static const uint32_t MOTA_NRF52_BL_END   = 0x000FE000u;

// Compile-time layout-ordering invariants. If a constant above is edited inconsistently these fail the
// BUILD rather than silently letting a stage/apply corrupt the filesystem (user prefs) or the app.
static_assert((MOTA_NRF52_APP_BASE_S140_V6 % MOTA_NRF52_FLASH_PAGE) == 0, "S140 v6 base must be page-aligned");
static_assert((MOTA_NRF52_APP_BASE_S140_V7 % MOTA_NRF52_FLASH_PAGE) == 0, "S140 v7 base must be page-aligned");
static_assert((MOTA_NRF52_STAGE_CEILING_LEGACY % MOTA_NRF52_FLASH_PAGE) == 0,
              "legacy staging ceiling must be page-aligned");
static_assert((MOTA_NRF52_STAGE_CEILING_EXPANDED % MOTA_NRF52_FLASH_PAGE) == 0,
              "expanded staging ceiling must be page-aligned");
static_assert(MOTA_NRF52_STAGE_CEILING_LEGACY < MOTA_NRF52_STAGE_CEILING_EXPANDED,
              "legacy staging ceiling must precede expanded ceiling");
static_assert(MOTA_NRF52_STAGE_CEILING_EXPANDED < MOTA_NRF52_BL_START,
              "staging and filesystems must end below the bootloader");
static_assert(MOTA_NRF52_BL_START < MOTA_NRF52_BL_END,    "bootloader region must be non-empty");
static_assert(MOTA_NRF52_APP_BASE_S140_V7 + MOTA_NRF52_FALLBACK_INPLACE_MEMORY <=
                  MOTA_NRF52_STAGE_CEILING_LEGACY,
              "fallback workspace must end at or below the legacy staging ceiling");

// Pure policy helper. Actual internal ExtraFS use always wins and keeps the legacy ceiling. Otherwise
// either supported linker region may safely reclaim through InternalFS at 0xED000. This handles
// repeaters, XIAO/QSPI companions, and future boards without maintaining a board-name allowlist. Unknown
// linker geometry falls back to the legacy ceiling.
inline uint32_t mota_nrf52_stage_ceiling_for_layout(uint32_t linked_app_end,
                                                     bool uses_internal_extrafs) {
  if (uses_internal_extrafs) return MOTA_NRF52_STAGE_CEILING_LEGACY;
  if (linked_app_end == MOTA_NRF52_APP_END || linked_app_end == MOTA_NRF52_EXTRAFS_START)
    return MOTA_NRF52_STAGE_CEILING_EXPANDED;
  return MOTA_NRF52_STAGE_CEILING_LEGACY;
}

inline uint32_t mota_nrf52_layout_stage_ceiling() {
#if defined(NRF52_PLATFORM)
  // DataStore.cpp defines this symbol only for a companion that really constructs internal ExtraFS.
  // An undefined weak symbol has address zero, so other roles do not inherit nrf52_base's broad EXTRAFS
  // feature define as a false-positive storage reservation.
  const bool uses_internal_extrafs = (uintptr_t)&g_meshcore_internal_extrafs != 0u;
  return mota_nrf52_stage_ceiling_for_layout((uint32_t)(uintptr_t)__flash_arduino_end,
                                              uses_internal_extrafs);
#else
  // Native callers use the explicit-ceiling helpers below; keep the no-argument fallback conservative.
  return MOTA_NRF52_STAGE_CEILING_LEGACY;
#endif
}

inline bool mota_nrf52_stage_ceiling_valid(uint32_t stage_ceiling) {
  return stage_ceiling == MOTA_NRF52_STAGE_CEILING_LEGACY ||
         stage_ceiling == MOTA_NRF52_STAGE_CEILING_EXPANDED;
}

inline bool mota_nrf52_layout_valid(uint32_t app_base, uint32_t stage_ceiling) {
  return mota_nrf52_stage_ceiling_valid(stage_ceiling) &&
         (app_base % MOTA_NRF52_FLASH_PAGE) == 0 && app_base < stage_ceiling;
}

inline bool mota_nrf52_layout_valid(uint32_t app_base) {
  return mota_nrf52_layout_valid(app_base, mota_nrf52_layout_stage_ceiling());
}

inline uint32_t mota_nrf52_stage_capacity(uint32_t app_base, uint32_t app_end,
                                           uint32_t stage_ceiling) {
  if (!mota_nrf52_layout_valid(app_base, stage_ceiling) || app_end < app_base ||
      app_end > stage_ceiling || app_end > UINT32_MAX - (MOTA_NRF52_FLASH_PAGE - 1u)) return 0;
  const uint32_t first_free_page =
      (app_end + MOTA_NRF52_FLASH_PAGE - 1u) & ~(MOTA_NRF52_FLASH_PAGE - 1u);
  return first_free_page <= stage_ceiling ? stage_ceiling - first_free_page : 0;
}

// Plan where to stage a received `.mota` of `total_size` bytes. It is placed bottom-aligned within the
// highest flash page below stage_ceiling (the bootloader scans downward from there), and it must sit
// ENTIRELY above the running image and below the filesystem region (ExtraFS/InternalFS - where user prefs
// live, assumed immutable). The apply path separately verifies the patch's own detools memory_size ends
// at or below this planned start before it marks the package approved.
// Returns false (and leaves out_start untouched) if it does not fit. This is the SINGLE place the FS
// ceiling + app-collision bounds are enforced; begin()/reopen() both go through it. Pure - no flash I/O -
// so it is unit-tested natively in test/test_ota/test_ota_flashplan.cpp.
inline bool mota_nrf52_stage_plan(uint32_t total_size, uint32_t app_base, uint32_t app_end,
                                  uint32_t stage_ceiling, uint32_t& out_start) {
  if (!mota_nrf52_layout_valid(app_base, stage_ceiling) || app_end < app_base ||
      app_end > stage_ceiling) return false;
  const uint32_t capacity = stage_ceiling - app_base;
  if (total_size < 13 || total_size > capacity) return false; // 13 = header(8)+trailer(5)
  uint32_t start = (stage_ceiling - total_size) & ~(MOTA_NRF52_FLASH_PAGE - 1);   // bottom-align down
  if (start < app_end) return false;                            // would overlap the running app
  out_start = start;
  return true;
}

inline bool mota_nrf52_stage_plan(uint32_t total_size, uint32_t app_base, uint32_t app_end,
                                  uint32_t& out_start) {
  return mota_nrf52_stage_plan(total_size, app_base, app_end,
                               mota_nrf52_layout_stage_ceiling(), out_start);
}

} // namespace ota
} // namespace mesh
