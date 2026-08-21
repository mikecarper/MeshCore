#pragma once

#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

#include "MotaContainer.h"
#include "Multihash.h"
#include "OtaBlInfo.h"
#include "OtaByteIO.h"

#if defined(OTA_QSPI_BOOTLOADER_UPDATE)
  #if !defined(NRF52_PLATFORM) || !defined(OTA_QSPI_STORE)
    #error "OTA_QSPI_BOOTLOADER_UPDATE requires nRF52 raw-QSPI staging"
  #endif
  #if !defined(XIAO_NRF52) && !defined(IKOKA_NRF52)
    #error "OTA_QSPI_BOOTLOADER_UPDATE is restricted to XIAO nRF52840 module builds"
  #endif
  #if defined(QSPIFLASH) || defined(OTA_SD_STORE)
    #error "bootloader-update QSPI cannot share a filesystem or SD staging configuration"
  #endif
#endif

#if defined(OTA_INTERNAL_BOOTLOADER_UPDATE)
  #if !defined(NRF52_PLATFORM) || !defined(OTA_FLASH_STORE)
    #error "OTA_INTERNAL_BOOTLOADER_UPDATE requires nRF52 internal-flash staging"
  #endif
  #if defined(OTA_QSPI_STORE) || defined(OTA_SD_STORE) || defined(QSPIFLASH)
    #error "internal bootloader staging cannot share an external OTA/filesystem store"
  #endif
#endif

