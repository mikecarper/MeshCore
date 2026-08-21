#pragma once

#include <stddef.h>
#include <stdint.h>
#include <string.h>

// Reset-retained authorization shared byte-for-byte with OTAFIX. This record is intentionally in MCU RAM,
// not on removable media: the application publishes it only after authenticating/verifying the exact SD
// container, and OTAFIX consumes it before trusting any card geometry or bytes. A power cycle erases it.
namespace mesh {
namespace ota {

static const uintptr_t MOTA_SD_AUTH_ADDR = 0x20006008u;
static const uint32_t MOTA_SD_AUTH_SECTOR_SIZE = 512u;
static const uint16_t MOTA_SD_AUTH_VERSION = 2u;
static const uint16_t MOTA_SD_AUTH_LEN = 72u;
static const uint8_t MOTA_SD_AUTH_PURPOSE_APP = 1u;
static const uint8_t MOTA_SD_AUTH_PURPOSE_BOOTLOADER = 2u;
static const uint8_t MOTA_SD_AUTH_MAGIC[8] = {
  'M', 'O', 'T', 'A', 'S', 'D', 'A', '2'
};

inline uint16_t mota_sd_auth_rd16(const uint8_t* p) {
  return (uint16_t)p[0] | ((uint16_t)p[1] << 8);
}

inline uint32_t mota_sd_auth_rd32(const uint8_t* p) {
  return (uint32_t)p[0] | ((uint32_t)p[1] << 8) |
         ((uint32_t)p[2] << 16) | ((uint32_t)p[3] << 24);
}

inline void mota_sd_auth_wr16(uint8_t* p, uint16_t value) {
  p[0] = (uint8_t)value;
  p[1] = (uint8_t)(value >> 8);
}

inline void mota_sd_auth_wr32(uint8_t* p, uint32_t value) {
  p[0] = (uint8_t)value;
  p[1] = (uint8_t)(value >> 8);
  p[2] = (uint8_t)(value >> 16);
  p[3] = (uint8_t)(value >> 24);
}

inline uint32_t mota_sd_auth_crc32(const uint8_t* data, size_t len) {
  uint32_t crc = 0xFFFFFFFFu;
  for (size_t i = 0; i < len; ++i) {
    crc ^= data[i];
    for (uint8_t bit = 0; bit < 8; ++bit)
      crc = (crc >> 1) ^ (0xEDB88320u & (uint32_t)-(int32_t)(crc & 1u));
  }
  return ~crc;
}

inline bool mota_sd_auth_geometry_valid(uint32_t first_lba, uint32_t sector_count,
                                        uint32_t container_total, uint32_t card_sectors) {
  const uint64_t needed = ((uint64_t)container_total + MOTA_SD_AUTH_SECTOR_SIZE - 1u) /
                          MOTA_SD_AUTH_SECTOR_SIZE;
  return first_lba != 0 && sector_count != 0 && container_total != 0 &&
         card_sectors != 0 && needed == sector_count &&
         first_lba < card_sectors && sector_count <= card_sectors - first_lba;
}

inline bool mota_sd_auth_encode(uint8_t out[MOTA_SD_AUTH_LEN], uint8_t purpose,
                                uint8_t format, uint32_t first_lba,
                                uint32_t sector_count, uint32_t container_total,
                                uint32_t card_sectors,
                                const uint8_t container_sha256[32]) {
  if (!out || !container_sha256 ||
      (purpose != MOTA_SD_AUTH_PURPOSE_APP &&
       purpose != MOTA_SD_AUTH_PURPOSE_BOOTLOADER) ||
      ((purpose == MOTA_SD_AUTH_PURPOSE_APP && format != 2u) ||
       (purpose == MOTA_SD_AUTH_PURPOSE_BOOTLOADER && format != 3u)) ||
      !mota_sd_auth_geometry_valid(first_lba, sector_count, container_total, card_sectors))
    return false;
  memset(out, 0, MOTA_SD_AUTH_LEN);
  memcpy(out, MOTA_SD_AUTH_MAGIC, sizeof(MOTA_SD_AUTH_MAGIC));
  mota_sd_auth_wr16(out + 8u, MOTA_SD_AUTH_VERSION);
  mota_sd_auth_wr16(out + 10u, MOTA_SD_AUTH_LEN);
  out[12] = purpose;
  out[13] = format;
  mota_sd_auth_wr32(out + 16u, first_lba);
  mota_sd_auth_wr32(out + 20u, sector_count);
  mota_sd_auth_wr32(out + 24u, container_total);
  mota_sd_auth_wr32(out + 28u, card_sectors);
  memcpy(out + 32u, container_sha256, 32u);
  const uint32_t crc = mota_sd_auth_crc32(out, 64u);
  mota_sd_auth_wr32(out + 64u, crc);
  mota_sd_auth_wr32(out + 68u, ~crc);
  return true;
}

inline bool mota_sd_auth_valid(const uint8_t record[MOTA_SD_AUTH_LEN]) {
  if (!record || memcmp(record, MOTA_SD_AUTH_MAGIC, sizeof(MOTA_SD_AUTH_MAGIC)) != 0 ||
      mota_sd_auth_rd16(record + 8u) != MOTA_SD_AUTH_VERSION ||
      mota_sd_auth_rd16(record + 10u) != MOTA_SD_AUTH_LEN ||
      mota_sd_auth_rd16(record + 14u) != 0 ||
      ((record[12] == MOTA_SD_AUTH_PURPOSE_APP && record[13] != 2u) ||
       (record[12] == MOTA_SD_AUTH_PURPOSE_BOOTLOADER && record[13] != 3u) ||
       (record[12] != MOTA_SD_AUTH_PURPOSE_APP &&
        record[12] != MOTA_SD_AUTH_PURPOSE_BOOTLOADER)))
    return false;
  const uint32_t crc = mota_sd_auth_rd32(record + 64u);
  return mota_sd_auth_geometry_valid(mota_sd_auth_rd32(record + 16u),
                                     mota_sd_auth_rd32(record + 20u),
                                     mota_sd_auth_rd32(record + 24u),
                                     mota_sd_auth_rd32(record + 28u)) &&
         mota_sd_auth_crc32(record, 64u) == crc &&
         mota_sd_auth_rd32(record + 68u) == ~crc;
}

} // namespace ota
} // namespace mesh
