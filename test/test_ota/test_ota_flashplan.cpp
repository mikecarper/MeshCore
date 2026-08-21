#include <gtest/gtest.h>
#include <cstring>

#include "helpers/ota/OtaFlashLayout_nrf52.h"
#include "helpers/ota/OtaStoreQspiNrf52.h"
#include "helpers/ota/OtaSdHandoff.h"

using namespace mesh::ota;

// These lock down the nRF52 single-slot staging geometry that OtaStoreFlashNrf52::begin()/
// reopen() rely on. A received `.mota` is placed bottom-aligned below the filesystem region - ExtraFS
// (0xD4000) / InternalFS (0xED000), where the node's user preferences live - and above the running image.
// The prefs region is assumed IMMUTABLE (its bytes are outside the served/hashed self-image), so staging
// or an in-place apply must never reach into it. If a layout constant or the placement math is edited
// inconsistently, these fail here instead of silently corrupting prefs / the app on real hardware.

static constexpr uint32_t APP_V6 = MOTA_NRF52_APP_BASE_S140_V6;
static constexpr uint32_t APP_V7 = MOTA_NRF52_APP_BASE_S140_V7;
static constexpr uint32_t LEGACY = MOTA_NRF52_STAGE_CEILING_LEGACY;
static constexpr uint32_t EXPANDED = MOTA_NRF52_STAGE_CEILING_EXPANDED;
static constexpr uint32_t SHARED_BOOT_START = MOTA_NRF52_SHARED_BOOT_STAGE_START;
static constexpr uint32_t APP_IMAGE_SIZE = 520u * 1024u;
static constexpr uint32_t APP_END_V6 = APP_V6 + APP_IMAGE_SIZE;
static constexpr uint32_t APP_END_V7 = APP_V7 + APP_IMAGE_SIZE;
static constexpr uint32_t CAP_V6 = LEGACY - APP_END_V6;
static constexpr uint32_t CAP_V7 = LEGACY - APP_END_V7;
static constexpr uint32_t CAP_V6_EXPANDED = EXPANDED - APP_END_V6;
static constexpr uint32_t CAP_V7_EXPANDED = EXPANDED - APP_END_V7;

TEST(OtaQspiTiming, PreservesDeepPowerDownEntryAndWakeGuards) {
  // MX25R1635F requires 10 us to enter DPD plus 30 us before another command;
  // its release latency can reach 45 us. These constants are consumed by the
  // real HAL path, so a future power-saving edit cannot restore the live
  // plan_layout()->begin() race without failing the native suite.
  EXPECT_GE(MOTA_QSPI_DPD_ENTRY_GUARD_US, 50u);
  EXPECT_GE(MOTA_QSPI_DPD_WAKE_GUARD_US, 45u);
}

TEST(OtaQspiWake, ShiftsReleaseCommandMostSignificantBitFirst) {
  // This byte is emitted over GPIO before TASKS_ACTIVATE. If activation is
  // attempted first, a sleeping NOR ignores it and READY never arrives.
  const bool expected[] = {true, false, true, false, true, false, true, true};
  EXPECT_EQ(MOTA_QSPI_RELEASE_FROM_DPD_OPCODE, 0xABu);
  for (uint8_t bit = 0; bit < 8u; bit++) {
    EXPECT_EQ(mota_qspi_release_from_dpd_bit(bit), expected[bit]);
  }
  EXPECT_FALSE(mota_qspi_release_from_dpd_bit(8u));
}

TEST(OtaQspiStatus, TreatsOnlyTheNorWriteInProgressBitAsBusy) {
  // Nordic READY is not flash completion. The hardware path polls RDSR until
  // this predicate clears after every program and erase operation.
  EXPECT_FALSE(mota_qspi_status_busy(0x00));
  EXPECT_FALSE(mota_qspi_status_busy(0xFC));
  EXPECT_TRUE(mota_qspi_status_busy(0x01));
  EXPECT_TRUE(mota_qspi_status_busy(0xFF));
}

