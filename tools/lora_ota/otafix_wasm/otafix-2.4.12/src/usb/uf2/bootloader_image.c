#include "bootloader_image.h"
#include "crc32.h"

#include <stddef.h>
#include <string.h>

uint32_t bootloader_image_crc32(uint8_t const* image, size_t image_size, size_t crc_offset) {
  // Stream through the shared CRC loop, replacing only the manifest's CRC
  // field with zeros. Out-of-range (including wrapped) fields mask no bytes.
  if (crc_offset >= image_size || crc_offset > SIZE_MAX - sizeof(uint32_t)) {
    return otafix_crc32_update(0, image, image_size);
  }
  static const uint8_t zero[sizeof(uint32_t)] = {0};
  const size_t tail = image_size - crc_offset;
  const size_t masked = tail < sizeof(zero) ? tail : sizeof(zero);
  uint32_t crc = otafix_crc32_update(0, image, crc_offset);
  crc = otafix_crc32_update(crc, zero, masked);
  return otafix_crc32_update(crc, image + crc_offset + masked, tail - masked);
}

static uint32_t read_le32(uint8_t const* data) {
  return (uint32_t)data[0] | ((uint32_t)data[1] << 8) |
         ((uint32_t)data[2] << 16) | ((uint32_t)data[3] << 24);
}

