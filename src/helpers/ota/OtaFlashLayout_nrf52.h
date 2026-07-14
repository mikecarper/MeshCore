#pragma once

// Shared OTA flash-layout constants for the nRF52840 single-slot delta-apply path.
//
// The running app occupies [APP_BASE, app_end]; the primary LittleFS (InternalFS) starts at FS_START.
// MeshCore stages a verified+approved `.mota` in the free flash below FS_START (bottom-aligned), then
// sets GPREGRET_OTA_APPLY and resets; the bootloader scans [APP_BASE, FS_START) for it and applies it
// in place. APP_BASE is obtained from the linker so S140 v6 (0x26000) and v7 (0x27000) both work. The
// bootloader independently uses DFU_BANK_0_REGION_START, which resolves to the same address.

#include <stdint.h>

namespace mesh {
namespace ota {

static const uint32_t MOTA_NRF52_APP_BASE_S140_V6 = 0x00026000u;
static const uint32_t MOTA_NRF52_APP_BASE_S140_V7 = 0x00027000u;
#if defined(NRF52_PLATFORM)
extern "C" uint32_t __flash_arduino_start[];       // nrf52_common.ld: ORIGIN(FLASH)
inline uint32_t mota_nrf52_app_base() {
  return (uint32_t)(uintptr_t)__flash_arduino_start;
}
#else
// Native geometry tests have no linker script; default their runtime helper to the v6 layout.
inline uint32_t mota_nrf52_app_base() { return MOTA_NRF52_APP_BASE_S140_V6; }
#endif
// Staging ceiling: the lowest filesystem region above the app. nRF52840 ExtraFS linker scripts place
// ExtraFS at 0xD4000..0xED000 (and InternalFS at 0xED000), while default scripts leave that range free.
// Staging below 0xD4000 therefore never touches either filesystem.
static const uint32_t MOTA_NRF52_FS_START   = 0x000D4000u;  // ExtraFS start (universal staging ceiling)
static const uint32_t MOTA_NRF52_FLASH_PAGE = 4096u;
static const uint8_t  GPREGRET_OTA_APPLY    = 0x6Au;        // distinct from DFU magics 0x57/0x4E/0xA8

// In-place patches are built with --inplace-memory = this (the apply workspace, from APP_BASE up).
// It must hold the new image (~520 KB) yet leave the staged mota room below FS_START: workspace ends
// at APP_BASE+this = 0xBE000 (S140 v6) or 0xBF000 (v7), leaving 88/84 KB for the staged delta. The
// bootloader also bounds writes to < the (scanned) mota start, so a mis-sized memory still fails safe.
static const uint32_t MOTA_NRF52_INPLACE_MEMORY = 0x00098000u;  // 608 KB

// Bootloader flash region (nRF52840: 39 KB ending just below the CF2/MBR-params pages). The app scans
// this for the bootloader capability marker (OtaBlInfo.h) to know whether THIS device's bootloader can
// actually apply a .mota before staging+approving+rebooting.
static const uint32_t MOTA_NRF52_BL_START = 0x000F4000u;
static const uint32_t MOTA_NRF52_BL_END   = 0x000FE000u;

// Compile-time layout-ordering invariants. If a constant above is edited inconsistently these fail the
// BUILD rather than silently letting a stage/apply corrupt the filesystem (user prefs) or the app.
static_assert((MOTA_NRF52_APP_BASE_S140_V6 % MOTA_NRF52_FLASH_PAGE) == 0, "S140 v6 base must be page-aligned");
static_assert((MOTA_NRF52_APP_BASE_S140_V7 % MOTA_NRF52_FLASH_PAGE) == 0, "S140 v7 base must be page-aligned");
static_assert((MOTA_NRF52_FS_START % MOTA_NRF52_FLASH_PAGE) == 0, "FS_START must be page-aligned");
static_assert(MOTA_NRF52_FS_START < MOTA_NRF52_BL_START,  "staging (+FS) must end below the bootloader");
static_assert(MOTA_NRF52_BL_START < MOTA_NRF52_BL_END,    "bootloader region must be non-empty");
// The in-place apply workspace [APP_BASE, APP_BASE+INPLACE_MEMORY) must end at/below the staging ceiling,
// so an in-place apply never writes into ExtraFS/InternalFS (where user prefs live).
static_assert(MOTA_NRF52_APP_BASE_S140_V7 + MOTA_NRF52_INPLACE_MEMORY <= MOTA_NRF52_FS_START,
              "in-place apply workspace must end at or below the staging ceiling");

inline bool mota_nrf52_layout_valid(uint32_t app_base) {
  return (app_base % MOTA_NRF52_FLASH_PAGE) == 0 && app_base < MOTA_NRF52_FS_START &&
         (uint64_t)app_base + MOTA_NRF52_INPLACE_MEMORY <= MOTA_NRF52_FS_START;
}

inline uint32_t mota_nrf52_stage_capacity(uint32_t app_base) {
  return mota_nrf52_layout_valid(app_base)
      ? MOTA_NRF52_FS_START - (app_base + MOTA_NRF52_INPLACE_MEMORY) : 0;
}

// Plan where to stage a received `.mota` of `total_size` bytes. It is placed bottom-aligned within the
// highest flash page below FS_START (the bootloader scans downward from there), and it must sit
// ENTIRELY above both the running image and the in-place decoder workspace, and below the filesystem
// region (ExtraFS/InternalFS — where user prefs live, assumed immutable). Reserving the full workspace
// here prevents accepting a large container that the bootloader would later reject as overlapping it.
// Returns false (and leaves out_start untouched) if it does not fit. This is the SINGLE place the FS
// ceiling + app-collision bounds are enforced; begin()/reopen() both go through it. Pure — no flash I/O —
// so it is unit-tested natively in test/test_ota/test_ota_flashplan.cpp.
inline bool mota_nrf52_stage_plan(uint32_t total_size, uint32_t app_base, uint32_t app_end,
                                  uint32_t& out_start) {
  if (!mota_nrf52_layout_valid(app_base) || app_end < app_base ||
      app_end > app_base + MOTA_NRF52_INPLACE_MEMORY) return false;
  const uint32_t workspace_end = app_base + MOTA_NRF52_INPLACE_MEMORY;
  const uint32_t capacity = MOTA_NRF52_FS_START - workspace_end;
  if (total_size < 13 || total_size > capacity) return false;   // 13 = header(8)+trailer(5); must fit below FS
  uint32_t start = (MOTA_NRF52_FS_START - total_size) & ~(MOTA_NRF52_FLASH_PAGE - 1);   // bottom-align down
  if (start < app_end || start < workspace_end) return false;   // would overlap app / decoder workspace
  out_start = start;
  return true;
}

} // namespace ota
} // namespace mesh