TEST(OtaQspiDiagnostics, ExposesStableFailureStageNames) {
  EXPECT_STREQ(mota_qspi_stage_name(OtaQspiStage::PROGRAM), "program");
  EXPECT_STREQ(mota_qspi_stage_name(OtaQspiStage::PROGRAM_BUSY), "program-busy");
  EXPECT_STREQ(mota_qspi_stage_name(OtaQspiStage::ERASE_BUSY), "erase-busy");
  EXPECT_STREQ(mota_qspi_stage_name(OtaQspiStage::INVALIDATE_VERIFY), "invalidate-verify");
}

TEST(OtaFlashPlan, SelectsCeilingFromLinkedLayoutAndStorage) {
  // Actual internal secondary storage is authoritative regardless of linker selection.
  EXPECT_EQ(mota_nrf52_stage_ceiling_for_layout(EXPANDED, true), LEGACY);
  EXPECT_EQ(mota_nrf52_stage_ceiling_for_layout(LEGACY, true), LEGACY);
  // Standard or ExtraFS linker without an internal secondary filesystem reclaims the unused 100 KiB.
  EXPECT_EQ(mota_nrf52_stage_ceiling_for_layout(EXPANDED, false), EXPANDED);
  EXPECT_EQ(mota_nrf52_stage_ceiling_for_layout(LEGACY, false), EXPANDED);
  // The explicit XIAO boot-update linker leaves 40 KiB scratch at 0xE0000 and still stages externally.
  EXPECT_EQ(mota_nrf52_stage_ceiling_for_layout(MOTA_NRF52_BOOT_SCRATCH_START, false), EXPANDED);
  // An unrecognized linker region is never permission to erase a larger window.
  EXPECT_EQ(mota_nrf52_stage_ceiling_for_layout(0xE1000u, false), LEGACY);
}

TEST(OtaFlashPlan, ValidatesExternalInplacePatchGeometryBeforeHandoff) {
  const uint32_t workspace = EXPANDED - APP_V7;
  const uint32_t running = 620000u;
  const uint32_t target = 633984u;
  ASSERT_TRUE(mota_nrf52_external_patch_geometry_valid(
      workspace, MOTA_NRF52_FLASH_PAGE, 0, running, target,
      workspace, running, target));

  EXPECT_FALSE(mota_nrf52_external_patch_geometry_valid(
      workspace + 1, MOTA_NRF52_FLASH_PAGE, 0, running, target,
      workspace, running, target));
  EXPECT_FALSE(mota_nrf52_external_patch_geometry_valid(
      workspace, 2048, 0, running, target, workspace, running, target));
  EXPECT_FALSE(mota_nrf52_external_patch_geometry_valid(
      workspace, MOTA_NRF52_FLASH_PAGE, 1, running, target,
      workspace, running, target));
  EXPECT_FALSE(mota_nrf52_external_patch_geometry_valid(
      workspace, MOTA_NRF52_FLASH_PAGE, 0, running - 1, target,
      workspace, running, target));
  EXPECT_FALSE(mota_nrf52_external_patch_geometry_valid(
      workspace, MOTA_NRF52_FLASH_PAGE, 0, running, target + 1,
      workspace, running, target));
}

// A typical running image (~520 KB) leaves room; the container lands strictly above it and below ExtraFS.
TEST(OtaFlashPlan, StagesBelowFilesystemAndAboveApp) {
  uint32_t start = 0xDEADBEEF;
  ASSERT_TRUE(mota_nrf52_stage_plan(64u * 1024u, APP_V6, APP_END_V6, LEGACY, start));
  EXPECT_GE(start, APP_END_V6);                         // never overlaps the running image
  EXPECT_LE(start + 64u * 1024u, LEGACY);              // never reaches into ExtraFS/prefs
  EXPECT_EQ(start % MOTA_NRF52_FLASH_PAGE, 0u);         // page-aligned (the flash erase unit)
}