bool bootloader_image_vectors_valid(uint8_t const* image, uint32_t image_size,
                                    uint32_t code_start, uint32_t code_size) {
#if defined(NRF52832_XXAA)
  uint32_t const ram_end = 0x20010000UL;
#elif defined(NRF52833_XXAA)
  uint32_t const ram_end = 0x20020000UL;
#else
  uint32_t const ram_end = 0x20040000UL;
#endif
  if (image_size < 8U || code_size < 8U || code_start > UINT32_MAX - code_size) {
    return false;
  }

  uint32_t const initial_sp = read_le32(image);
  uint32_t const reset = read_le32(image + sizeof(uint32_t));
  uint32_t const reset_addr = reset & ~1UL;
  return (initial_sp & 7U) == 0U && initial_sp >= 0x20000000UL &&
         initial_sp <= ram_end && (reset & 1U) != 0U &&
         reset_addr >= code_start && reset_addr < code_start + code_size;
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

#if RECOVERY_ALLOW_ALL_BOARDS
static bool recovery_identity_valid(bootloader_update_manifest_t const* manifest) {
  // Identity is opaque in a bridge, but exclude blank/erased literal-pool
  // decoys and require a bounded name. The complete field remains CRC-covered.
  uint8_t const first = (uint8_t)manifest->device_name[0];
  return manifest->board_id != 0U && manifest->board_id != UINT32_MAX &&
         first >= 0x21U && first <= 0x7EU &&
         manifest->device_name[BOOTLOADER_UPDATE_DEVICE_NAME_SIZE - 1U] == '\0';
}
#endif

static bool bootloader_manifest_find(uint8_t const* image, uint32_t image_start,
                                     uint32_t image_size, uint32_t expected_board_id,
                                     char const* expected_device_name,
                                     uint32_t* manifest_offset_out, bool allow_other_boards) {
  if (image == NULL || image_size < sizeof(bootloader_update_manifest_t) ||
      expected_device_name == NULL) {
    return false;
  }
  if (image_size > UINT32_MAX - image_start ||
      !bootloader_image_vectors_valid(image, image_size, image_start, image_size)) {
    return false;
  }

  static uint8_t const manifest_magic[8] = {'B','L','M','F','C','R','C','1'};
  uint32_t valid_offset = UINT32_MAX;
  // Scan aligned legacy identities and require exactly one permitted,
  // complete-region CRC match. BLMF is deliberately independent of adjacent
  // bytes, preserving every released recovery layout.
  for (uint32_t offset = 0;
       offset + sizeof(bootloader_update_manifest_t) <= image_size;
       offset += sizeof(uint32_t)) {
    uint8_t const* candidate = image + offset;
    if (memcmp(candidate, manifest_magic, sizeof(manifest_magic)) != 0) {
      continue;
    }
    bootloader_update_manifest_t candidate_manifest;
    memcpy(&candidate_manifest, candidate, sizeof(candidate_manifest));
    if (candidate_manifest.version != BOOTLOADER_UPDATE_MANIFEST_VERSION ||
        candidate_manifest.header_size != sizeof(bootloader_update_manifest_t) ||
        candidate_manifest.image_start != image_start ||
        candidate_manifest.image_size != image_size) {
      continue;
    }
    bool identity_matches;
#if RECOVERY_ALLOW_ALL_BOARDS
    if (allow_other_boards) {
      identity_matches = recovery_identity_valid(&candidate_manifest);
    } else {
#else
    (void)allow_other_boards;
    {
#endif
      identity_matches = candidate_manifest.board_id == expected_board_id &&
                         memcmp(candidate_manifest.device_name, expected_device_name,
                                BOOTLOADER_UPDATE_DEVICE_NAME_SIZE) == 0;
    }
    if (!identity_matches) {
      continue;
    }
    if (bootloader_image_crc32(
          image, image_size,
          offset + offsetof(bootloader_update_manifest_t, crc32)) != candidate_manifest.crc32) {
      continue;
    }
    if (valid_offset != UINT32_MAX) {
      return false;
    }
    valid_offset = offset;
  }
  if (valid_offset == UINT32_MAX) {
    return false;
  }

  if (manifest_offset_out) {
    *manifest_offset_out = valid_offset;
  }
  return true;
}

static bootloader_image_format_t bootloader_image_classify_impl(
  uint8_t const* image, uint32_t image_start, uint32_t image_size,
  uint32_t expected_board_id, char const* expected_device_name,
  bootloader_image_info_t* info_out, bool allow_other_boards) {
  uint32_t manifest_offset;
  if (!bootloader_manifest_find(image, image_start, image_size, expected_board_id,
                                expected_device_name, &manifest_offset, allow_other_boards)) {
    return BOOTLOADER_IMAGE_INVALID;
  }
  if (image_size < sizeof(bootloader_update_envelope_t) ||
      manifest_offset != image_size - sizeof(bootloader_update_envelope_t)) {
    return BOOTLOADER_IMAGE_LEGACY;
  }

  // A v2 identity is authoritative only as the canonical final BLMF+BLM2
  // envelope. Copying keeps unaligned .mota payloads safe with UNALIGN_TRP.
  bootloader_update_extension_t extension;
  memcpy(&extension, image + manifest_offset + sizeof(bootloader_update_manifest_t),
         sizeof(extension));
  if (!bootloader_extension_validate(&extension)) {
    return BOOTLOADER_IMAGE_INVALID;
  }

  if (info_out) {
    info_out->boot_version = extension.boot_version;
    info_out->softdevice_family = extension.softdevice_family;
    info_out->softdevice_fwid = extension.softdevice_fwid;
    info_out->app_base = extension.app_base;
    info_out->layout_abi = extension.layout_abi;
  }
  return BOOTLOADER_IMAGE_V2;
}

bootloader_image_format_t bootloader_image_classify(
  uint8_t const* image, uint32_t image_start, uint32_t image_size,
  uint32_t expected_board_id, char const* expected_device_name,
  bootloader_image_info_t* info_out) {
  return bootloader_image_classify_impl(image, image_start, image_size, expected_board_id,
                                        expected_device_name, info_out, false);
}

bootloader_image_format_t bootloader_image_classify_manual(
  uint8_t const* image, uint32_t image_start, uint32_t image_size,
  uint32_t expected_board_id, char const* expected_device_name,
  bootloader_image_info_t* info_out) {
  return bootloader_image_classify_impl(image, image_start, image_size, expected_board_id,
                                        expected_device_name, info_out, RECOVERY_ALLOW_ALL_BOARDS);
}

bool bootloader_image_info(uint8_t const* image, uint32_t image_start, uint32_t image_size,
                           uint32_t expected_board_id, char const* expected_device_name,
                           bootloader_image_info_t* info_out) {
  return bootloader_image_classify(image, image_start, image_size, expected_board_id,
                                   expected_device_name, info_out) ==
         BOOTLOADER_IMAGE_V2;
}

bool bootloader_image_validate(uint8_t const* image, uint32_t image_start, uint32_t image_size,
                               uint32_t expected_board_id, char const* expected_device_name) {
  return bootloader_manifest_find(image, image_start, image_size, expected_board_id,
                                  expected_device_name, NULL, false);
}