namespace mesh {
namespace ota {

// Mirror of OTAFIX src/usb/uf2/bootloader_image.h. This manifest is embedded in the complete,
// padded bootloader region and is covered both by its own CRC32 and the signed .mota image_hash.
static const uint32_t OTA_BOOT_MANIFEST_MAGIC0 = 0x464D4C42UL; // bytes "BLMF"
static const uint32_t OTA_BOOT_MANIFEST_MAGIC1 = 0x31435243UL; // bytes "CRC1"
static const uint16_t OTA_BOOT_MANIFEST_VERSION = 1;
static const uint16_t OTA_BOOT_MANIFEST_SIZE = 44;
static const uint8_t  OTA_BOOT_DEVICE_NAME_SIZE = 16;
static const uint32_t OTA_BOOT_IMAGE_START = 0x000F4000UL;
static const uint32_t OTA_BOOT_IMAGE_SIZE = 0x0000A000UL; // F4000..FE000, padded to exactly 40 KiB
static const uint32_t OTA_BOOT_SCRATCH_START = 0x000E0000UL;
static const uint32_t OTA_BOOT_SCRATCH_END = 0x000EA000UL;
static const uint32_t OTA_NRF52840_RAM_START = 0x20000000UL;
static const uint32_t OTA_NRF52840_RAM_END   = 0x20040000UL;

// Only the two official XIAO nRF52840 OTAFIX identities are eligible. Runtime matching against the
// installed, CRC-valid manifest distinguishes the base and Sense modules even for carrier-board aliases.
static const uint32_t OTA_XIAO_BOARD_ID_BASE  = 0x28860044UL;
static const uint32_t OTA_XIAO_BOARD_ID_SENSE = 0x28860045UL;
static const uint8_t OTA_XIAO_BOOT_DEVICE_NAME[OTA_BOOT_DEVICE_NAME_SIZE] = {
  'X','I','A','O','_','D','F','U', 0,0,0,0,0,0,0,0
};

struct OtaBootloaderIdentity {
  bool present = false;
  bool crc_ok = false;
  uint32_t manifest_offset = 0;
  uint32_t image_start = 0;
  uint32_t image_size = 0;
  uint32_t board_id = 0;
  char device_name[OTA_BOOT_DEVICE_NAME_SIZE + 1] = {0};
  uint32_t crc32 = 0;
};

// Accumulate a stream of already CRC-validated candidates without silently
// choosing between two privileged identities. Both the memory-mapped and QSPI
// scanners use this helper so their duplicate policy cannot drift.
inline bool ota_bootloader_identity_add_unique(const OtaBootloaderIdentity& candidate,
                                               OtaBootloaderIdentity& selected,
                                               uint8_t& valid_count) {
  if (!candidate.present || !candidate.crc_ok) return true;
  if (valid_count != 0u) {
    selected = OtaBootloaderIdentity();
    return false;
  }
  selected = candidate;
  valid_count = 1u;
  return true;
}

inline uint16_t ota_boot_rd16(const uint8_t* p) {
  return (uint16_t)(p[0] | ((uint16_t)p[1] << 8));
}

inline uint32_t ota_boot_rd32(const uint8_t* p) {
  return (uint32_t)p[0] | ((uint32_t)p[1] << 8) | ((uint32_t)p[2] << 16) |
         ((uint32_t)p[3] << 24);
}

inline bool ota_xiao_bootloader_board_id(uint32_t board_id) {
  return board_id == OTA_XIAO_BOARD_ID_BASE || board_id == OTA_XIAO_BOARD_ID_SENSE;
}

inline bool ota_bootloader_board_id_valid(uint32_t board_id) {
  return board_id != 0u && board_id != UINT32_MAX;
}

// New exact-board manifests use one to fifteen printable ASCII bytes followed by zero padding. Requiring
// a terminator keeps the canonical signed hw_id at 31 bytes or less. The two already-deployed XIAO
// identities retain their exact legacy name and signed hardware IDs.
inline bool ota_bootloader_device_name_valid(uint32_t board_id, const uint8_t name[16]) {
  if (!name) return false;
  if (ota_xiao_bootloader_board_id(board_id))
    return memcmp(name, OTA_XIAO_BOOT_DEVICE_NAME, OTA_BOOT_DEVICE_NAME_SIZE) == 0;
  size_t end = 0;
  while (end < OTA_BOOT_DEVICE_NAME_SIZE && name[end] != 0) {
    if (name[end] < 0x21u || name[end] > 0x7Eu) return false;
    end++;
  }
  if (end == 0 || end == OTA_BOOT_DEVICE_NAME_SIZE) return false;
  for (size_t i = end; i < OTA_BOOT_DEVICE_NAME_SIZE; i++)
    if (name[i] != 0) return false;
  return true;
}

// Legacy XIAO signed .mota hw_id. It is intentionally distinct from application hw_id values and is
// derived from the exact embedded board ID for compatibility with already-deployed packages.
inline bool ota_xiao_bootloader_hw_id(uint32_t board_id, uint8_t out[32]) {
  if (!out || !ota_xiao_bootloader_board_id(board_id)) return false;
  memset(out, 0, 32);
  const char* value = board_id == OTA_XIAO_BOARD_ID_BASE
      ? "XIAO_BL_28860044" : "XIAO_BL_28860045";
  memcpy(out, value, strlen(value));
  return true;
}


// Canonical generic bootloader identity. The board ID alone is not unique (several nRF52 families use
// the same USB VID/PID-derived ID), so the signed identity includes the complete embedded device name.
// Hashing all 32 padded bytes produces the bootloader package's collision-checked wire target ID.
inline bool ota_bootloader_hw_id(uint32_t board_id, const uint8_t device_name[16],
                                 uint8_t out[32]) {
  if (!out || !ota_bootloader_board_id_valid(board_id) ||
      !ota_bootloader_device_name_valid(board_id, device_name)) return false;
  if (ota_xiao_bootloader_board_id(board_id)) return ota_xiao_bootloader_hw_id(board_id, out);
  static const char hex[] = "0123456789ABCDEF";
  memset(out, 0, 32);
  memcpy(out, "NRF_BL_", 7);
  for (uint8_t i = 0; i < 8; i++)
    out[7 + i] = (uint8_t)hex[(board_id >> (28u - 4u * i)) & 0x0Fu];
  out[15] = '_';
  size_t n = 0;
  while (n < 15 && device_name[n]) n++;
  memcpy(out + 16, device_name, n);
  return true;
}

inline bool ota_bootloader_hw_id(const OtaBootloaderIdentity& identity, uint8_t out[32]);

inline uint32_t ota_bootloader_target_id(uint32_t board_id, const uint8_t device_name[16]) {
  if (ota_xiao_bootloader_board_id(board_id)) return board_id;
  uint8_t hw_id[32], digest[4];
  if (!ota_bootloader_hw_id(board_id, device_name, hw_id)) return 0;
  mh4(digest, hw_id, sizeof(hw_id));
  return rd_u32le(digest);
}

inline uint32_t ota_boot_crc32_update(uint32_t crc, uint8_t value) {
  crc ^= value;
  for (uint8_t bit = 0; bit < 8; bit++)
    crc = (crc >> 1) ^ (0xEDB88320UL & (uint32_t)-(int32_t)(crc & 1U));
  return crc;
}

inline uint32_t ota_boot_image_crc32(const uint8_t* image, size_t image_size,
                                     size_t crc_offset) {
  if (!image || crc_offset > image_size || image_size - crc_offset < sizeof(uint32_t)) return 0;
  uint32_t crc = UINT32_MAX;
  for (size_t i = 0; i < image_size; i++) {
    const uint8_t value = (i >= crc_offset && i < crc_offset + sizeof(uint32_t)) ? 0 : image[i];
    crc = ota_boot_crc32_update(crc, value);
  }
  return ~crc;
}

// Parse one candidate header. The caller independently validates the complete-region CRC so a magic
// string in a literal pool cannot establish an identity.
inline bool ota_bootloader_manifest_parse(const uint8_t* raw, uint32_t raw_offset,
                                          OtaBootloaderIdentity& out) {
  if (!raw || ota_boot_rd32(raw) != OTA_BOOT_MANIFEST_MAGIC0 ||
      ota_boot_rd32(raw + 4) != OTA_BOOT_MANIFEST_MAGIC1 ||
      ota_boot_rd16(raw + 8) != OTA_BOOT_MANIFEST_VERSION ||
      ota_boot_rd16(raw + 10) != OTA_BOOT_MANIFEST_SIZE ||
      ota_boot_rd32(raw + 12) != OTA_BOOT_IMAGE_START ||
      ota_boot_rd32(raw + 16) != OTA_BOOT_IMAGE_SIZE ||
      !ota_bootloader_board_id_valid(ota_boot_rd32(raw + 20)) ||
      !ota_bootloader_device_name_valid(ota_boot_rd32(raw + 20), raw + 24)) return false;

  out = OtaBootloaderIdentity();
  out.present = true;
  out.manifest_offset = raw_offset;
  out.image_start = ota_boot_rd32(raw + 12);
  out.image_size = ota_boot_rd32(raw + 16);
  out.board_id = ota_boot_rd32(raw + 20);
  memcpy(out.device_name, raw + 24, OTA_BOOT_DEVICE_NAME_SIZE);
  out.device_name[OTA_BOOT_DEVICE_NAME_SIZE] = 0;
  out.crc32 = ota_boot_rd32(raw + 40);
  return true;
}

inline bool ota_bootloader_hw_id(const OtaBootloaderIdentity& identity, uint8_t out[32]) {
  return identity.present && identity.crc_ok &&
         ota_bootloader_hw_id(identity.board_id,
                              reinterpret_cast<const uint8_t*>(identity.device_name), out);
}

inline uint32_t ota_bootloader_target_id(const OtaBootloaderIdentity& identity) {
  return identity.present && identity.crc_ok
      ? ota_bootloader_target_id(identity.board_id,
                                 reinterpret_cast<const uint8_t*>(identity.device_name)) : 0;
}

// Validate an installed memory-mapped bootloader and recover its exact identity. Scan on word
// boundaries to mirror OTAFIX's self-update validator.
inline bool ota_bootloader_identity_from_image(const uint8_t* image, size_t image_size,
                                               OtaBootloaderIdentity& out) {
  out = OtaBootloaderIdentity();
  if (!image || image_size != OTA_BOOT_IMAGE_SIZE) return false;
  uint8_t valid_count = 0;
  for (size_t offset = 0; offset + OTA_BOOT_MANIFEST_SIZE <= image_size; offset += 4) {
    OtaBootloaderIdentity candidate;
    if (!ota_bootloader_manifest_parse(image + offset, (uint32_t)offset, candidate)) continue;
    candidate.crc_ok = ota_boot_image_crc32(image, image_size, offset + 40) == candidate.crc32;
    if (!candidate.crc_ok) continue;
    if (!ota_bootloader_identity_add_unique(candidate, out, valid_count)) return false;
  }
  return valid_count == 1u;
}

inline bool ota_bootloader_identity_matches(const OtaBootloaderIdentity& a,
                                            const OtaBootloaderIdentity& b) {
  return a.present && a.crc_ok && b.present && b.crc_ok &&
         a.image_start == b.image_start && a.image_size == b.image_size &&
         a.board_id == b.board_id &&
         memcmp(a.device_name, b.device_name, OTA_BOOT_DEVICE_NAME_SIZE) == 0;
}

// Cheap first-line rejection of a malformed raw bootloader region. OTAFIX repeats this check before
// touching internal flash. MSP may point one byte beyond RAM; the reset vector must be Thumb code in
// the exact bootloader image range.
inline bool ota_bootloader_vector_sane(const uint8_t vectors[8]) {
  if (!vectors) return false;
  const uint32_t sp = ota_boot_rd32(vectors);
  const uint32_t reset = ota_boot_rd32(vectors + 4);
  const uint32_t entry = reset & ~1UL;
  return (sp & 7u) == 0 && sp >= OTA_NRF52840_RAM_START && sp <= OTA_NRF52840_RAM_END &&
         (reset & 1u) != 0 && entry >= OTA_BOOT_IMAGE_START &&
         entry < OTA_BOOT_IMAGE_START + OTA_BOOT_IMAGE_SIZE;
}

struct OtaBootloaderCapsMarker {
  bool present = false;
  uint16_t apply_abi = 0;
  uint16_t codec_mask = 0;
  uint8_t storage_flags = 0;
};

inline bool ota_bootloader_caps_marker_parse(const uint8_t raw[16],
                                              OtaBootloaderCapsMarker& out) {
  static const uint8_t magic[8] = {'M','O','T','A','B','L','D','R'};
  if (!raw || memcmp(raw, magic, sizeof(magic)) != 0 ||
      raw[8] == 0 || (raw[8] == 0xFF && raw[9] == 0xFF) ||
      (raw[10] == 0 && raw[11] == 0) || (raw[12] & ~OTA_BL_STORAGE_KNOWN) != 0 ||
      raw[13] != 0 || raw[14] != 0 || raw[15] != 0) return false;
  out.present = true;
  out.apply_abi = ota_boot_rd16(raw + 8);
  out.codec_mask = ota_boot_rd16(raw + 10);
  out.storage_flags = raw[12];
  return true;
}

inline bool ota_bootloader_caps_from_image(const uint8_t* image, size_t image_size,
                                            uint8_t exact_storage_flags,
                                            OtaBootloaderCapsMarker& out) {
  out = OtaBootloaderCapsMarker();
  if (!image || exact_storage_flags == 0) return false;
  uint8_t valid_count = 0;
  for (size_t off = 0; off + 16u <= image_size; off += 4u) {
    OtaBootloaderCapsMarker parsed;
    if (!ota_bootloader_caps_marker_parse(image + off, parsed) ||
        parsed.apply_abi < MOTA_BOOT_FORMAT_VER ||
        (parsed.codec_mask & (1u << CODEC_FULL)) == 0 ||
        (parsed.storage_flags & OTA_BL_STORAGE_BOOT_UPDATE) == 0) continue;
    if (++valid_count != 1u) return false; // privileged marker identity must be unambiguous
    if (parsed.storage_flags != exact_storage_flags) continue;
    out = parsed;
  }
  return valid_count == 1u && out.present;
}

enum OtaBootloaderConfirmGate : uint8_t {
  OTA_BOOT_CONFIRM_OK = 0,
  OTA_BOOT_CONFIRM_NOT_BOOT_PACKAGE,
  OTA_BOOT_CONFIRM_GEOMETRY,
  OTA_BOOT_CONFIRM_LOCAL_IDENTITY,
  OTA_BOOT_CONFIRM_TARGET,
  OTA_BOOT_CONFIRM_HW_ID,
  OTA_BOOT_CONFIRM_MID,
  OTA_BOOT_CONFIRM_IMAGE_HASH,
};

// Pure explicit-confirmation gate used by the device command and native tests. Cryptographic and
// candidate-image checks run immediately after this structural/typed gate.
inline OtaBootloaderConfirmGate ota_bootloader_confirmation_gate(
    const MotaManifest& m, const OtaBootloaderIdentity& installed,
    const uint8_t actual_mid[4], const uint8_t operator_mid[4],
    const uint8_t operator_hash8[8]) {
  if (m.format_ver != MOTA_BOOT_FORMAT_VER ||
      m.flags != (MFLAG_FULL | MFLAG_SIGNED | MFLAG_BOOTLOADER))
    return OTA_BOOT_CONFIRM_NOT_BOOT_PACKAGE;
  static const uint8_t zero_base[8] = {0};
  if (m.codec_id != CODEC_FULL || m.image_size != OTA_BOOT_IMAGE_SIZE ||
      m.payload_size != OTA_BOOT_IMAGE_SIZE || m.block_size_log2 != 10 ||
      m.block_count != OTA_BOOT_IMAGE_SIZE / 1024u || !m.base_hash ||
      memcmp(m.base_hash, zero_base, sizeof(zero_base)) != 0)
    return OTA_BOOT_CONFIRM_GEOMETRY;
  if (!installed.present || !installed.crc_ok ||
      !ota_bootloader_board_id_valid(installed.board_id) ||
      !ota_bootloader_device_name_valid(
          installed.board_id, reinterpret_cast<const uint8_t*>(installed.device_name)))
    return OTA_BOOT_CONFIRM_LOCAL_IDENTITY;
  if (m.target_id != ota_bootloader_target_id(installed)) return OTA_BOOT_CONFIRM_TARGET;
  uint8_t expected_hw[32];
  if (!ota_bootloader_hw_id(installed, expected_hw) || !m.hw_id ||
      memcmp(m.hw_id, expected_hw, sizeof(expected_hw)) != 0)
    return OTA_BOOT_CONFIRM_HW_ID;
  if (!actual_mid || !operator_mid || memcmp(actual_mid, operator_mid, 4) != 0 ||
      !m.merkle_root || memcmp(m.merkle_root, actual_mid, 4) != 0)
    return OTA_BOOT_CONFIRM_MID;
  if (!operator_hash8 || !m.image_hash || memcmp(m.image_hash, operator_hash8, 8) != 0)
    return OTA_BOOT_CONFIRM_IMAGE_HASH;
  return OTA_BOOT_CONFIRM_OK;
}

} // namespace ota
} // namespace mesh
