#ifndef BOOTLOADER_IMAGE_H_
#define BOOTLOADER_IMAGE_H_

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#define BOOTLOADER_UPDATE_MANIFEST_MAGIC0       0x464D4C42UL
#define BOOTLOADER_UPDATE_MANIFEST_MAGIC1       0x31435243UL
#define BOOTLOADER_UPDATE_MANIFEST_VERSION      1U
#define BOOTLOADER_UPDATE_DEVICE_NAME_SIZE      16U

// Backward-compatible v2 compatibility envelope.  Bytes 0..43 remain the
// exact manifest understood by preview.12 and earlier.  This extension must be
// immediately adjacent, and is covered by the legacy whole-image CRC at byte
// 40.  A preview.12 bootloader can therefore bootstrap this image, while every
// v2-aware receiver requires and machine-checks the extension.
#define BOOTLOADER_UPDATE_EXTENSION_MAGIC0      0x324D4C42UL /* "BLM2" */
#define BOOTLOADER_UPDATE_EXTENSION_MAGIC1      0x54464F53UL /* "SOFT" */
#define BOOTLOADER_UPDATE_EXTENSION_VERSION     2U
#define BOOTLOADER_UPDATE_LAYOUT_ABI             1U

typedef struct {
  uint32_t magic0;
  uint32_t magic1;
  uint16_t version;
  uint16_t header_size;
  uint32_t image_start;
  uint32_t image_size;
  uint32_t board_id;
  char device_name[BOOTLOADER_UPDATE_DEVICE_NAME_SIZE];
  uint32_t crc32;
} bootloader_update_manifest_t;

typedef char bootloader_update_manifest_size_must_be_44
  [(sizeof(bootloader_update_manifest_t) == 44) ? 1 : -1];

typedef struct {
  uint32_t magic0;
  uint32_t magic1;
  uint16_t version;
  uint16_t header_size;
  uint32_t boot_version;       // MAJOR<<24 | MINOR<<16 | PATCH<<8 | preview
  uint16_t softdevice_family;  // 140 for S140, 132 for S132
  uint16_t softdevice_fwid;    // exact runtime SD_FWID_GET(MBR_SIZE)
  uint32_t app_base;
  uint16_t layout_abi;
  uint16_t compat_flags;       // must be zero; no remote migration override
  uint32_t reserved;           // must be zero
} bootloader_update_extension_t;

typedef char bootloader_update_extension_size_must_be_32
  [(sizeof(bootloader_update_extension_t) == 32) ? 1 : -1];

typedef struct {
  bootloader_update_manifest_t manifest;
  bootloader_update_extension_t extension;
} bootloader_update_envelope_t;

typedef char bootloader_update_envelope_size_must_be_76
  [(sizeof(bootloader_update_envelope_t) == 76) ? 1 : -1];

typedef struct {
  uint32_t boot_version;
  uint16_t softdevice_family;
  uint16_t softdevice_fwid;
  uint32_t app_base;
  uint16_t layout_abi;
} bootloader_image_info_t;

uint32_t bootloader_image_crc32(uint8_t const* image, size_t image_size, size_t crc_offset);
bool bootloader_extension_validate(bootloader_update_extension_t const* extension);
bool bootloader_image_validate(uint8_t const* image, uint32_t image_start, uint32_t image_size,
                               uint32_t expected_board_id, char const* expected_device_name);
bool bootloader_image_info(uint8_t const* image, uint32_t image_start, uint32_t image_size,
                           uint32_t expected_board_id, char const* expected_device_name,
                           bootloader_image_info_t* info_out);

// expected_device_name in both APIs points to the complete zero-padded
// BOOTLOADER_UPDATE_DEVICE_NAME_SIZE-byte identity field, not a shorter C
// string. The running bootloader passes its own embedded manifest field.

#endif