// Bottom-aligned: start is the page-aligned FS_START - total_size, so the trailer sits within the
// highest page below the ceiling where the bootloader's downward scan finds it.
TEST(OtaFlashPlan, BottomAlignedBelowCeiling) {
  uint32_t start = 0;
  uint32_t total = 60000;
  ASSERT_TRUE(mota_nrf52_stage_plan(total, APP_V6, APP_V6, LEGACY, start));
  EXPECT_EQ(start, (LEGACY - total) & ~(MOTA_NRF52_FLASH_PAGE - 1));
  EXPECT_LE(start + total, LEGACY);
  EXPECT_GT(start + total, LEGACY - MOTA_NRF52_FLASH_PAGE);   // within one page of the ceiling
}

// An exactly-capacity container fills the page-aligned space above the app; one byte more never fits.
TEST(OtaFlashPlan, RejectsOversizedContainer) {
  uint32_t start = 0;
  ASSERT_TRUE(mota_nrf52_stage_plan(CAP_V6, APP_V6, APP_END_V6, LEGACY, start));
  EXPECT_EQ(start, APP_END_V6);
  EXPECT_EQ(start + CAP_V6, LEGACY);
  EXPECT_FALSE(mota_nrf52_stage_plan(CAP_V6 + 1, APP_V6, APP_END_V6, LEGACY, start));
}

// The package carries its own checked memory_size, so a valid app may exceed the old 608 KiB fallback.
TEST(OtaFlashPlan, AcceptsAppLargerThanFallbackWhenPackageFits) {
  const uint32_t app_end = APP_V6 + MOTA_NRF52_FALLBACK_INPLACE_MEMORY + 32u * 1024u;
  uint32_t start = 0;
  ASSERT_TRUE(mota_nrf52_stage_plan(16u * 1024u, APP_V6, app_end, LEGACY, start));
  EXPECT_GE(start, app_end);
}

// Minimum container is header(8)+trailer(5)=13 bytes; anything smaller is not a container.
TEST(OtaFlashPlan, RejectsUndersizedContainer) {
  uint32_t start = 0;
  EXPECT_FALSE(mota_nrf52_stage_plan(12, APP_V6, APP_V6, LEGACY, start));
  EXPECT_TRUE(mota_nrf52_stage_plan(13, APP_V6, APP_V6, LEGACY, start));
}

// The user-preferences filesystems (ExtraFS @ 0xD4000, InternalFS @ 0xED000) are entirely ABOVE any
// staged container. Hard-code the FS addresses here (independent of the layout header) so a future edit
// that drifts into a filesystem is caught.
TEST(OtaFlashPlan, PrefsRegionNeverStaged) {
  const uint32_t EXTRAFS_START    = 0xD4000u;   // companion ExtraFS (CustomLFS(0xD4000, 0x19000))
  const uint32_t INTERNALFS_START = 0xED000u;   // primary LittleFS (holds /com_prefs)
  EXPECT_EQ(LEGACY, EXTRAFS_START);
  EXPECT_EQ(EXPANDED, INTERNALFS_START);
  EXPECT_LT(EXTRAFS_START, INTERNALFS_START);
  // the largest possible staged container still ends at the ceiling, never into a filesystem
  uint32_t start = 0;
  ASSERT_TRUE(mota_nrf52_stage_plan(CAP_V6, APP_V6, APP_END_V6, LEGACY, start));
  EXPECT_LE(start + CAP_V6, EXTRAFS_START);
}

// S140 v7 moves the app start by one page. Runtime linker-base discovery must leave a correspondingly
// smaller but still safe staging region rather than scanning the v6 address and missing EndF.
TEST(OtaFlashPlan, SupportsS140V7RuntimeBase) {
  EXPECT_TRUE(mota_nrf52_layout_valid(APP_V7, LEGACY));
  EXPECT_EQ(mota_nrf52_stage_capacity(APP_V7, APP_END_V7, LEGACY), CAP_V7);
  uint32_t start = 0;
  ASSERT_TRUE(mota_nrf52_stage_plan(CAP_V7, APP_V7, APP_END_V7, LEGACY, start));
  EXPECT_EQ(start, APP_END_V7);
  EXPECT_EQ(start + CAP_V7, LEGACY);
}

