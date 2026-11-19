#ifndef MOTA_ROLLBACK_RECORD_H
#define MOTA_ROLLBACK_RECORD_H

#include <stddef.h>
#include <stdint.h>

// Application and OTAFIX share this flash record. Only the phase word changes
// after publication, by clearing bits so the bootloader need not erase the page.
#define MOTA_ROLLBACK_MAGIC          0x3152424Du
#define MOTA_ROLLBACK_LEGACY_PAGE    0x000D3000u
#define MOTA_ROLLBACK_EXPANDED_PAGE  0x000EC000u
#define MOTA_ROLLBACK_READY          UINT32_MAX
#define MOTA_ROLLBACK_INSTALLING     0xFFFFFFFEu
#define MOTA_ROLLBACK_TRIAL          0xFFFFFFFCu
#define MOTA_ROLLBACK_RUNNING        0xFFFFFFF8u
#define MOTA_ROLLBACK_DONE           0xFFFFFFF0u
#define MOTA_ROLLBACK_FAILED         0xFFFFFFE0u
#define MOTA_ROLLBACK_CAPABILITY     "MOTARBK1"
#define MOTA_ROLLBACK_APP_CAPABILITY "MOTAOKB1"

typedef struct {
  uint32_t magic;
  uint32_t media;        // GPREGRET2 staging selector
  uint32_t offset;       // NOR byte offset, or first contiguous SD sector
  uint32_t previous_size;
  uint32_t next_size;
  uint8_t previous_hash[32];
  uint8_t next_hash[32];
  uint32_t crc32;
  uint32_t phase;
} mota_rollback_record_t;

typedef char mota_rollback_record_size_must_be_92
    [(sizeof(mota_rollback_record_t) == 92u) ? 1 : -1];

static inline uint32_t mota_rollback_crc(const mota_rollback_record_t* record) {
  uint32_t crc = ~0u;
  const uint8_t* data = (const uint8_t*) record;
  for (size_t i = 0; i < offsetof(mota_rollback_record_t, crc32); ++i) {
    crc ^= data[i];
    for (unsigned bit = 0; bit != 8; ++bit) {
      crc = (crc >> 1) ^ ((0u - (crc & 1u)) & 0xEDB88320u);
    }
  }
  return ~crc;
}

static inline int mota_rollback_valid(const mota_rollback_record_t* record,
                                      uint32_t base, uint32_t page) {
  return record->magic == MOTA_ROLLBACK_MAGIC &&
         (page == MOTA_ROLLBACK_LEGACY_PAGE || page == MOTA_ROLLBACK_EXPANDED_PAGE) &&
         base < page && record->previous_size >= 56u && record->next_size >= 56u &&
         record->previous_size <= page - base && record->next_size <= page - base &&
         record->offset != 0u && record->crc32 == mota_rollback_crc(record);
}

static inline int mota_rollback_should_restore(uint32_t phase) {
  return phase == MOTA_ROLLBACK_INSTALLING || phase == MOTA_ROLLBACK_RUNNING;
}

#endif
