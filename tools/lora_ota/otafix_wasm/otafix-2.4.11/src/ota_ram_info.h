// Separate bootloader capability marker for the fixed MeshCore mOTA SRAM
// arena. This deliberately does not extend mota_bl_info_t: older OTAFIX
// bootloaders require that structure's reserved bytes remain zero when they
// validate a bootloader update.
#ifndef OTA_RAM_INFO_H_
#define OTA_RAM_INFO_H_

#include <stddef.h>
#include <stdint.h>

#include "ota_hybrid_handoff.h"

#define MOTA_RAM_INFO_ABI    1u
#define MOTA_RAM_INFO_MAGIC0 'M'
#define MOTA_RAM_INFO_MAGIC1 'O'
#define MOTA_RAM_INFO_MAGIC2 'T'
#define MOTA_RAM_INFO_MAGIC3 'A'
#define MOTA_RAM_INFO_MAGIC4 'R'
#define MOTA_RAM_INFO_MAGIC5 'A'
#define MOTA_RAM_INFO_MAGIC6 'M'
#define MOTA_RAM_INFO_MAGIC7 'A'

typedef struct {
  uint8_t  magic[8];
  uint16_t abi;
  uint16_t handoff_len;
  uint32_t arena_size;
} mota_ram_info_t;

typedef char mota_ram_info_size_must_be_16[(sizeof(mota_ram_info_t) == 16u) ? 1 : -1];
typedef char mota_ram_info_abi_offset_must_be_8[(offsetof(mota_ram_info_t, abi) == 8u) ? 1 : -1];
typedef char mota_ram_info_handoff_len_offset_must_be_10
  [(offsetof(mota_ram_info_t, handoff_len) == 10u) ? 1 : -1];
typedef char mota_ram_info_arena_size_offset_must_be_12
  [(offsetof(mota_ram_info_t, arena_size) == 12u) ? 1 : -1];

#if defined(MOTA_RAM_ARENA_SIZE) && MOTA_RAM_ARENA_SIZE > 0
  #if MOTA_RAM_ARENA_SIZE != MOTA_HYBRID_ARENA_SIZE
    #error "The mOTA retained arena ABI requires exactly 64 KiB"
  #endif
// Append optional application storage capabilities without changing either
// legacy MOTABLDR or MOTARAMA. Old board loaders can validate this successor
// using their exact existing identity, internal boot-update profile and RAM ABI.
typedef struct {
  mota_ram_info_t ram;
#if defined(MOTA_RAK_AUTO_STORE)
  struct {
    uint8_t  magic[8];       // MOTASTOR
    uint16_t abi;            // 1
    uint16_t length;         // 16
    uint8_t  storage_flags;  // optional application QSPI/header-W25 capabilities
    uint8_t  reserved[3];
  } app;
#endif
} mota_ram_capabilities_t;

#if defined(MOTA_RAK_AUTO_STORE)
typedef char mota_ram_capabilities_size_must_be_32[(sizeof(mota_ram_capabilities_t) == 32u) ? 1 : -1];
typedef char mota_app_storage_offset_must_be_16[(offsetof(mota_ram_capabilities_t, app) == 16u) ? 1 : -1];
#endif

extern const mota_ram_capabilities_t g_mota_ram_capabilities;
#define g_mota_ram_info (g_mota_ram_capabilities.ram)
#endif

#endif // OTA_RAM_INFO_H_