TEST(OtaFlashPlan, ExpandedCeilingAddsExactly100KiBForV6AndV7) {
  EXPECT_EQ(EXPANDED - LEGACY, 100u * 1024u);
  EXPECT_EQ(CAP_V6_EXPANDED - CAP_V6, 100u * 1024u);
  EXPECT_EQ(CAP_V7_EXPANDED - CAP_V7, 100u * 1024u);
  EXPECT_EQ(mota_nrf52_stage_capacity(APP_V6, APP_END_V6, EXPANDED), CAP_V6_EXPANDED);
  EXPECT_EQ(mota_nrf52_stage_capacity(APP_V7, APP_END_V7, EXPANDED), CAP_V7_EXPANDED);

  uint32_t start = 0;
  ASSERT_TRUE(mota_nrf52_stage_plan(CAP_V7_EXPANDED, APP_V7, APP_END_V7, EXPANDED, start));
  EXPECT_EQ(start, APP_END_V7);
  EXPECT_EQ(start + CAP_V7_EXPANDED, EXPANDED);
}

TEST(OtaFlashPlan, RejectsAppOutsideSelectedRegion) {
  uint32_t start = 0x1234ABCD;
  EXPECT_FALSE(mota_nrf52_stage_plan(4096, APP_V6, LEGACY + 1, LEGACY, start));
  EXPECT_EQ(start, 0x1234ABCDu);
}

TEST(OtaFlashPlan, RejectsUnknownCeiling) {
  uint32_t start = 0x1234ABCD;
  EXPECT_FALSE(mota_nrf52_stage_plan(4096, APP_V6, APP_V6, 0xE1000u, start));
  EXPECT_EQ(start, 0x1234ABCDu);
}

TEST(OtaFlashPlan, BootPackageUsesOneDynamicSharedInternalSlot) {
  // v3 = header + fixed manifest + forty leaves + 40 KiB image + trailer.
  const uint32_t total = 8u + 197u + 40u * 4u + 40u * 1024u + 5u;
  ASSERT_EQ(total, MOTA_NRF52_BOOT_CONTAINER_SIZE);
  uint32_t start = 0;
  ASSERT_TRUE(mota_nrf52_shared_boot_stage_plan(
      total, APP_V6, true, SHARED_BOOT_START, start));
  EXPECT_EQ(start, SHARED_BOOT_START);
  EXPECT_EQ(start, (EXPANDED - total) & ~(MOTA_NRF52_FLASH_PAGE - 1u));

  // OTAFIX compacts payload offset +365 forward inside these same eleven
  // pages; no independent scratch bank participates in the internal path.
  const uint32_t payload_offset = 8u + 197u + 40u * 4u;
  EXPECT_EQ(payload_offset, 365u);
  EXPECT_LE(start + payload_offset + 40u * 1024u, EXPANDED);
  EXPECT_LE(start + 40u * 1024u, EXPANDED);

  // Runtime headroom, not a special linker, is authoritative.
  EXPECT_FALSE(mota_nrf52_shared_boot_stage_plan(
      total, APP_V6, true, SHARED_BOOT_START + 1u, start));
  EXPECT_FALSE(mota_nrf52_shared_boot_stage_plan(
      total - 1u, APP_V6, true, APP_V6, start));
  EXPECT_FALSE(mota_nrf52_shared_boot_stage_plan(
      total + 1u, APP_V6, true, APP_V6, start));
}

