// Supplemental SD capabilities for the all-in-one RAK4631 loader. Keep the
// deployed MOTABLDR and MOTASTOR records unchanged: released RAK loaders use
// those exact bytes to authorize an app-preserving bootloader replacement.
#ifndef OTA_RAK_SD_INFO_H_
#define OTA_RAK_SD_INFO_H_

#include <stddef.h>
#include <stdint.h>
#include "ota_bl_info.h"
#include "ota_layout.h"
#include "ota_sd_auth.h"

#if defined(MOTA_RAK_SD_STORE)
  #if !defined(MOTA_RAK_AUTO_STORE) || !defined(MOTA_RAK_AUTO_RAK4631) || \
      !defined(MOTA_QSPI_FLASH) || !defined(MOTA_SD_CARD) || \
      !defined(MOTA_INTERNAL_BOOTLOADER_UPDATE) || defined(MOTA_SD_BOOTLOADER_UPDATE) || \
      !defined(MOTA_RAM_ARENA_SIZE) || MOTA_RAM_ARENA_SIZE != 65536 || \
      defined(MOTA_SD_DUAL_STORE)
    #error "RAK SD storage requires the exact combined RAK4631 internal/RAM/NOR/SD profile"
  #endif

#define MOTA_RAK_SD_INFO_ABI 1u
#define MOTA_RAK_SD_STORAGE_FLAGS MOTA_BL_STORAGE_SD

typedef struct {
  uint8_t magic[8];       // MOTASDRA
  uint16_t abi;           // 1
  uint16_t auth_len;      // existing 72-byte MOTA SDA2 handoff
  uint8_t storage_flags;  // SD applications only; bootloader updates stay internal
  uint8_t stage_marker;   // explicit GPREGRET2_OTA_STAGE_SD, never an auto-probe
  uint8_t reserved[2];
} mota_rak_sd_info_t;

typedef char mota_rak_sd_info_size_must_be_16[(sizeof(mota_rak_sd_info_t) == 16u) ? 1 : -1];
typedef char mota_rak_sd_info_abi_offset_must_be_8
  [(offsetof(mota_rak_sd_info_t, abi) == 8u) ? 1 : -1];
typedef char mota_rak_sd_info_flags_offset_must_be_12
  [(offsetof(mota_rak_sd_info_t, storage_flags) == 12u) ? 1 : -1];

extern const mota_rak_sd_info_t g_mota_rak_sd_info;
#endif

#endif // OTA_RAK_SD_INFO_H_
