#include <gtest/gtest.h>

#include "helpers/ota/OtaFlashLayout_nrf52.h"

using namespace mesh::ota;

// These lock down the nRF52 single-slot staging geometry that OtaStoreFlashNrf52::begin()/
// reopen() rely on. A received `.mota` is placed bottom-aligned below the filesystem region — ExtraFS
// (0xD4000) / InternalFS (0xED000), where the node's user preferences live — and above the running image.
// The prefs region is assumed IMMUTABLE (its bytes are outside the served/hashed self-image), so staging
// or an in-place apply must never reach into it. If a layout constant or the placement math is edited
// inconsistently, these fail here instead of silently corrupting prefs / the app on real hardware.

static constexpr uint32_t APP_V6 = MOTA_NRF52_APP_BASE_S140_V6;
static constexpr uint32_t APP_V7 = MOTA_NRF52_APP_BASE_S140_V7;
static constexpr uint32_t CAP_V6 = MOTA_NRF52_FS_START - (APP_V6 + MOTA_NRF52_INPLACE_MEMORY); // 88 KB
static constexpr uint32_t CAP_V7 = MOTA_NRF52_FS_START - (APP_V7 + MOTA_NRF52_INPLACE_MEMORY); // 84 KB

// A typical running image (~520 KB) leaves room; the container lands strictly within (app_end, FS_START].
TEST(OtaFlashPlan, StagesBelowFilesystemAndAboveApp) {
  uint32_t app_end = APP_V6 + 520u * 1024u;
  uint32_t start = 0xDEADBEEF;
  ASSERT_TRUE(mota_nrf52_stage_plan(64u * 1024u, APP_V6, app_end, start));
  EXPECT_GE(start, app_end);                                    // never overlaps the running image
  EXPECT_GE(start, APP_V6 + MOTA_NRF52_INPLACE_MEMORY);         // never overlaps detools workspace
  EXPECT_LE(start + 64u * 1024u, MOTA_NRF52_FS_START);          // never reaches into ExtraFS/InternalFS/prefs
  EXPECT_EQ(start % MOTA_NRF52_FLASH_PAGE, 0u);                 // page-aligned (the flash erase unit)
}

// Bottom-aligned: start is the page-aligned FS_START - total_size, so the trailer sits within the
// highest page below the ceiling where the bootloader's downward scan finds it.
TEST(OtaFlashPlan, BottomAlignedBelowCeiling) {
  uint32_t start = 0;
  uint32_t total = 60000;
  ASSERT_TRUE(mota_nrf52_stage_plan(total, APP_V6, APP_V6, start));
  EXPECT_EQ(start, (MOTA_NRF52_FS_START - total) & ~(MOTA_NRF52_FLASH_PAGE - 1));
  EXPECT_LE(start + total, MOTA_NRF52_FS_START);
  EXPECT_GT(start + total, MOTA_NRF52_FS_START - MOTA_NRF52_FLASH_PAGE);   // within one page of the ceiling
}

// An exactly-capacity container fills the region above the decoder workspace; one byte more never fits.
TEST(OtaFlashPlan, RejectsOversizedContainer) {
  uint32_t start = 0;
  ASSERT_TRUE(mota_nrf52_stage_plan(CAP_V6, APP_V6, APP_V6, start));
  EXPECT_EQ(start, APP_V6 + MOTA_NRF52_INPLACE_MEMORY);
  EXPECT_EQ(start + CAP_V6, MOTA_NRF52_FS_START);
  EXPECT_FALSE(mota_nrf52_stage_plan(CAP_V6 + 1, APP_V6, APP_V6, start));
}

// A running image that exceeds detools' fixed in-place memory can never be a valid delta base.
TEST(OtaFlashPlan, RejectsAppLargerThanWorkspace) {
  uint32_t app_end = APP_V6 + MOTA_NRF52_INPLACE_MEMORY + 1;
  uint32_t start = 0;
  EXPECT_FALSE(mota_nrf52_stage_plan(4u * 1024u, APP_V6, app_end, start));
}

// Minimum container is header(8)+trailer(5)=13 bytes; anything smaller is not a container.
TEST(OtaFlashPlan, RejectsUndersizedContainer) {
  uint32_t start = 0;
  EXPECT_FALSE(mota_nrf52_stage_plan(12, APP_V6, APP_V6, start));
  EXPECT_TRUE(mota_nrf52_stage_plan(13, APP_V6, APP_V6, start));
}

// The user-preferences filesystems (ExtraFS @ 0xD4000, InternalFS @ 0xED000) are entirely ABOVE any
// staged container AND above the in-place apply workspace — the immutability the shared-firmware hashes
// depend on. Hard-code the FS addresses here (independent of the layout header) so a future edit to
// FS_START that drifts into a filesystem is caught.
TEST(OtaFlashPlan, PrefsRegionNeverStaged) {
  const uint32_t EXTRAFS_START    = 0xD4000u;   // companion ExtraFS (CustomLFS(0xD4000, 0x19000))
  const uint32_t INTERNALFS_START = 0xED000u;   // primary LittleFS (holds /com_prefs)
  EXPECT_LE(MOTA_NRF52_FS_START, EXTRAFS_START);      // staging ceiling at/below the first filesystem
  EXPECT_LT(EXTRAFS_START, INTERNALFS_START);
  // the largest possible staged container still ends at the ceiling, never into a filesystem
  uint32_t start = 0;
  ASSERT_TRUE(mota_nrf52_stage_plan(CAP_V6, APP_V6, APP_V6, start));
  EXPECT_LE(start + CAP_V6, EXTRAFS_START);
  // and the in-place apply workspace ends below the filesystem too
  EXPECT_LE(APP_V6 + MOTA_NRF52_INPLACE_MEMORY, EXTRAFS_START);
}

// S140 v7 moves the app start by one page. Runtime linker-base discovery must leave a correspondingly
// smaller but still safe staging region rather than scanning the v6 address and missing EndF.
TEST(OtaFlashPlan, SupportsS140V7RuntimeBase) {
  EXPECT_TRUE(mota_nrf52_layout_valid(APP_V7));
  EXPECT_EQ(mota_nrf52_stage_capacity(APP_V7), CAP_V7);
  uint32_t start = 0;
  ASSERT_TRUE(mota_nrf52_stage_plan(CAP_V7, APP_V7, APP_V7 + 520u * 1024u, start));
  EXPECT_EQ(start, APP_V7 + MOTA_NRF52_INPLACE_MEMORY);
  EXPECT_EQ(start + CAP_V7, MOTA_NRF52_FS_START);
}

// out_start is only written on success — a rejected plan must not clobber the caller's variable.
TEST(OtaFlashPlan, LeavesOutputUntouchedOnReject) {
  uint32_t start = 0x1234ABCD;
  EXPECT_FALSE(mota_nrf52_stage_plan(CAP_V6 + 1, APP_V6, APP_V6, start));
  EXPECT_EQ(start, 0x1234ABCDu);
}
