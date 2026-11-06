// MeshCore `.mota` delta-apply for the nRF52 bootloader (single-slot, in-place).
//
// Called once per boot, just before the app jump. If MeshCore staged + approved an application update
// and asked us to apply it (GPREGRET == GPREGRET_OTA_APPLY, with GPREGRET2 selecting the safe staging ceiling), we:
// scan flash for the `.mota`, confirm it is APPROVED and built for the *currently flashed* firmware
// (base_hash vs the running image's EndF body hash),
// clear the approval flag (so a failure never retries), apply the detools in-place patch over the app
// region, verify the result hashes to the manifest image_hash, and update the bootloader settings so
// the new image boots. On any failure after the (non-destructive) base check we leave the settings
// invalid -> the bootloader falls through to DFU (UF2-recoverable); we never boot an unverified image.
//
// GPREGRET_BOOTLOADER_APPLY instead validates an exact-board format-v3 full bootloader container. XIAO
// reads it from QSPI into the fixed MBR scratch range. MeshTower V2 reads it from SD and first proves
// its unreserved scratch range is above the hash-bound live app. Eligible internal-only nRF52840 targets
// compact the payload forward in the same ED000 staging slot. Every path verifies the final raw source
// before invoking Nordic MBR COPY_BL.
#ifndef OTA_DELTA_H_
#define OTA_DELTA_H_

#include <stdbool.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

// Returns true iff a pending update was applied + committed (caller should reset to boot it).
// Returns false if there was nothing to do, or it failed (caller continues the normal boot/DFU path).
bool ota_delta_check_and_apply(void);

#if defined(MOTA_INTERNAL_BOOTLOADER_UPDATE) || defined(MOTA_SD_BOOTLOADER_UPDATE)
// Recompute the live application's EndF body hash and require the inclusive
// image end at or below limit. Used by LoRa/SD scratch safety and the
// legacy/manual UF2 staging guard.
bool ota_delta_live_app_fits_below(uint32_t limit);
#endif

#ifdef __cplusplus
}
#endif

#endif // OTA_DELTA_H_
