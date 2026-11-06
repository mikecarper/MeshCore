#include "bootloader_image.h"

#include <stddef.h>
#include <string.h>

uint32_t bootloader_image_crc32(uint8_t const* image, size_t image_size, size_t crc_offset) {
  uint32_t crc = UINT32_MAX;

  for (size_t i = 0; i < image_size; i++) {
    uint8_t const value = (i >= crc_offset && i < crc_offset + sizeof(uint32_t)) ? 0 : image[i];
    crc ^= value;
    for (uint8_t bit = 0; bit < 8; bit++) {
      crc = (crc >> 1) ^ (0xEDB88320UL & (uint32_t)-(int32_t)(crc & 1U));
    }
  }

  return ~crc;
}

static bool bootloader_device_name_valid(uint32_t board_id, char const* name) {
  static char const xiao_name[BOOTLOADER_UPDATE_DEVICE_NAME_SIZE] = "XIAO_DFU";
  if (board_id == 0u || board_id == UINT32_MAX) {
    return false;
  }
  if (board_id == 0x28860044u || board_id == 0x28860045u) {
    return memcmp(name, xiao_name, sizeof(xiao_name)) == 0;
  }
  size_t end = 0;
  while (end < BOOTLOADER_UPDATE_DEVICE_NAME_SIZE && name[end] != 0) {
    uint8_t const value = (uint8_t)name[end++];
    if (value < 0x21u || value > 0x7Eu) {
      return false;
    }
  }
  if (end == 0 || end == BOOTLOADER_UPDATE_DEVICE_NAME_SIZE) {
    return false;
  }
  while (end < BOOTLOADER_UPDATE_DEVICE_NAME_SIZE) {
    if (name[end++] != 0) {
      return false;
    }
  }
  return true;
}

bool bootloader_extension_validate(bootloader_update_extension_t const* extension) {
  uint32_t const version = extension->boot_version;
  return extension->magic0 == BOOTLOADER_UPDATE_EXTENSION_MAGIC0 &&
         extension->magic1 == BOOTLOADER_UPDATE_EXTENSION_MAGIC1 &&
         extension->version == BOOTLOADER_UPDATE_EXTENSION_VERSION &&
         extension->header_size == sizeof(*extension) && version != UINT32_MAX &&
         (version & 0xFFu) != 0u && extension->softdevice_family != 0u &&
         extension->softdevice_fwid != 0u && extension->app_base != 0u &&
         extension->layout_abi != 0u && extension->compat_flags == 0u &&
         extension->reserved == 0u;
}

bool bootloader_image_info(uint8_t const* image, uint32_t image_start, uint32_t image_size,
                           uint32_t expected_board_id, char const* expected_device_name,
                           bootloader_image_info_t* info_out) {
  if (image == NULL || image_size < sizeof(bootloader_update_envelope_t) ||
      expected_device_name == NULL) {
    return false;
  }
  uint32_t const initial_sp = (uint32_t)image[0] | ((uint32_t)image[1] << 8) |
                              ((uint32_t)image[2] << 16) | ((uint32_t)image[3] << 24);
  uint32_t const reset = (uint32_t)image[4] | ((uint32_t)image[5] << 8) |
                         ((uint32_t)image[6] << 16) | ((uint32_t)image[7] << 24);
#if defined(NRF52833_XXAA)
  uint32_t const ram_end = 0x20020000UL;
#else
  uint32_t const ram_end = 0x20040000UL;
#endif
  uint32_t const reset_addr = reset & ~1UL;
  if (image_size > UINT32_MAX - image_start ||
      (initial_sp & 7U) != 0 || initial_sp < 0x20000000UL || initial_sp > ram_end ||
      (reset & 1U) == 0 || reset_addr < image_start ||
      reset_addr >= image_start + image_size) {
    return false;
  }

  uint32_t const manifest_offset = image_size - sizeof(bootloader_update_envelope_t);
  static uint8_t const manifest_magic[8] = {'B','L','M','F','C','R','C','1'};
  bootloader_update_envelope_t envelope;
  uint32_t valid_offset = UINT32_MAX;
  // Scan aligned legacy identities and require exactly one complete-region
  // CRC match. BLMF is deliberately counted independently of adjacent bytes,
  // preserving preview.12's base-first bootstrap semantics; the sole identity
  // is required to have a valid canonical BLM2 below.
  for (uint32_t offset = 0;
       offset + sizeof(bootloader_update_manifest_t) <= image_size;
       offset += sizeof(uint32_t)) {
    uint8_t const* candidate = image + offset;
    if (memcmp(candidate, manifest_magic, sizeof(manifest_magic)) != 0) {
      continue;
    }
    memcpy(&envelope.manifest, candidate, sizeof(envelope.manifest));
    if (envelope.manifest.version != BOOTLOADER_UPDATE_MANIFEST_VERSION ||
        envelope.manifest.header_size != sizeof(bootloader_update_manifest_t) ||
        envelope.manifest.image_start != image_start ||
        envelope.manifest.image_size != image_size ||
        !bootloader_device_name_valid(envelope.manifest.board_id,
                                      envelope.manifest.device_name)) {
      continue;
    }
    if (bootloader_image_crc32(
          image, image_size,
          offset + offsetof(bootloader_update_manifest_t, crc32)) != envelope.manifest.crc32) {
      continue;
    }
    if (valid_offset != UINT32_MAX) {
      return false;
    }
    valid_offset = offset;
  }
  if (valid_offset != manifest_offset) {
    return false;
  }

  // The sole valid identity must be the canonical final BLMF+BLM2 envelope.
  // Copying also makes unaligned .mota payloads safe when UNALIGN_TRP is set.
  memcpy(&envelope, image + manifest_offset, sizeof(envelope));
  bootloader_update_extension_t const* extension = &envelope.extension;
  if (envelope.manifest.board_id != expected_board_id ||
      memcmp(envelope.manifest.device_name, expected_device_name,
             BOOTLOADER_UPDATE_DEVICE_NAME_SIZE) != 0 ||
      !bootloader_extension_validate(extension)) {
    return false;
  }

  if (info_out) {
    info_out->boot_version = extension->boot_version;
    info_out->softdevice_family = extension->softdevice_family;
    info_out->softdevice_fwid = extension->softdevice_fwid;
    info_out->app_base = extension->app_base;
    info_out->layout_abi = extension->layout_abi;
  }
  return true;
}

bool bootloader_image_validate(uint8_t const* image, uint32_t image_start, uint32_t image_size,
                               uint32_t expected_board_id, char const* expected_device_name) {
  return bootloader_image_info(image, image_start, image_size, expected_board_id,
                               expected_device_name, NULL);
}
