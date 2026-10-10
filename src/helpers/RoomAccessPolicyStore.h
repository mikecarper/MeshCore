#pragma once

#include <stddef.h>
#include <stdint.h>
#include <string.h>
#include "FilePresence.h"

namespace mesh {

static constexpr size_t ROOM_ACCESS_KEY_SIZE = 32;
static constexpr size_t ROOM_ACCESS_MAX_BANS = 32;
static const char ROOM_ACCESS_PRIMARY_PATH[] = "/room_access";
static const char ROOM_ACCESS_TEMP_PATH[] = "/room_access.tmp";
static const char ROOM_ACCESS_BACKUP_PATH[] = "/room_access.bak";

struct RoomAccessSettings {
  uint16_t posts_per_minute = 0;
  uint16_t polls_per_minute = 0;
  uint8_t ban_count = 0;
  uint8_t bans[ROOM_ACCESS_MAX_BANS][ROOM_ACCESS_KEY_SIZE] = {};
};
static_assert(sizeof(RoomAccessSettings) <= 1032,
              "Room access settings must remain bounded");

namespace room_access_detail {
static const uint8_t MAGIC[4] = {'R', 'A', 'P', 1};
static constexpr size_t HEADER_SIZE = 12;
static constexpr size_t TRAILER_SIZE = 4;

inline uint32_t crc(uint32_t value, const uint8_t* data, size_t length) {
  while (length-- != 0) {
    value ^= *data++;
    for (uint8_t bit = 0; bit < 8; ++bit) {
      value = (value >> 1) ^ ((value & 1) ? UINT32_C(0xedb88320) : 0);
    }
  }
  return value;
}

inline bool nonzeroKey(const uint8_t* key) {
  if (!key) return false;
  uint8_t any = 0;
  for (size_t i = 0; i < ROOM_ACCESS_KEY_SIZE; ++i) any |= key[i];
  return any != 0;
}

inline bool validSettings(const RoomAccessSettings& settings) {
  if (settings.ban_count > ROOM_ACCESS_MAX_BANS) return false;
  for (size_t i = 0; i < settings.ban_count; ++i) {
    if (!nonzeroKey(settings.bans[i])) return false;
    for (size_t prior = 0; prior < i; ++prior) {
      if (memcmp(settings.bans[prior], settings.bans[i], ROOM_ACCESS_KEY_SIZE) == 0) return false;
    }
  }
  return true;
}

template <typename Filesystem>
bool removeArtifact(Filesystem* fs, const char* path) {
  bool present;
  if (!filePresence(fs, path, present)) return false;
  if (!present) return true;
  fs->remove(path);
  return filePresence(fs, path, present) && !present;
}

// Decode into a staging object. Callers publish it only after full validation;
// a failed read can never replace the active policy with a partial ban list.
template <typename Filesystem>
bool readState(Filesystem* fs, const char* path, RoomAccessSettings& staged,
               const RoomAccessSettings* expected = nullptr) {
#if defined(RP2040_PLATFORM)
  auto file = fs->open(path, "r");
#else
  auto file = fs->open(path);
#endif
  if (!file) return false;
  uint8_t header[HEADER_SIZE];
  bool valid = !file.isDirectory()
      && static_cast<size_t>(file.read(header, sizeof(header))) == sizeof(header);
  if (valid) {
    const size_t count = header[8];
    valid = memcmp(header, MAGIC, sizeof(MAGIC)) == 0
        && header[9] == 0 && header[10] == 0 && header[11] == 0
        && count <= ROOM_ACCESS_MAX_BANS
        && file.size() == HEADER_SIZE + count * ROOM_ACCESS_KEY_SIZE + TRAILER_SIZE;
    if (valid) {
      staged.posts_per_minute = uint16_t(header[4]) | (uint16_t(header[5]) << 8);
      staged.polls_per_minute = uint16_t(header[6]) | (uint16_t(header[7]) << 8);
      staged.ban_count = static_cast<uint8_t>(count);
      memset(staged.bans, 0, sizeof(staged.bans));
    }
  }
  uint32_t checksum = valid ? crc(UINT32_C(0xffffffff), header, sizeof(header)) : 0;
  for (size_t i = 0; valid && i < staged.ban_count; ++i) {
    uint8_t* key = staged.bans[i];
    valid = static_cast<size_t>(file.read(key, ROOM_ACCESS_KEY_SIZE)) == ROOM_ACCESS_KEY_SIZE
        && nonzeroKey(key);
    if (valid) {
      checksum = crc(checksum, key, ROOM_ACCESS_KEY_SIZE);
      for (size_t prior = 0; prior < i; ++prior) {
        if (memcmp(staged.bans[prior], key, ROOM_ACCESS_KEY_SIZE) == 0) valid = false;
      }
    }
  }
  uint8_t trailer[TRAILER_SIZE] = {};
  if (valid) {
    valid = static_cast<size_t>(file.read(trailer, sizeof(trailer))) == sizeof(trailer);
    const uint32_t stored = uint32_t(trailer[0]) | (uint32_t(trailer[1]) << 8)
        | (uint32_t(trailer[2]) << 16) | (uint32_t(trailer[3]) << 24);
    valid = valid && (checksum ^ UINT32_C(0xffffffff)) == stored;
  }
  file.close();
  if (valid && expected) {
    valid = staged.posts_per_minute == expected->posts_per_minute
        && staged.polls_per_minute == expected->polls_per_minute
        && staged.ban_count == expected->ban_count
        && memcmp(staged.bans, expected->bans,
                  staged.ban_count * ROOM_ACCESS_KEY_SIZE) == 0;
  }
  return valid;
}

// A present primary is authoritative even when corrupt/newer/unreadable.
// Restore only a verified backup when the primary is genuinely absent. A temp
// file is uncommitted and can never be promoted after reboot.
template <typename Filesystem>
bool recover(Filesystem* fs, RoomAccessSettings& staged, bool require_cleanup) {
  bool present;
  if (!fs || !filePresence(fs, ROOM_ACCESS_PRIMARY_PATH, present)) return false;
  if (present) {
    if (!readState(fs, ROOM_ACCESS_PRIMARY_PATH, staged)) return false;
    const bool removed_temp = removeArtifact(fs, ROOM_ACCESS_TEMP_PATH);
    const bool removed_backup = removeArtifact(fs, ROOM_ACCESS_BACKUP_PATH);
    return !require_cleanup || (removed_temp && removed_backup);
  }
  if (!filePresence(fs, ROOM_ACCESS_BACKUP_PATH, present)) return false;
  if (present) {
    if (!readState(fs, ROOM_ACCESS_BACKUP_PATH, staged)
        || !fs->rename(ROOM_ACCESS_BACKUP_PATH, ROOM_ACCESS_PRIMARY_PATH)) return false;
  } else {
    staged = RoomAccessSettings{};
  }
  const bool removed_temp = removeArtifact(fs, ROOM_ACCESS_TEMP_PATH);
  return !require_cleanup || removed_temp;
}

enum class SaveResult : uint8_t { Saved, Unavailable, WriteFailure };

template <typename Filesystem>
SaveResult save(Filesystem* fs, const RoomAccessSettings& next) {
  if (!validSettings(next)) return SaveResult::WriteFailure;
  RoomAccessSettings checked;
  if (!recover(fs, checked, true)) return SaveResult::Unavailable;
  uint8_t header[HEADER_SIZE] = {};
  memcpy(header, MAGIC, sizeof(MAGIC));
  header[4] = static_cast<uint8_t>(next.posts_per_minute);
  header[5] = static_cast<uint8_t>(next.posts_per_minute >> 8);
  header[6] = static_cast<uint8_t>(next.polls_per_minute);
  header[7] = static_cast<uint8_t>(next.polls_per_minute >> 8);
  header[8] = next.ban_count;
#if defined(NRF52_PLATFORM) || defined(STM32_PLATFORM)
  auto file = fs->open(ROOM_ACCESS_TEMP_PATH, FILE_O_WRITE);
#elif defined(ESP32_PLATFORM)
  auto file = fs->open(ROOM_ACCESS_TEMP_PATH, "w", true);
#else
  auto file = fs->open(ROOM_ACCESS_TEMP_PATH, "w");
#endif
  if (!file) return SaveResult::WriteFailure;
  bool written = !file.isDirectory()
      && file.write(header, sizeof(header)) == sizeof(header);
  uint32_t checksum = crc(UINT32_C(0xffffffff), header, sizeof(header));
  for (size_t i = 0; written && i < next.ban_count; ++i) {
    written = file.write(next.bans[i], ROOM_ACCESS_KEY_SIZE) == ROOM_ACCESS_KEY_SIZE;
    checksum = crc(checksum, next.bans[i], ROOM_ACCESS_KEY_SIZE);
  }
  checksum ^= UINT32_C(0xffffffff);
  uint8_t trailer[TRAILER_SIZE];
  for (size_t i = 0; i < sizeof(trailer); ++i) trailer[i] = static_cast<uint8_t>(checksum >> (8 * i));
  written = written && file.write(trailer, sizeof(trailer)) == sizeof(trailer);
  file.flush();
  file.close();
  if (!written || !readState(fs, ROOM_ACCESS_TEMP_PATH, checked, &next)) {
    removeArtifact(fs, ROOM_ACCESS_TEMP_PATH);
    return SaveResult::WriteFailure;
  }
  bool had_primary;
  if (!filePresence(fs, ROOM_ACCESS_PRIMARY_PATH, had_primary)
      || (had_primary && !fs->rename(ROOM_ACCESS_PRIMARY_PATH, ROOM_ACCESS_BACKUP_PATH))) {
    removeArtifact(fs, ROOM_ACCESS_TEMP_PATH);
    return SaveResult::WriteFailure;
  }
  if (!fs->rename(ROOM_ACCESS_TEMP_PATH, ROOM_ACCESS_PRIMARY_PATH)) {
    if (had_primary) fs->rename(ROOM_ACCESS_BACKUP_PATH, ROOM_ACCESS_PRIMARY_PATH);
    removeArtifact(fs, ROOM_ACCESS_TEMP_PATH);
    return SaveResult::WriteFailure;
  }
  // Commit occurred at rename. Failed backup housekeeping cannot reject a
  // policy that is already durable, nor publish an older in-memory policy.
  removeArtifact(fs, ROOM_ACCESS_BACKUP_PATH);
  return SaveResult::Saved;
}
} // namespace room_access_detail
} // namespace mesh
