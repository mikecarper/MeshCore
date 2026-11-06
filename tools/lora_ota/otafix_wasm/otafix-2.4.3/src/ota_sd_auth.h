// Retained-RAM authorization and source handoff for SD-backed .mota apply.
//
// The running application writes this record only after authenticating the
// exact container whose normalized SHA-256 it records, then performs a system
// reset.  OTAFIX copies and consumes the record before accessing the removable
// card.  A power cycle (or any loss/corruption of retained RAM) therefore fails
// closed.  Keep this ABI byte-identical to MeshCore's OtaSdAuth.h.
#ifndef OTA_SD_AUTH_H_
#define OTA_SD_AUTH_H_

#include <stddef.h>
#include <stdint.h>
#include <string.h>

#define MOTA_SD_AUTH_ADDRESS             0x20006008u
#define MOTA_SD_AUTH_VERSION             2u
#define MOTA_SD_AUTH_LEN                 72u
#define MOTA_SD_AUTH_PURPOSE_APP         1u
#define MOTA_SD_AUTH_PURPOSE_BOOTLOADER  2u
#define MOTA_SD_AUTH_APPROVAL_OFFSET     201u
#define MOTA_SD_AUTH_APPROVAL_LEN        4u
#define MOTA_SD_SECTOR_SIZE              512u

static const uint8_t MOTA_SD_AUTH_MAGIC[8] = {
  'M', 'O', 'T', 'A', 'S', 'D', 'A', '2'
};

typedef struct {
  uint8_t  magic[8];
  uint16_t version;
  uint16_t length;
  uint8_t  purpose;
  uint8_t  format_ver;
  uint16_t reserved;
  uint32_t first_sector;
  uint32_t sector_count;
  uint32_t container_total;
  uint32_t card_sector_count;
  uint8_t  container_sha256[32];
  uint32_t crc32;
  uint32_t crc32_inv;
} mota_sd_auth_t;

typedef char mota_sd_auth_size_must_be_72
  [(sizeof(mota_sd_auth_t) == MOTA_SD_AUTH_LEN) ? 1 : -1];
typedef char mota_sd_auth_must_fit_reserved_retained_ram
  [((MOTA_SD_AUTH_ADDRESS + MOTA_SD_AUTH_LEN) == 0x20006050u) ? 1 : -1];

static inline uint32_t mota_sd_auth_crc32(const uint8_t *data, size_t len) {
  uint32_t crc = UINT32_MAX;
  for (size_t i = 0; i < len; i++) {
    crc ^= data[i];
    for (uint8_t bit = 0; bit < 8u; bit++) {
      crc = (crc >> 1) ^ (0xEDB88320u & (uint32_t)-(int32_t)(crc & 1u));
    }
  }
  return ~crc;
}

static inline void mota_sd_auth_encode(mota_sd_auth_t *out, uint8_t purpose,
                                       uint8_t format_ver, uint32_t first_sector,
                                       uint32_t sector_count, uint32_t container_total,
                                       uint32_t card_sector_count,
                                       const uint8_t container_sha256[32]) {
  memset(out, 0, sizeof(*out));
  memcpy(out->magic, MOTA_SD_AUTH_MAGIC, sizeof(out->magic));
  out->version = MOTA_SD_AUTH_VERSION;
  out->length = MOTA_SD_AUTH_LEN;
  out->purpose = purpose;
  out->format_ver = format_ver;
  out->first_sector = first_sector;
  out->sector_count = sector_count;
  out->container_total = container_total;
  out->card_sector_count = card_sector_count;
  memcpy(out->container_sha256, container_sha256, 32u);
  out->crc32 = mota_sd_auth_crc32((const uint8_t *)out,
                                  offsetof(mota_sd_auth_t, crc32));
  out->crc32_inv = ~out->crc32;
}

static inline int mota_sd_auth_valid(const mota_sd_auth_t *record,
                                     uint8_t expected_purpose,
                                     uint8_t expected_format) {
  if (!record || memcmp(record->magic, MOTA_SD_AUTH_MAGIC,
                        sizeof(record->magic)) != 0 ||
      record->version != MOTA_SD_AUTH_VERSION ||
      record->length != MOTA_SD_AUTH_LEN ||
      record->purpose != expected_purpose ||
      record->format_ver != expected_format || record->reserved != 0u ||
      record->first_sector == 0u || record->sector_count == 0u ||
      record->container_total == 0u || record->card_sector_count == 0u ||
      record->first_sector >= record->card_sector_count ||
      record->sector_count > record->card_sector_count - record->first_sector ||
      record->sector_count != (((record->container_total - 1u) >> 9) + 1u)) {
    return 0;
  }
  const uint32_t crc = mota_sd_auth_crc32((const uint8_t *)record,
                                          offsetof(mota_sd_auth_t, crc32));
  return crc == record->crc32 && record->crc32_inv == ~crc;
}

#endif // OTA_SD_AUTH_H_
