// MeshCore OTA bootloader capability marker.
//
// A const blob the bootloader embeds in its flash so the RUNNING app can tell, BEFORE staging+approving+
// rebooting, whether THIS bootloader can actually apply a given `.mota`. Without it the app would reboot
// into a bootloader that silently can't apply (legacy, stock Adafruit, or an OLDER OTAFIX that predates a
// `.mota` format change) and the device would just come back up unchanged.
//
// The app scans the bootloader flash region for MOTA_BL_MAGIC and reads the fields. Mirror of MeshCore's
// src/helpers/ota/OtaBlInfo.h - keep byte-identical.
#ifndef OTA_BL_INFO_H_
#define OTA_BL_INFO_H_

#include <stdint.h>

// 8-byte magic (distinctive enough that a stray match in flash is implausible).
#define MOTA_BL_MAGIC0 'M'
#define MOTA_BL_MAGIC1 'O'
#define MOTA_BL_MAGIC2 'T'
#define MOTA_BL_MAGIC3 'A'
#define MOTA_BL_MAGIC4 'B'
#define MOTA_BL_MAGIC5 'L'
#define MOTA_BL_MAGIC6 'D'
#define MOTA_BL_MAGIC7 'R'

// Highest `.mota` format_ver this bootloader's apply understands. Bump when the on-flash `.mota` layout the
// bootloader parses changes (e.g. the fixed-layout manifest). The app requires bl.apply_abi >= mota.format_ver.
#if defined(MOTA_SD_BOOTLOADER_UPDATE) || defined(MOTA_QSPI_BOOTLOADER_UPDATE) || \
  defined(MOTA_INTERNAL_BOOTLOADER_UPDATE)
  #define MOTA_BL_APPLY_ABI 3u
#else
  #define MOTA_BL_APPLY_ABI 2u
#endif

// storage_flags[0]
#define MOTA_BL_STORAGE_SD             0x01u
#define MOTA_BL_STORAGE_STAGE_CEILING  0x02u // understands the GPREGRET2 staging-ceiling handoff
#define MOTA_BL_STORAGE_QSPI           0x04u // raw external-QSPI container at offset zero
#define MOTA_BL_STORAGE_BOOT_UPDATE     0x08u // format-v3 bootloader image install
#define MOTA_BL_STORAGE_KNOWN          (MOTA_BL_STORAGE_SD | MOTA_BL_STORAGE_STAGE_CEILING | \
                                        MOTA_BL_STORAGE_QSPI | MOTA_BL_STORAGE_BOOT_UPDATE)

typedef struct {
  uint8_t  magic[8];     // MOTA_BL_MAGIC*
  uint16_t apply_abi;    // max .mota format_ver this bootloader can apply
  uint16_t codec_mask;   // bit i set => can apply codec_id i (in-place delta = bit 2)
  uint8_t  storage_flags[4]; // byte 0 MOTA_BL_STORAGE_*; remaining bytes reserved
} mota_bl_info_t;

#endif // OTA_BL_INFO_H_
