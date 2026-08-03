#pragma once

#include <stdint.h>
#include <stddef.h>

// On-card handoff shared with Adafruit_nRF52_Bootloader_OTAFIX. The staged
// .mota is a normal, contiguous filesystem file. Sector 1 is in the unused
// gap between the MBR and the first partition and tells the bootloader where
// that file's sectors live. Cards without such a gap are rejected.

namespace mesh {
namespace ota {

static const uint32_t MOTA_SD_SECTOR_SIZE = 512u;
static const uint32_t MOTA_SD_HANDOFF_SECTOR = 1u;
static const uint32_t MOTA_SD_HANDOFF_VERSION = 1u;
static const uint32_t MOTA_SD_HANDOFF_LEN = 36u;
static const uint8_t MOTA_SD_HANDOFF_MAGIC[8] = {
  'M', 'O', 'T', 'A', 'S', 'D', '0', '1'
};

inline uint32_t mota_sd_rd32(const uint8_t* p) {
  return (uint32_t)p[0] | ((uint32_t)p[1] << 8) |
         ((uint32_t)p[2] << 16) | ((uint32_t)p[3] << 24);
}

inline void mota_sd_wr32(uint8_t* p, uint32_t v) {
  p[0] = (uint8_t)v;
  p[1] = (uint8_t)(v >> 8);
  p[2] = (uint8_t)(v >> 16);
  p[3] = (uint8_t)(v >> 24);
}

inline uint32_t mota_sd_crc32(const uint8_t* data, size_t len) {
  uint32_t crc = 0xFFFFFFFFu;
  for (size_t i = 0; i < len; i++) {
    crc ^= data[i];
    for (uint8_t bit = 0; bit < 8; bit++) {
      crc = (crc >> 1) ^ (0xEDB88320u & (uint32_t)-(int32_t)(crc & 1u));
    }
  }
  return ~crc;
}

inline void mota_sd_encode_handoff(uint8_t sector[MOTA_SD_SECTOR_SIZE],
                                   uint32_t first_sector,
                                   uint32_t sector_count,
                                   uint32_t total_size,
                                   uint32_t card_sectors) {
  // Only own the record bytes. The caller preserves the rest of sector 1 in
  // case a card formatter placed non-partition metadata there.
  for (uint32_t i = 0; i < MOTA_SD_HANDOFF_LEN; i++) sector[i] = 0xFF;
  for (uint8_t i = 0; i < 8; i++) sector[i] = MOTA_SD_HANDOFF_MAGIC[i];
  mota_sd_wr32(sector + 8, MOTA_SD_HANDOFF_VERSION);
  mota_sd_wr32(sector + 12, first_sector);
  mota_sd_wr32(sector + 16, sector_count);
  mota_sd_wr32(sector + 20, total_size);
  mota_sd_wr32(sector + 24, ~total_size);
  mota_sd_wr32(sector + 28, card_sectors);
  mota_sd_wr32(sector + 32, mota_sd_crc32(sector, 32));
}

} // namespace ota
} // namespace mesh