TEST(OtaFlashPlan, SharedInternalStoreKeepsNormalApplicationCeilingAndHandoff) {
  EXPECT_TRUE(mota_nrf52_target_image_fits(
      APP_V6, EXPANDED - APP_V6, EXPANDED));
  EXPECT_FALSE(mota_nrf52_target_image_fits(
      APP_V6, EXPANDED - APP_V6 + 1u, EXPANDED));
  EXPECT_EQ(mota_nrf52_flash_stage_handoff(EXPANDED),
            GPREGRET2_OTA_STAGE_EXPANDED);
  EXPECT_EQ(mota_nrf52_flash_stage_handoff(LEGACY),
            GPREGRET2_OTA_STAGE_LEGACY);
}

TEST(OtaFlashPlan, InternalOrdinaryDeltaUsesTheSameEd000Store) {
  const uint32_t running_end = APP_V6 + 500u * 1024u;
  const uint32_t delta_container = 28u * 1024u;
  uint32_t staged = 0;
  ASSERT_TRUE(mota_nrf52_stage_plan(delta_container, APP_V6, running_end,
                                    EXPANDED, staged));
  EXPECT_GE(staged, running_end);
  EXPECT_LE(staged + delta_container, EXPANDED);
  EXPECT_EQ(mota_nrf52_flash_stage_handoff(EXPANDED), 0xEDu);
}

TEST(OtaFlashPlan, LargeInternalOrdinaryDeltaMayStageBelowSharedBootStart) {
  // An ordinary delta container can exceed the boot package's eleven pages.
  // It bottom-aligns below E2000 and remains valid when detools'
  // encoded workspace ends before the actual container start.
  const uint32_t running_end = APP_V6 + 600u * 1024u;
  const uint32_t delta_container = 80u * 1024u;
  uint32_t staged = 0;
  ASSERT_TRUE(mota_nrf52_stage_plan(delta_container, APP_V6, running_end,
                                    EXPANDED, staged));
  EXPECT_LT(staged, SHARED_BOOT_START);
  EXPECT_GE(staged, running_end);

  const uint32_t workspace = staged - APP_V6;
  const uint32_t target_size = workspace;
  EXPECT_TRUE(mota_nrf52_internal_patch_workspace_valid(
      workspace, APP_V6, staged, target_size, EXPANDED));
  EXPECT_FALSE(mota_nrf52_internal_patch_workspace_valid(
      workspace + 1u, APP_V6, staged, target_size, EXPANDED));
  EXPECT_FALSE(mota_nrf52_internal_patch_workspace_valid(
      workspace, APP_V6, staged, EXPANDED - APP_V6 + 1u, EXPANDED));
}

TEST(OtaFlashPlan, MissingEndfRejectsEverySharedInternalPackageBeforeErase) {
  uint32_t protected_end = 0;
  ASSERT_TRUE(mota_nrf52_protected_app_end(
      APP_V6, EXPANDED, false, 0, false, protected_end));
  EXPECT_EQ(protected_end, APP_V6 + MOTA_NRF52_FALLBACK_INPLACE_MEMORY);

  // A shared-internal-update build is linked through ED000, so the old 608 KiB
  // estimate is not a safe erase floor for either package kind. Missing EndF
  // disables all internal staging before begin()/reopen can erase a page.
  EXPECT_FALSE(mota_nrf52_protected_app_end(
      APP_V6, EXPANDED, false, 0, true, protected_end));

  // The old rescue estimate would also admit this ordinary 80 KiB delta at
  // D9000 even though a legal ED000-linked image can have live bytes there.
  // A privileged shared-internal build therefore never calls stage_plan with
  // that estimate: protected_app_end() above returns false/capacity zero.
  const uint32_t ordinary_container = 80u * 1024u;
  uint32_t unsafe_start = 0;
  const uint32_t legacy_estimate =
      APP_V6 + MOTA_NRF52_FALLBACK_INPLACE_MEMORY;
  ASSERT_TRUE(mota_nrf52_stage_plan(
      ordinary_container, APP_V6, legacy_estimate, EXPANDED, unsafe_start));
  const uint32_t legal_live_tail = unsafe_start + MOTA_NRF52_FLASH_PAGE;
  ASSERT_GT(legal_live_tail, legacy_estimate);
  ASSERT_LT(legal_live_tail, EXPANDED);
  EXPECT_FALSE(mota_nrf52_stage_plan(
      ordinary_container, APP_V6, legal_live_tail, EXPANDED, protected_end));

  // Model a legal linked image whose tail extends beyond the old 608 KiB
  // estimate. The privileged planner rejects missing EndF even if the generic
  // rescue fallback would appear to leave enough room.
  const uint32_t real_tail = APP_V6 + MOTA_NRF52_FALLBACK_INPLACE_MEMORY + 0x8000u;
  ASSERT_LT(real_tail, SHARED_BOOT_START);
  uint32_t staged = 0;
  EXPECT_FALSE(mota_nrf52_shared_boot_stage_plan(
      MOTA_NRF52_BOOT_CONTAINER_SIZE, APP_V6, false, protected_end, staged));
  ASSERT_TRUE(mota_nrf52_shared_boot_stage_plan(
      MOTA_NRF52_BOOT_CONTAINER_SIZE, APP_V6, true, real_tail, staged));
  EXPECT_EQ(staged, SHARED_BOOT_START);
}

