// Retained-RAM handoff for an internal-flash prefix plus an SRAM suffix.
//
// The running application publishes this record only after it has verified the
// complete format-v2 application container and written its flash prefix. OTAFIX
// copies and consumes the record before trusting any field or source byte. Keep
// this ABI byte-identical to MeshCore's OtaHybridHandoff.h.
#ifndef OTA_HYBRID_HANDOFF_H_
#define OTA_HYBRID_HANDOFF_H_

#include <stddef.h>
#include <stdint.h>
#include <string.h>
#include "crc32.h"

#define MOTA_HYBRID_HANDOFF_ADDRESS     0x20006008u
#define MOTA_HYBRID_HANDOFF_VERSION     1u
#define MOTA_HYBRID_HANDOFF_LEN         72u
#define MOTA_HYBRID_HANDOFF_PURPOSE_APP 1u
#define MOTA_HYBRID_HANDOFF_FORMAT      2u
#define MOTA_HYBRID_ARENA_START         0x20030000u
#define MOTA_HYBRID_ARENA_SIZE          0x00010000u

static const uint8_t MOTA_HYBRID_HANDOFF_MAGIC[8] = {'M', 'O', 'T', 'A', 'H', 'Y', 'B', '1'};

typedef struct {
  uint8_t  magic[8];
  uint16_t version;
  uint16_t length;
  uint8_t  purpose;
  uint8_t  format_ver;
  uint16_t reserved;
  uint32_t container_total;
  uint32_t flash_start;
  uint32_t flash_len;
  uint32_t ram_len;
  uint8_t  container_sha256[32];
  uint32_t crc32;
  uint32_t crc32_inv;
} mota_hybrid_handoff_t;

typedef char mota_hybrid_handoff_size_must_be_72[(sizeof(mota_hybrid_handoff_t) == MOTA_HYBRID_HANDOFF_LEN) ? 1 : -1];
typedef char mota_hybrid_handoff_must_fit_retained_ram
  [((MOTA_HYBRID_HANDOFF_ADDRESS + MOTA_HYBRID_HANDOFF_LEN) == 0x20006050u) ? 1 : -1];
#define MOTA_HYBRID_HANDOFF_OFFSET_ASSERT(member, expected)                                                        \
  typedef char mota_hybrid_handoff_##member##_offset_mismatch                                                     \
    [(offsetof(mota_hybrid_handoff_t, member) == (expected)) ? 1 : -1]
MOTA_HYBRID_HANDOFF_OFFSET_ASSERT(version, 8u);
MOTA_HYBRID_HANDOFF_OFFSET_ASSERT(length, 10u);
MOTA_HYBRID_HANDOFF_OFFSET_ASSERT(purpose, 12u);
MOTA_HYBRID_HANDOFF_OFFSET_ASSERT(format_ver, 13u);
MOTA_HYBRID_HANDOFF_OFFSET_ASSERT(reserved, 14u);
MOTA_HYBRID_HANDOFF_OFFSET_ASSERT(container_total, 16u);
MOTA_HYBRID_HANDOFF_OFFSET_ASSERT(flash_start, 20u);
MOTA_HYBRID_HANDOFF_OFFSET_ASSERT(flash_len, 24u);
MOTA_HYBRID_HANDOFF_OFFSET_ASSERT(ram_len, 28u);
MOTA_HYBRID_HANDOFF_OFFSET_ASSERT(container_sha256, 32u);
MOTA_HYBRID_HANDOFF_OFFSET_ASSERT(crc32, 64u);
MOTA_HYBRID_HANDOFF_OFFSET_ASSERT(crc32_inv, 68u);
#undef MOTA_HYBRID_HANDOFF_OFFSET_ASSERT

static inline uint32_t mota_hybrid_handoff_crc32(const uint8_t *data, size_t len) {
  return otafix_crc32_update(0, data, len);
}

static inline void mota_hybrid_handoff_encode(mota_hybrid_handoff_t *out, uint32_t container_total,
                                              uint32_t flash_start, uint32_t flash_len, uint32_t ram_len,
                                              const uint8_t container_sha256[32]) {
  memset(out, 0, sizeof(*out));
  memcpy(out->magic, MOTA_HYBRID_HANDOFF_MAGIC, sizeof(out->magic));
  out->version         = MOTA_HYBRID_HANDOFF_VERSION;
  out->length          = MOTA_HYBRID_HANDOFF_LEN;
  out->purpose         = MOTA_HYBRID_HANDOFF_PURPOSE_APP;
  out->format_ver      = MOTA_HYBRID_HANDOFF_FORMAT;
  out->container_total = container_total;
  out->flash_start     = flash_start;
  out->flash_len       = flash_len;
  out->ram_len         = ram_len;
  memcpy(out->container_sha256, container_sha256, 32u);
  out->crc32     = mota_hybrid_handoff_crc32((const uint8_t *)out, offsetof(mota_hybrid_handoff_t, crc32));
  out->crc32_inv = ~out->crc32;
}

// Checks only the authenticated record envelope and source-length arithmetic.
// The bootloader additionally checks reset reason, live APP_BASE, page
// alignment, and the exact recognized flash ceiling before reading a source.
static inline int mota_hybrid_handoff_valid(const mota_hybrid_handoff_t *record) {
  if (!record || memcmp(record->magic, MOTA_HYBRID_HANDOFF_MAGIC, sizeof(record->magic)) != 0 ||
      record->version != MOTA_HYBRID_HANDOFF_VERSION || record->length != MOTA_HYBRID_HANDOFF_LEN ||
      record->purpose != MOTA_HYBRID_HANDOFF_PURPOSE_APP || record->format_ver != MOTA_HYBRID_HANDOFF_FORMAT ||
      record->reserved != 0u || record->flash_len == 0u || record->ram_len == 0u ||
      record->ram_len > MOTA_HYBRID_ARENA_SIZE || record->flash_len > UINT32_MAX - record->ram_len ||
      record->container_total != record->flash_len + record->ram_len) {
    return 0;
  }
  const uint32_t crc = mota_hybrid_handoff_crc32((const uint8_t *)record, offsetof(mota_hybrid_handoff_t, crc32));
  return crc == record->crc32 && record->crc32_inv == ~crc;
}

#endif // OTA_HYBRID_HANDOFF_H_
