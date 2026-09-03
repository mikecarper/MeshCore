#pragma once

#include <stddef.h>
#include <stdint.h>
#include <string.h>

namespace mesh {
namespace storage {

// Contact pages are deliberately smaller than a 4 KiB LittleFS block.  A
// single contact update therefore never rewrites the complete contact list.
static const uint8_t CONTACTS_PER_PAGE = 25;
static const uint8_t CONTACT_PAGE_COUNT = 14;
static const uint16_t CONTACT_RECORD_SIZE = 152;
static const uint16_t CONTACT_PAGE_HEADER_SIZE = 20;
static const uint16_t CONTACT_PAGE_PAYLOAD_SIZE =
    CONTACTS_PER_PAGE * CONTACT_RECORD_SIZE;
static const uint16_t CONTACT_PAGE_FILE_SIZE =
    CONTACT_PAGE_HEADER_SIZE + CONTACT_PAGE_PAYLOAD_SIZE;
static const uint16_t CONTACT_SLOT_NONE = 0xFFFF;

static const uint8_t CONTACT_PAGE_VERSION = 1;
static const uint8_t CONTACT_PAGE_MAGIC[4] = {'M', 'C', 'P', '4'};

inline uint32_t contactPageValidSlotMask() {
  return (1UL << CONTACTS_PER_PAGE) - 1UL;
}

struct ContactPageHeader {
  uint8_t page_index;
  uint32_t occupied;
  uint32_t generation;
  uint32_t payload_crc;
};

enum class ContactStoreSource : uint8_t {
  EMPTY,
  LEGACY,
  PAGED,
};

enum class ContactPathState : uint8_t {
  ABSENT,
  PRESENT,
  IO_ERROR,
};

// Adafruit_LittleFS::exists() collapses every lfs_stat() error into false.
// Keep the raw result distinguishable so a transient media error can never be
// treated as proof that an authoritative stored source or journal is absent.
inline ContactPathState classifyContactPathStat(int result,
                                                int no_entry_result) {
  if (result == 0) return ContactPathState::PRESENT;
  if (result == no_entry_result) return ContactPathState::ABSENT;
  return ContactPathState::IO_ERROR;
}

// A retained legacy file means migration is still in progress. Its complete
// record count is the authoritative prefix boundary; page records at or above
// that boundary have already committed.
inline ContactStoreSource chooseContactStoreSource(bool legacy_exists,
                                                   bool page_exists) {
  if (legacy_exists) return ContactStoreSource::LEGACY;
  if (page_exists) return ContactStoreSource::PAGED;
  return ContactStoreSource::EMPTY;
}

// When a legacy file returns after a downgrade, old page files may describe a
// different contact list. Only a marker created after stale pages were removed
// makes those pages valid participants in an in-progress migration.
inline bool trustMigratedContactPages(bool legacy_exists,
                                      bool migration_marker_exists) {
  return !legacy_exists || migration_marker_exists;
}

inline uint16_t legacyContactCountForSize(size_t file_size) {
  const size_t count = file_size / CONTACT_RECORD_SIZE;
  const size_t capacity = CONTACT_PAGE_COUNT * CONTACTS_PER_PAGE;
  return (uint16_t)(count < capacity ? count : capacity);
}

inline bool isValidLegacyContactFileSize(size_t file_size) {
  const size_t capacity = CONTACT_PAGE_COUNT * CONTACTS_PER_PAGE;
  return file_size % CONTACT_RECORD_SIZE == 0
      && file_size <= capacity * CONTACT_RECORD_SIZE;
}

inline uint8_t legacyMigrationPage(uint16_t legacy_contact_count) {
  return legacy_contact_count == 0 ? CONTACT_PAGE_COUNT
      : (uint8_t)((legacy_contact_count - 1) / CONTACTS_PER_PAGE);
}

inline uint16_t legacyCountAfterMigratingPage(uint8_t page) {
  return page < CONTACT_PAGE_COUNT
      ? (uint16_t)page * CONTACTS_PER_PAGE : 0;
}

// During tail-first migration, the still-present prefix in /contacts3 wins.
// Page slots at or beyond its complete-record count are already committed and
// can be loaded. This also makes a reset between page commit and truncation
// harmless: the duplicate page records remain hidden until truncation commits.
inline bool loadSlotFromMigratedPage(uint16_t slot,
                                     uint16_t legacy_contact_count) {
  return slot >= legacy_contact_count;
}

inline uint16_t readLE16(const uint8_t* src) {
  return (uint16_t)src[0] | ((uint16_t)src[1] << 8);
}

inline uint32_t readLE32(const uint8_t* src) {
  return (uint32_t)src[0] | ((uint32_t)src[1] << 8)
      | ((uint32_t)src[2] << 16) | ((uint32_t)src[3] << 24);
}

inline void writeLE16(uint8_t* dest, uint16_t value) {
  dest[0] = (uint8_t)value;
  dest[1] = (uint8_t)(value >> 8);
}

inline void writeLE32(uint8_t* dest, uint32_t value) {
  dest[0] = (uint8_t)value;
  dest[1] = (uint8_t)(value >> 8);
  dest[2] = (uint8_t)(value >> 16);
  dest[3] = (uint8_t)(value >> 24);
}

// Same CRC convention as AtomicFileWriter: reflected CRC-32, initial value
// 0xFFFFFFFF, with no final xor.  Keeping this incremental makes it usable on
// small targets without buffering an entire file.
inline uint32_t updateCRC32(uint32_t crc, const uint8_t* data, size_t len) {
  while (len-- > 0) {
    crc ^= *data++;
    for (uint8_t bit = 0; bit < 8; bit++) {
      crc = (crc >> 1) ^ ((crc & 1) ? 0xEDB88320UL : 0);
    }
  }
  return crc;
}

inline bool contactRecordHasData(
    const uint8_t record[CONTACT_RECORD_SIZE]) {
  uint8_t combined = 0;
  for (uint16_t i = 0; i < CONTACT_RECORD_SIZE; i++) combined |= record[i];
  return combined != 0;
}

inline void encodeContactPageHeader(uint8_t dest[CONTACT_PAGE_HEADER_SIZE],
                                    const ContactPageHeader& header) {
  memcpy(dest, CONTACT_PAGE_MAGIC, sizeof(CONTACT_PAGE_MAGIC));
  dest[4] = CONTACT_PAGE_VERSION;
  dest[5] = header.page_index;
  writeLE16(&dest[6], CONTACT_RECORD_SIZE);
  writeLE32(&dest[8], header.occupied);
  writeLE32(&dest[12], header.generation);
  writeLE32(&dest[16], header.payload_crc);
}

inline bool decodeContactPageHeader(const uint8_t src[CONTACT_PAGE_HEADER_SIZE],
                                    uint8_t expected_page,
                                    ContactPageHeader& header) {
  if (memcmp(src, CONTACT_PAGE_MAGIC, sizeof(CONTACT_PAGE_MAGIC)) != 0
      || src[4] != CONTACT_PAGE_VERSION || src[5] != expected_page
      || readLE16(&src[6]) != CONTACT_RECORD_SIZE) {
    return false;
  }

  header.page_index = src[5];
  header.occupied = readLE32(&src[8]);
  header.generation = readLE32(&src[12]);
  header.payload_crc = readLE32(&src[16]);
  // Occupancy is repairable from the CRC-protected record payload. Keep the
  // header parseable even when an unused high bit was upset so load can retain
  // the page's contacts and rewrite the corrected mask.
  return true;
}

class DirtyPageSet {
  uint32_t _bits;

public:
  DirtyPageSet() : _bits(0) {}

