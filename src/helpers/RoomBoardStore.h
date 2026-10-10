#pragma once

#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include "FilePresence.h"

namespace mesh {

static constexpr uint8_t ROOM_BOARD_MAX_ARTICLES = 8;
static constexpr size_t ROOM_BOARD_TITLE_CAPACITY = 64;
static constexpr size_t ROOM_BOARD_MAX_BODY_LENGTH = 2048;
static constexpr size_t ROOM_BOARD_READ_MAX_CHUNK = 128;
static const char ROOM_BOARD_PRIMARY_PATH[] = "/room_board";
static const char ROOM_BOARD_TEMP_PATH[] = "/room_board.tmp";
static const char ROOM_BOARD_BACKUP_PATH[] = "/room_board.bak";

struct RoomBoardArticle {
  uint8_t id = 0;                    // Stable slots, 1 through 8.
  uint8_t bank = 0;                  // Internal immutable-body file selection.
  uint16_t body_length = 0;
  uint32_t version = 0;
  char title[ROOM_BOARD_TITLE_CAPACITY] = {};
};

struct RoomBoardIndex {
  uint32_t revision = 0;
  uint8_t count = 0;
  RoomBoardArticle articles[ROOM_BOARD_MAX_ARTICLES]; // Sorted by article ID.
};
static_assert(sizeof(RoomBoardIndex) <= 640, "Board metadata must remain bounded");

enum class RoomBoardResult : uint8_t {
  Success, Invalid, Unavailable, WriteFailure, NotFound, StaleVersion
};

namespace room_board_detail {
static constexpr size_t HEADER_SIZE = 16;
static constexpr size_t ENTRY_SIZE = 72;
static constexpr size_t STREAM_CHUNK = 64;
static const uint8_t INDEX_MAGIC[4] = {'R', 'B', 'I', 1};
static const uint8_t ARTICLE_MAGIC[4] = {'R', 'B', 'A', 1};

inline uint32_t crc(uint32_t value, const uint8_t* data, size_t length) {
  while (length-- != 0) {
    value ^= *data++;
    for (uint8_t bit = 0; bit < 8; ++bit) {
      value = (value >> 1) ^ ((value & 1) ? UINT32_C(0xedb88320) : 0);
    }
  }
  return value;
}
inline uint32_t get32(const uint8_t* bytes) {
  return uint32_t(bytes[0]) | (uint32_t(bytes[1]) << 8)
      | (uint32_t(bytes[2]) << 16) | (uint32_t(bytes[3]) << 24);
}
inline void put32(uint8_t* bytes, uint32_t value) {
  for (uint8_t i = 0; i < 4; ++i) bytes[i] = uint8_t(value >> (8 * i));
}
inline uint16_t get16(const uint8_t* bytes) {
  return uint16_t(bytes[0]) | (uint16_t(bytes[1]) << 8);
}
inline void put16(uint8_t* bytes, uint16_t value) {
  bytes[0] = uint8_t(value); bytes[1] = uint8_t(value >> 8);
}

// Titles are complete UTF-8 strings; reject overlong encodings, surrogate
// code points, embedded controls, and values beyond Unicode's maximum.
inline bool validTitle(const char* title) {
  if (!title) return false;
  size_t length = 0;
  while (length < ROOM_BOARD_TITLE_CAPACITY && title[length] != 0) ++length;
  if (length == 0 || length == ROOM_BOARD_TITLE_CAPACITY) return false;
  for (size_t i = 0; i < length;) {
    const uint8_t first = uint8_t(title[i++]);
    if (first < 0x80) {
      if (first < 0x20 || first == 0x7f) return false;
      continue;
    }
    uint8_t count;
    uint32_t point, minimum;
    if (first >= 0xc2 && first <= 0xdf) { count = 1; point = first & 0x1f; minimum = 0x80; }
    else if (first >= 0xe0 && first <= 0xef) { count = 2; point = first & 0x0f; minimum = 0x800; }
    else if (first >= 0xf0 && first <= 0xf4) { count = 3; point = first & 0x07; minimum = 0x10000; }
    else return false;
    if (i + count > length) return false;
    while (count-- != 0) {
      const uint8_t next = uint8_t(title[i++]);
      if ((next & 0xc0) != 0x80) return false;
      point = (point << 6) | (next & 0x3f);
    }
    if (point < minimum || point > 0x10ffff || (point >= 0xd800 && point <= 0xdfff)) return false;
  }
  return true;
}

inline void articlePath(const RoomBoardArticle& entry, char (&path)[24]) {
  snprintf(path, sizeof(path), "/room_board_%u.%c", unsigned(entry.id), entry.bank ? 'b' : 'a');
}
inline void encodeEntry(const RoomBoardArticle& entry, uint8_t (&bytes)[ENTRY_SIZE]) {
  memset(bytes, 0, sizeof(bytes));
  bytes[0] = entry.id; bytes[1] = entry.bank;
  put16(bytes + 2, entry.body_length); put32(bytes + 4, entry.version);
  memcpy(bytes + 8, entry.title, sizeof(entry.title));
}
inline void articleHeader(const RoomBoardArticle& entry, uint8_t (&bytes)[HEADER_SIZE]) {
  memset(bytes, 0, sizeof(bytes));
  memcpy(bytes, ARTICLE_MAGIC, sizeof(ARTICLE_MAGIC));
  bytes[4] = entry.id; bytes[5] = entry.bank;
  put16(bytes + 6, entry.body_length); put32(bytes + 8, entry.version);
}

template <typename Filesystem>
auto openRead(Filesystem* fs, const char* path)
#if defined(RP2040_PLATFORM)
    -> decltype(fs->open(path, static_cast<const char*>(nullptr))) { return fs->open(path, "r"); }
#else
    -> decltype(fs->open(path)) { return fs->open(path); }
#endif

template <typename Filesystem>
auto openWrite(Filesystem* fs, const char* path)
#if defined(NRF52_PLATFORM) || defined(STM32_PLATFORM)
    -> decltype(fs->open(path, FILE_O_WRITE)) { return fs->open(path, FILE_O_WRITE); }
#elif defined(ESP32_PLATFORM)
    -> decltype(fs->open(path, static_cast<const char*>(nullptr), true)) { return fs->open(path, "w", true); }
#else
    -> decltype(fs->open(path, static_cast<const char*>(nullptr))) { return fs->open(path, "w"); }
#endif

template <typename Filesystem>
bool removeArtifact(Filesystem* fs, const char* path) {
  bool present;
  if (!filePresence(fs, path, present)) return false;
  if (!present) return true;
  fs->remove(path);
  return filePresence(fs, path, present) && !present;
}

// Validate the whole selected body before any caller may send the copied
// slice. The only internal body buffer is 64 bytes, including readback.
template <typename Filesystem>
bool readBody(Filesystem* fs, const RoomBoardArticle& entry,
              uint8_t* output = nullptr, size_t offset = 0, size_t capacity = 0,
              const uint32_t* expected_checksum = nullptr) {
  char path[24]; articlePath(entry, path);
  auto file = openRead(fs, path);
  if (!file) return false;
  uint8_t header[HEADER_SIZE], expected[HEADER_SIZE];
  articleHeader(entry, expected);
  bool valid = !file.isDirectory()
      && file.size() == HEADER_SIZE + entry.body_length + 4
      && static_cast<size_t>(file.read(header, sizeof(header))) == sizeof(header)
      && memcmp(header, expected, sizeof(header)) == 0;
  uint32_t checksum = valid ? crc(UINT32_C(0xffffffff), header, sizeof(header)) : 0;
  uint8_t chunk[STREAM_CHUNK];
  for (size_t position = 0; valid && position < entry.body_length;) {
    const size_t remaining = entry.body_length - position;
    const size_t length = remaining < sizeof(chunk) ? remaining : sizeof(chunk);
    valid = static_cast<size_t>(file.read(chunk, length)) == length;
    if (!valid) break;
    checksum = crc(checksum, chunk, length);
    if (output && position + length > offset && position < offset + capacity) {
      const size_t start = position < offset ? offset - position : 0;
      size_t end = length;
      if (position + end > offset + capacity) end = offset + capacity - position;
      memcpy(output + position + start - offset, chunk + start, end - start);
    }
    position += length;
  }
  uint8_t trailer[4];
  if (valid) {
    checksum ^= UINT32_C(0xffffffff);
    valid = static_cast<size_t>(file.read(trailer, sizeof(trailer))) == sizeof(trailer)
        && get32(trailer) == checksum
        && (!expected_checksum || *expected_checksum == checksum);
  }
  file.close();
  if (!valid && output) memset(output, 0, capacity);
  return valid;
}

inline bool sameIndex(const RoomBoardIndex& first, const RoomBoardIndex& second) {
  if (first.revision != second.revision || first.count != second.count) return false;
  for (uint8_t i = 0; i < first.count; ++i) {
    uint8_t a[ENTRY_SIZE], b[ENTRY_SIZE];
    encodeEntry(first.articles[i], a); encodeEntry(second.articles[i], b);
    if (memcmp(a, b, sizeof(a)) != 0) return false;
  }
  return true;
}

template <typename Filesystem>
bool readIndex(Filesystem* fs, const char* path, RoomBoardIndex& staged,
               const RoomBoardIndex* expected = nullptr) {
  auto file = openRead(fs, path);
  if (!file) return false;
  uint8_t header[HEADER_SIZE];
  bool valid = !file.isDirectory()
      && static_cast<size_t>(file.read(header, sizeof(header))) == sizeof(header);
  if (valid) {
    valid = memcmp(header, INDEX_MAGIC, sizeof(INDEX_MAGIC)) == 0
        && get32(header + 4) != 0 && header[8] <= ROOM_BOARD_MAX_ARTICLES
        && file.size() == HEADER_SIZE + header[8] * ENTRY_SIZE + 4;
    for (size_t i = 9; i < sizeof(header); ++i) valid = valid && header[i] == 0;
  }
  uint32_t checksum = valid ? crc(UINT32_C(0xffffffff), header, sizeof(header)) : 0;
  staged = RoomBoardIndex{};
  if (valid) { staged.revision = get32(header + 4); staged.count = header[8]; }
  for (uint8_t i = 0; valid && i < staged.count; ++i) {
    uint8_t bytes[ENTRY_SIZE];
    valid = static_cast<size_t>(file.read(bytes, sizeof(bytes))) == sizeof(bytes);
    if (!valid) break;
    checksum = crc(checksum, bytes, sizeof(bytes));
    RoomBoardArticle& entry = staged.articles[i];
    entry.id = bytes[0]; entry.bank = bytes[1]; entry.body_length = get16(bytes + 2);
    entry.version = get32(bytes + 4); memcpy(entry.title, bytes + 8, sizeof(entry.title));
    valid = entry.id >= 1 && entry.id <= ROOM_BOARD_MAX_ARTICLES && entry.bank <= 1
        && entry.body_length <= ROOM_BOARD_MAX_BODY_LENGTH && entry.version != 0
        && entry.version <= staged.revision && validTitle(entry.title)
        && (i == 0 || staged.articles[i - 1].id < entry.id);
    bool terminated = false;
    for (size_t j = 0; j < sizeof(entry.title); ++j) {
      if (terminated && entry.title[j] != 0) valid = false;
      if (entry.title[j] == 0) terminated = true;
    }
  }
  uint8_t trailer[4];
  if (valid) valid = static_cast<size_t>(file.read(trailer, sizeof(trailer))) == sizeof(trailer)
      && get32(trailer) == (checksum ^ UINT32_C(0xffffffff));
  file.close();
  if (valid && expected) valid = sameIndex(staged, *expected);
  for (uint8_t i = 0; valid && i < staged.count; ++i) valid = readBody(fs, staged.articles[i]);
  return valid;
}

// A present primary, including corrupt/future/unreadable metadata or bodies,
// is authoritative. Never replace it with an older backup or delete evidence.
// A temp index is uncommitted; only a verified backup may restore absence.
template <typename Filesystem>
bool recover(Filesystem* fs, RoomBoardIndex& staged, bool require_cleanup) {
  bool present;
  if (!fs || !filePresence(fs, ROOM_BOARD_PRIMARY_PATH, present)) return false;
  if (present) {
    if (!readIndex(fs, ROOM_BOARD_PRIMARY_PATH, staged)) return false;
    const bool temp_removed = removeArtifact(fs, ROOM_BOARD_TEMP_PATH);
    const bool backup_removed = removeArtifact(fs, ROOM_BOARD_BACKUP_PATH);
    return !require_cleanup || (temp_removed && backup_removed);
  }
  if (!filePresence(fs, ROOM_BOARD_BACKUP_PATH, present)) return false;
  if (present) {
    if (!readIndex(fs, ROOM_BOARD_BACKUP_PATH, staged)
        || !fs->rename(ROOM_BOARD_BACKUP_PATH, ROOM_BOARD_PRIMARY_PATH)) return false;
  } else staged = RoomBoardIndex{};
  const bool temp_removed = removeArtifact(fs, ROOM_BOARD_TEMP_PATH);
  return !require_cleanup || temp_removed;
}

template <typename Filesystem>
RoomBoardResult commitIndex(Filesystem* fs, const RoomBoardIndex& next) {
  uint8_t header[HEADER_SIZE] = {};
  memcpy(header, INDEX_MAGIC, sizeof(INDEX_MAGIC));
  put32(header + 4, next.revision); header[8] = next.count;
  auto file = openWrite(fs, ROOM_BOARD_TEMP_PATH);
  if (!file) return RoomBoardResult::WriteFailure;
  bool written = !file.isDirectory() && file.write(header, sizeof(header)) == sizeof(header);
  uint32_t checksum = crc(UINT32_C(0xffffffff), header, sizeof(header));
  for (uint8_t i = 0; written && i < next.count; ++i) {
    uint8_t bytes[ENTRY_SIZE]; encodeEntry(next.articles[i], bytes);
    written = file.write(bytes, sizeof(bytes)) == sizeof(bytes);
    checksum = crc(checksum, bytes, sizeof(bytes));
  }
  uint8_t trailer[4]; put32(trailer, checksum ^ UINT32_C(0xffffffff));
  written = written && file.write(trailer, sizeof(trailer)) == sizeof(trailer);
  file.flush(); file.close();
  RoomBoardIndex checked;
  if (!written || !readIndex(fs, ROOM_BOARD_TEMP_PATH, checked, &next)) {
    removeArtifact(fs, ROOM_BOARD_TEMP_PATH); return RoomBoardResult::WriteFailure;
  }
  bool had_primary;
  if (!filePresence(fs, ROOM_BOARD_PRIMARY_PATH, had_primary)
      || (had_primary && !fs->rename(ROOM_BOARD_PRIMARY_PATH, ROOM_BOARD_BACKUP_PATH))) {
    removeArtifact(fs, ROOM_BOARD_TEMP_PATH); return RoomBoardResult::WriteFailure;
  }
  if (!fs->rename(ROOM_BOARD_TEMP_PATH, ROOM_BOARD_PRIMARY_PATH)) {
    if (had_primary) fs->rename(ROOM_BOARD_BACKUP_PATH, ROOM_BOARD_PRIMARY_PATH);
    removeArtifact(fs, ROOM_BOARD_TEMP_PATH); return RoomBoardResult::WriteFailure;
  }
  // The rename is the commit point. Failed housekeeping cannot undo success.
  removeArtifact(fs, ROOM_BOARD_BACKUP_PATH);
  return RoomBoardResult::Success;
}
} // namespace room_board_detail

template <typename Filesystem>
bool loadRoomBoard(Filesystem* fs, RoomBoardIndex& index) {
  RoomBoardIndex staged;
  index = RoomBoardIndex{};
  if (!room_board_detail::recover(fs, staged, false)) return false;
  index = staged; return true;
}

// Main-loop single writer. The reader is called as reader(offset, buffer,
// requested_length), returning exactly requested_length, in <=64-byte pieces.
// expected_version == 0 permits an administrative create/overwrite; nonzero
// versions perform compare-and-swap and reject an article changed since load.
template <typename Filesystem, typename Reader>
RoomBoardResult saveRoomBoardArticle(Filesystem* fs, uint8_t id, const char* title,
                                     size_t body_length, Reader reader,
                                     uint32_t expected_version = 0) {
  using namespace room_board_detail;
  if (id == 0 || id > ROOM_BOARD_MAX_ARTICLES || !validTitle(title)
      || body_length > ROOM_BOARD_MAX_BODY_LENGTH) return RoomBoardResult::Invalid;
  RoomBoardIndex next;
  if (!recover(fs, next, true)) return RoomBoardResult::Unavailable;
  uint8_t position = 0;
  while (position < next.count && next.articles[position].id < id) ++position;
  const bool existing = position < next.count && next.articles[position].id == id;
  if (expected_version && (!existing || next.articles[position].version != expected_version)) {
    return RoomBoardResult::StaleVersion;
  }
  if (next.revision == UINT32_MAX) return RoomBoardResult::Unavailable;
  RoomBoardArticle entry;
  entry.id = id; entry.bank = existing ? 1 - next.articles[position].bank : 0;
  entry.version = ++next.revision; entry.body_length = uint16_t(body_length);
  memcpy(entry.title, title, strlen(title));
  char path[24]; articlePath(entry, path);
  if (!removeArtifact(fs, path)) return RoomBoardResult::WriteFailure;
  auto file = openWrite(fs, path);
  if (!file) return RoomBoardResult::WriteFailure;
  uint8_t header[HEADER_SIZE]; articleHeader(entry, header);
  bool written = !file.isDirectory() && file.write(header, sizeof(header)) == sizeof(header);
  uint32_t checksum = crc(UINT32_C(0xffffffff), header, sizeof(header));
  uint8_t chunk[STREAM_CHUNK];
  for (size_t offset = 0; written && offset < body_length;) {
    const size_t remaining = body_length - offset;
    const size_t length = remaining < sizeof(chunk) ? remaining : sizeof(chunk);
    written = reader(offset, chunk, length) == length
        && file.write(chunk, length) == length;
    if (written) checksum = crc(checksum, chunk, length);
    offset += length;
  }
  checksum ^= UINT32_C(0xffffffff);
  uint8_t trailer[4]; put32(trailer, checksum);
  written = written && file.write(trailer, sizeof(trailer)) == sizeof(trailer);
  file.flush(); file.close();
  if (!written || !readBody(fs, entry, nullptr, 0, 0, &checksum)) {
    removeArtifact(fs, path); return RoomBoardResult::WriteFailure;
  }
  if (!existing) {
    for (uint8_t i = next.count; i > position; --i) next.articles[i] = next.articles[i - 1];
    ++next.count;
  }
  next.articles[position] = entry;
  return commitIndex(fs, next);
}

template <typename Filesystem>
RoomBoardResult deleteRoomBoardArticle(Filesystem* fs, uint8_t id,
                                       uint32_t expected_version = 0) {
  using namespace room_board_detail;
  if (id == 0 || id > ROOM_BOARD_MAX_ARTICLES) return RoomBoardResult::Invalid;
  RoomBoardIndex next;
  if (!recover(fs, next, true)) return RoomBoardResult::Unavailable;
  uint8_t position = 0;
  while (position < next.count && next.articles[position].id != id) ++position;
  if (position == next.count) return RoomBoardResult::NotFound;
  if (expected_version && next.articles[position].version != expected_version) return RoomBoardResult::StaleVersion;
  if (next.revision == UINT32_MAX) return RoomBoardResult::Unavailable;
  ++next.revision;
  for (uint8_t i = position; i + 1 < next.count; ++i) next.articles[i] = next.articles[i + 1];
  next.articles[--next.count] = RoomBoardArticle{};
  // Bodies are bounded to two banks per ID. Retaining unreferenced banks
  // avoids deleting a body still needed by recovery after a power cut.
  return commitIndex(fs, next);
}

// Every page must identify the exact published article version. The caller
// supplies a <=128-byte capacity; offset==body_length returns successful EOF.
template <typename Filesystem>
RoomBoardResult readRoomBoardArticle(Filesystem* fs, uint8_t id,
                                     uint32_t expected_version, size_t offset,
                                     uint8_t* output, size_t capacity, size_t& bytes_read) {
  using namespace room_board_detail;
  bytes_read = 0;
  if (id == 0 || id > ROOM_BOARD_MAX_ARTICLES || expected_version == 0
      || !output || capacity == 0 || capacity > ROOM_BOARD_READ_MAX_CHUNK) return RoomBoardResult::Invalid;
  RoomBoardIndex index;
  if (!recover(fs, index, false)) return RoomBoardResult::Unavailable;
  uint8_t position = 0;
  while (position < index.count && index.articles[position].id != id) ++position;
  if (position == index.count) return RoomBoardResult::NotFound;
  const RoomBoardArticle& entry = index.articles[position];
  if (entry.version != expected_version) return RoomBoardResult::StaleVersion;
  if (offset > entry.body_length) return RoomBoardResult::Invalid;
  if (!readBody(fs, entry, output, offset, capacity)) return RoomBoardResult::Unavailable;
  const size_t remaining = entry.body_length - offset;
  bytes_read = remaining < capacity ? remaining : capacity;
  return RoomBoardResult::Success;
}

} // namespace mesh
