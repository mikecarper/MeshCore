// Shared OTA flash-layout constants for the MeshCore `.mota` delta-apply path on nRF52840.
// SINGLE SOURCE OF TRUTH - keep constants and handoff semantics synchronized with MeshCore
// src/helpers/ota/OtaFlashLayout_nrf52.h.
//
// The running app lives at the SoftDevice end (APP_BASE). Internal ExtraFS, when used, begins at
// 0xD4000; primary InternalFS begins at 0xED000. MeshCore selects the safe staging ceiling for its
// linked layout/storage and passes that choice to the bootloader through GPREGRET2. APP_BASE here is
// the nominal S140 v6 value; the device build uses runtime DFU_BANK_0_REGION_START so it also supports
// S140 v7's one-page-higher application base.

#ifndef OTA_LAYOUT_H_
#define OTA_LAYOUT_H_

#ifndef MOTA_NRF52_APP_BASE
#define MOTA_NRF52_APP_BASE    0x00026000u   // S140 v6 end (host-test default)
#endif
#define MOTA_NRF52_EXTRAFS_START 0x000D4000u // Internal ExtraFS begins here when present
#define MOTA_NRF52_APP_END     0x000ED000u   // Primary InternalFS begins here
#define MOTA_NRF52_BL_START    0x000F4000u   // production nRF52840 bootloader region
#define MOTA_NRF52_BL_SIZE     0x0000A000u   // raw padded region copied by the MBR
#define MOTA_NRF52_INTERNAL_BL_SLOT_START 0x000E2000u // shared internal .mota/raw MBR source
#define MOTA_NRF52_INTERNAL_BL_SLOT_END   MOTA_NRF52_APP_END
// Fixed raw MBR source used by the established XIAO QSPI path and by legacy/manual UF2 DFU.
// The generic internal-LoRa path does not reserve or write this range independently.
#define MOTA_NRF52_BL_SCRATCH_START 0x000E0000u
#define MOTA_NRF52_STAGE_CEILING_LEGACY   MOTA_NRF52_EXTRAFS_START
#define MOTA_NRF52_STAGE_CEILING_EXPANDED MOTA_NRF52_APP_END
// Compatibility name for the legacy lower boundary. New staging/workspace code uses one of the two
// explicit ceilings above.
#define MOTA_NRF52_FS_START    MOTA_NRF52_EXTRAFS_START
#define MOTA_NRF52_FLASH_PAGE  4096u

// GPREGRET value MeshCore writes (then resets) to ask the bootloader to apply a staged `.mota`.
// Distinct from the Adafruit DFU magics (0x57 UF2, 0x4E serial, 0xA8 OTA-BLE) so it never enters DFU.
#define GPREGRET_OTA_APPLY     0x6Au
// Distinct request to install a validated bootloader image from the storage
// selected by GPREGRET2. Keeping this separate from GPREGRET_OTA_APPLY guarantees
// that a bootloader package can never enter the application-update path.
#define GPREGRET_BOOTLOADER_APPLY 0x6Bu
// GPREGRET2 tells the bootloader which bottom-aligned staging window the app used. An older app leaves
// a diagnostic/unknown value here; the bootloader deliberately treats every value except EXPANDED as
// LEGACY so it never scans through a possible Internal ExtraFS by accident.
#define GPREGRET2_OTA_STAGE_LEGACY   0xD4u
#define GPREGRET2_OTA_STAGE_EXPANDED 0xEDu
#define GPREGRET2_OTA_STAGE_QSPI     0x51u
#define GPREGRET2_OTA_STAGE_SD       0x53u
// One-shot retained-RAM handoff describing a format-v2 application container
// split between an internal-flash prefix and the fixed 64 KiB SRAM arena.
#define GPREGRET2_OTA_STAGE_HYBRID   0xA6u

// Bootloader-update result diagnostics. C8 is written immediately before the
// MBR COPY_BL handoff (success never returns); a returned MBR call replaces it
// with C9. A normal boot preserves the retained result for the application.
#define GPREGRET2_BL_GATE             0xC1u
#define GPREGRET2_BL_CONTAINER        0xC2u
#define GPREGRET2_BL_POLICY           0xC3u
#define GPREGRET2_BL_INTEGRITY        0xC4u
#define GPREGRET2_BL_MANIFEST         0xC5u
#define GPREGRET2_BL_APPROVAL         0xC6u
#define GPREGRET2_BL_COPY             0xC7u
#define GPREGRET2_BL_MBR_HANDOFF      0xC8u
#define GPREGRET2_BL_MBR_RETURNED     0xC9u

#endif // OTA_LAYOUT_H_
