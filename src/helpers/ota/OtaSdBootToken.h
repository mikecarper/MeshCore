// Internal authorization token for a bootloader package staged on removable SD.
// Keep byte-identical with OTAFIX src/ota_sd_boot_token.h.
#ifndef OTA_SD_BOOT_TOKEN_H_
#define OTA_SD_BOOT_TOKEN_H_

#include <stddef.h>
#include <stdint.h>
#include <string.h>

#define MOTA_SD_BOOT_TOKEN_VERSION     1u
#define MOTA_SD_BOOT_TOKEN_LEN         64u
#define MOTA_SD_BOOT_TOKEN_IMAGE_HASH_OFFSET 24u
#define MOTA_SD_BOOT_TOKEN_CRC_OFFSET  56u

static const uint8_t MOTA_SD_BOOT_TOKEN_MAGIC[8] = {
  'M', 'O', 'T', 'A', 'S', 'D', 'B', 'L'
};

static inline uint32_t mota_sd_boot_token_rd32(const uint8_t *p) {
  return (uint32_t)p[0] | ((uint32_t)p[1] << 8) |
         ((uint32_t)p[2] << 16) | ((uint32_t)p[3] << 24);
}

static inline void mota_sd_boot_token_wr32(uint8_t *p, uint32_t value) {
  p[0] = (uint8_t)value;
  p[1] = (uint8_t)(value >> 8);
  p[2] = (uint8_t)(value >> 16);
  p[3] = (uint8_t)(value >> 24);
}

static inline uint32_t mota_sd_boot_token_crc32(const uint8_t *data, size_t len) {
  uint32_t crc = 0xFFFFFFFFu;
  for (size_t i = 0; i < len; i++) {
    crc ^= data[i];
    for (uint8_t bit = 0; bit < 8; bit++) {
      crc = (crc >> 1) ^ (0xEDB88320u & (uint32_t)-(int32_t)(crc & 1u));
    }
  }
  return ~crc;
}

// image_hash is copied from the exact signed manifest buffer authenticated by
// the application. It binds the only bootloader bytes that the MBR can install.
static inline void mota_sd_boot_token_encode(uint8_t out[MOTA_SD_BOOT_TOKEN_LEN],
                                             uint32_t total,
                                             const uint8_t image_hash[32]) {
  memset(out, 0, MOTA_SD_BOOT_TOKEN_LEN);
  memcpy(out, MOTA_SD_BOOT_TOKEN_MAGIC, sizeof(MOTA_SD_BOOT_TOKEN_MAGIC));
  mota_sd_boot_token_wr32(out + 8u, MOTA_SD_BOOT_TOKEN_VERSION);
  mota_sd_boot_token_wr32(out + 12u, MOTA_SD_BOOT_TOKEN_LEN);
  mota_sd_boot_token_wr32(out + 16u, total);
  mota_sd_boot_token_wr32(out + 20u, ~total);
  memcpy(out + MOTA_SD_BOOT_TOKEN_IMAGE_HASH_OFFSET, image_hash, 32u);
  const uint32_t crc = mota_sd_boot_token_crc32(out, MOTA_SD_BOOT_TOKEN_CRC_OFFSET);
  mota_sd_boot_token_wr32(out + MOTA_SD_BOOT_TOKEN_CRC_OFFSET, crc);
  mota_sd_boot_token_wr32(out + MOTA_SD_BOOT_TOKEN_CRC_OFFSET + 4u, ~crc);
}

static inline int mota_sd_boot_token_valid(const uint8_t token[MOTA_SD_BOOT_TOKEN_LEN],
                                           uint32_t total,
                                           const uint8_t image_hash[32]) {
  const uint32_t crc = mota_sd_boot_token_rd32(token + MOTA_SD_BOOT_TOKEN_CRC_OFFSET);
  return memcmp(token, MOTA_SD_BOOT_TOKEN_MAGIC, sizeof(MOTA_SD_BOOT_TOKEN_MAGIC)) == 0 &&
         mota_sd_boot_token_rd32(token + 8u) == MOTA_SD_BOOT_TOKEN_VERSION &&
         mota_sd_boot_token_rd32(token + 12u) == MOTA_SD_BOOT_TOKEN_LEN &&
         mota_sd_boot_token_rd32(token + 16u) == total &&
         mota_sd_boot_token_rd32(token + 20u) == ~total &&
         memcmp(token + MOTA_SD_BOOT_TOKEN_IMAGE_HASH_OFFSET, image_hash, 32u) == 0 &&
         mota_sd_boot_token_crc32(token, MOTA_SD_BOOT_TOKEN_CRC_OFFSET) == crc &&
         mota_sd_boot_token_rd32(token + MOTA_SD_BOOT_TOKEN_CRC_OFFSET + 4u) == ~crc;
}

#endif // OTA_SD_BOOT_TOKEN_H_
