#pragma once

#include <stddef.h>
#include <stdint.h>
#include <string.h>

#include "OtaFlashLayout_nrf52.h"

// Reset-retained authorization for the nRF52840 hybrid internal-flash/SRAM
// staging source. The application publishes this record only after streaming
// verification and approval. OTAFIX copies and consumes it before trusting any
// retained bytes. A power loss destroys the SRAM suffix and therefore makes
// the hash check fail closed before the first application write.

namespace mesh {
namespace ota {

static const uintptr_t MOTA_HYBRID_AUTH_ADDR = 0x20006008u;
static const uint16_t MOTA_HYBRID_AUTH_VERSION = 1u;
static const uint16_t MOTA_HYBRID_AUTH_LEN = 72u;
static const uint8_t MOTA_HYBRID_AUTH_PURPOSE_APP = 1u;
static const uint8_t MOTA_HYBRID_AUTH_FORMAT_APP = 2u;
static const uint8_t MOTA_HYBRID_AUTH_MAGIC[8] = {
    'M', 'O', 'T', 'A', 'H', 'Y', 'B', '1'};

static const uint8_t MOTA_RAM_CAP_MAGIC[8] = {
    'M', 'O', 'T', 'A', 'R', 'A', 'M', 'A'};
static const uint16_t MOTA_RAM_CAP_ABI = 1u;
static const uint16_t MOTA_RAM_CAP_RECORD_LEN = MOTA_HYBRID_AUTH_LEN;

static_assert(MOTA_HYBRID_AUTH_ADDR + MOTA_HYBRID_AUTH_LEN == 0x20006050u,
              "hybrid auth must occupy the dedicated retained-RAM tail");
static_assert(MOTA_NRF52_HYBRID_RAM_START + MOTA_NRF52_HYBRID_RAM_SIZE ==
                  0x20040000u,
              "hybrid arena must occupy the top 64 KiB of nRF52840 SRAM");

inline uint16_t mota_hybrid_rd16(const uint8_t* p) {
  return (uint16_t)p[0] | ((uint16_t)p[1] << 8);
}

inline uint32_t mota_hybrid_rd32(const uint8_t* p) {
  return (uint32_t)p[0] | ((uint32_t)p[1] << 8) |
         ((uint32_t)p[2] << 16) | ((uint32_t)p[3] << 24);
}

inline void mota_hybrid_wr16(uint8_t* p, uint16_t value) {
  p[0] = (uint8_t)value;
  p[1] = (uint8_t)(value >> 8);
}

inline void mota_hybrid_wr32(uint8_t* p, uint32_t value) {
  p[0] = (uint8_t)value;
  p[1] = (uint8_t)(value >> 8);
  p[2] = (uint8_t)(value >> 16);
  p[3] = (uint8_t)(value >> 24);
}

inline uint32_t mota_hybrid_crc32(const uint8_t* data, size_t len) {
  uint32_t crc = 0xFFFFFFFFu;
  for (size_t i = 0; i < len; ++i) {
    crc ^= data[i];
    for (uint8_t bit = 0; bit < 8u; ++bit)
      crc = (crc >> 1) ^
            (0xEDB88320u & (uint32_t)-(int32_t)(crc & 1u));
  }
  return ~crc;
}

inline bool mota_hybrid_auth_geometry_valid(uint32_t total,
                                             uint32_t flash_start,
                                             uint32_t flash_len,
                                             uint32_t ram_len) {
  if (total < MOTA_NRF52_CONTAINER_MIN_SIZE ||
      flash_len < MOTA_NRF52_FLASH_PAGE ||
      (flash_start & (MOTA_NRF52_FLASH_PAGE - 1u)) != 0u ||
      (flash_len & (MOTA_NRF52_FLASH_PAGE - 1u)) != 0u ||
      flash_start > UINT32_MAX - flash_len ||
      (uint64_t)flash_len + ram_len != total || ram_len == 0u ||
      ram_len > MOTA_NRF52_HYBRID_RAM_SIZE) {
    return false;
  }

  uint32_t flash_needed = total > MOTA_NRF52_HYBRID_RAM_SIZE
      ? total - MOTA_NRF52_HYBRID_RAM_SIZE : 0u;
  if (flash_needed < MOTA_NRF52_FLASH_PAGE)
    flash_needed = MOTA_NRF52_FLASH_PAGE;
  if (flash_needed > UINT32_MAX - (MOTA_NRF52_FLASH_PAGE - 1u))
    return false;
  const uint32_t expected_flash_len =
      (flash_needed + MOTA_NRF52_FLASH_PAGE - 1u) &
      ~(MOTA_NRF52_FLASH_PAGE - 1u);
  if (flash_len != expected_flash_len || flash_len >= total)
    return false;
  // Hybrid staging is qualified only for dedicated no-ExtraFS profiles. It
  // must never reinterpret a legacy D4000 handoff as permission to use SRAM.
  return flash_start + flash_len ==
         MOTA_NRF52_STAGE_CEILING_EXPANDED;
}

inline bool mota_hybrid_auth_encode(
    uint8_t out[MOTA_HYBRID_AUTH_LEN], uint32_t total,
    uint32_t flash_start, uint32_t flash_len, uint32_t ram_len,
    const uint8_t normalized_container_sha256[32]) {
  if (!out || !normalized_container_sha256 ||
      !mota_hybrid_auth_geometry_valid(
          total, flash_start, flash_len, ram_len)) {
    return false;
  }
  memset(out, 0, MOTA_HYBRID_AUTH_LEN);
  memcpy(out, MOTA_HYBRID_AUTH_MAGIC, sizeof(MOTA_HYBRID_AUTH_MAGIC));
  mota_hybrid_wr16(out + 8u, MOTA_HYBRID_AUTH_VERSION);
  mota_hybrid_wr16(out + 10u, MOTA_HYBRID_AUTH_LEN);
  out[12] = MOTA_HYBRID_AUTH_PURPOSE_APP;
  out[13] = MOTA_HYBRID_AUTH_FORMAT_APP;
  mota_hybrid_wr32(out + 16u, total);
  mota_hybrid_wr32(out + 20u, flash_start);
  mota_hybrid_wr32(out + 24u, flash_len);
  mota_hybrid_wr32(out + 28u, ram_len);
  memcpy(out + 32u, normalized_container_sha256, 32u);
  const uint32_t crc = mota_hybrid_crc32(out, 64u);
  mota_hybrid_wr32(out + 64u, crc);
  mota_hybrid_wr32(out + 68u, ~crc);
  return true;
}

inline bool mota_hybrid_auth_valid(
    const uint8_t record[MOTA_HYBRID_AUTH_LEN]) {
  if (!record ||
      memcmp(record, MOTA_HYBRID_AUTH_MAGIC,
             sizeof(MOTA_HYBRID_AUTH_MAGIC)) != 0 ||
      mota_hybrid_rd16(record + 8u) != MOTA_HYBRID_AUTH_VERSION ||
      mota_hybrid_rd16(record + 10u) != MOTA_HYBRID_AUTH_LEN ||
      record[12] != MOTA_HYBRID_AUTH_PURPOSE_APP ||
      record[13] != MOTA_HYBRID_AUTH_FORMAT_APP ||
      mota_hybrid_rd16(record + 14u) != 0u) {
    return false;
  }
  const uint32_t crc = mota_hybrid_rd32(record + 64u);
  return mota_hybrid_auth_geometry_valid(
             mota_hybrid_rd32(record + 16u),
             mota_hybrid_rd32(record + 20u),
             mota_hybrid_rd32(record + 24u),
             mota_hybrid_rd32(record + 28u)) &&
         mota_hybrid_crc32(record, 64u) == crc &&
         mota_hybrid_rd32(record + 68u) == ~crc;
}

}  // namespace ota
}  // namespace mesh