TEST(OtaFlashPlan, ReopenBoundsUntrustedTotalBeforeManifestRead) {
  const uint32_t start = SHARED_BOOT_START;
  EXPECT_TRUE(mota_nrf52_container_span_valid(
      start, EXPANDED, MOTA_NRF52_BOOT_CONTAINER_SIZE, 8u + 197u + 5u));
  EXPECT_FALSE(mota_nrf52_container_span_valid(
      start, EXPANDED, UINT32_MAX, 8u + 197u + 5u));
  EXPECT_FALSE(mota_nrf52_container_span_valid(
      start, EXPANDED, 8u + 197u + 4u, 8u + 197u + 5u));
  EXPECT_FALSE(mota_nrf52_container_span_valid(
      EXPANDED + 1u, EXPANDED, MOTA_NRF52_BOOT_CONTAINER_SIZE,
      8u + 197u + 5u));
}

// out_start is only written on success - a rejected plan must not clobber the caller's variable.
TEST(OtaFlashPlan, LeavesOutputUntouchedOnReject) {
  uint32_t start = 0x1234ABCD;
  EXPECT_FALSE(mota_nrf52_stage_plan(CAP_V6 + 1, APP_V6, APP_END_V6, LEGACY, start));
  EXPECT_EQ(start, 0x1234ABCDu);
}

TEST(OtaSdHandoff, EncodesChecksummedRecordAndPreservesSectorTail) {
  uint8_t sector[MOTA_SD_SECTOR_SIZE];
  std::memset(sector, 0xA5, sizeof(sector));
  mota_sd_encode_handoff(sector, 2048, 1234, 630000, 8000000);

  EXPECT_EQ(0, std::memcmp(sector, MOTA_SD_HANDOFF_MAGIC, 8));
  EXPECT_EQ(mota_sd_rd32(sector + 8), MOTA_SD_HANDOFF_VERSION);
  EXPECT_EQ(mota_sd_rd32(sector + 12), 2048u);
  EXPECT_EQ(mota_sd_rd32(sector + 16), 1234u);
  EXPECT_EQ(mota_sd_rd32(sector + 20), 630000u);
  EXPECT_EQ(mota_sd_rd32(sector + 24), ~630000u);
  EXPECT_EQ(mota_sd_rd32(sector + 28), 8000000u);
  EXPECT_EQ(mota_sd_rd32(sector + 32), mota_sd_crc32(sector, 32));
  EXPECT_EQ(sector[MOTA_SD_HANDOFF_LEN], 0xA5);  // bytes outside our record are untouched

  sector[20] ^= 1u;
  EXPECT_NE(mota_sd_rd32(sector + 32), mota_sd_crc32(sector, 32));
}