  void clearAll() { _bits = 0; }
  bool empty() const { return _bits == 0; }
  uint32_t bits() const { return _bits; }

  bool mark(uint8_t page) {
    if (page >= CONTACT_PAGE_COUNT) return false;
    _bits |= (1UL << page);
    return true;
  }

  int first() const {
    for (uint8_t page = 0; page < CONTACT_PAGE_COUNT; page++) {
      if ((_bits & (1UL << page)) != 0) return page;
    }
    return -1;
  }

  void clear(uint8_t page) {
    if (page < CONTACT_PAGE_COUNT) _bits &= ~(1UL << page);
  }
};

class ContactSlotMap {
  uint32_t _used[CONTACT_PAGE_COUNT];

public:
  ContactSlotMap() { clear(); }

  void clear() { memset(_used, 0, sizeof(_used)); }

  uint32_t pageMask(uint8_t page) const {
    return page < CONTACT_PAGE_COUNT ? _used[page] : 0;
  }

  bool isUsed(uint16_t slot) const {
    if (slot >= CONTACT_PAGE_COUNT * CONTACTS_PER_PAGE) return false;
    return (_used[slot / CONTACTS_PER_PAGE]
            & (1UL << (slot % CONTACTS_PER_PAGE))) != 0;
  }

  bool reserve(uint16_t slot) {
    if (slot >= CONTACT_PAGE_COUNT * CONTACTS_PER_PAGE) return false;
    const uint8_t page = slot / CONTACTS_PER_PAGE;
    const uint32_t bit = 1UL << (slot % CONTACTS_PER_PAGE);
    if ((_used[page] & bit) != 0) return false;
    _used[page] |= bit;
    return true;
  }

  uint16_t allocate() {
    const uint32_t valid_slots = (1UL << CONTACTS_PER_PAGE) - 1UL;
    for (uint8_t page = 0; page < CONTACT_PAGE_COUNT; page++) {
      if ((_used[page] & valid_slots) == valid_slots) continue;
      for (uint8_t index = 0; index < CONTACTS_PER_PAGE; index++) {
        const uint32_t bit = 1UL << index;
        if ((_used[page] & bit) == 0) {
          _used[page] |= bit;
          return (uint16_t)page * CONTACTS_PER_PAGE + index;
        }
      }
    }
    return CONTACT_SLOT_NONE;
  }

  bool release(uint16_t slot) {
    if (!isUsed(slot)) return false;
    _used[slot / CONTACTS_PER_PAGE] &= ~(1UL << (slot % CONTACTS_PER_PAGE));
    return true;
  }
};

} // namespace storage
} // namespace mesh
