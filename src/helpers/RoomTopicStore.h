#pragma once

#include <stddef.h>
#include <stdint.h>
#include <string.h>
#if defined(ESP32_PLATFORM)
#include "FilePresence.h"
#endif

namespace mesh {

static constexpr size_t ROOM_TOPIC_MAX_TEXT_LEN = 151;
static constexpr size_t ROOM_TOPIC_TEXT_CAPACITY = ROOM_TOPIC_MAX_TEXT_LEN + 1;
static constexpr size_t ROOM_TOPIC_IMAGE_SIZE = 160;
static const char ROOM_TOPIC_PRIMARY_PATH[] = "/room_prefs";
static const char ROOM_TOPIC_TEMP_PATH[] = "/room_prefs.tmp";
static const char ROOM_TOPIC_BACKUP_PATH[] = "/room_prefs.bak";

namespace room_topic_detail {

static const uint8_t MAGIC[] = { 'R', 'T', 'P', 1 };

inline uint32_t crcByte(uint32_t crc, uint8_t value) {
  crc ^= value;
  for (uint8_t bit = 0; bit < 8; ++bit) {
    crc = (crc >> 1) ^ ((crc & 1) ? UINT32_C(0xedb88320) : 0);
  }
  return crc;
}

template <typename Filesystem>
bool presence(Filesystem* fs, const char* path, bool& present) {
#if defined(ESP32_PLATFORM)
  return filePresence(fs, path, present);
#else
  present = fs->exists(path);
  return true;
#endif
}

template <typename Filesystem>
bool removeArtifact(Filesystem* fs, const char* path) {
  bool present;
  if (!presence(fs, path, present)) return false;
  if (!present) return true;
  fs->remove(path);
  return presence(fs, path, present) && !present;
}

// Validate while streaming so readback does not nest another full image on
// the loop stack. A supplied expected image additionally proves an exact write.
template <typename Filesystem>
bool readImage(Filesystem* fs, const char* path, char* text = nullptr,
               const uint8_t* expected = nullptr) {
#if defined(RP2040_PLATFORM)
  auto file = fs->open(path, "r");
#else
  auto file = fs->open(path);
#endif
  if (!file) return false;
  bool valid = !file.isDirectory() && file.size() == ROOM_TOPIC_IMAGE_SIZE;
  uint32_t crc = UINT32_C(0xffffffff);
  uint32_t stored_crc = 0;
  bool terminated = false;
  uint8_t chunk[16];
  for (size_t offset = 0; valid && offset < ROOM_TOPIC_IMAGE_SIZE;
       offset += sizeof(chunk)) {
    if (static_cast<size_t>(file.read(chunk, sizeof(chunk))) != sizeof(chunk)) {
      valid = false;
      break;
    }
    if (expected && memcmp(chunk, expected + offset, sizeof(chunk)) != 0) {
      valid = false;
      break;
    }
    for (size_t index = 0; index < sizeof(chunk); ++index) {
      const size_t position = offset + index;
      const uint8_t value = chunk[index];
      if (position < ROOM_TOPIC_IMAGE_SIZE - 4) {
        crc = crcByte(crc, value);
      } else {
        stored_crc |= static_cast<uint32_t>(value)
                      << (8 * (position - (ROOM_TOPIC_IMAGE_SIZE - 4)));
      }
      if (position < sizeof(MAGIC)) {
        if (value != MAGIC[position]) valid = false;
      } else if (position < sizeof(MAGIC) + ROOM_TOPIC_TEXT_CAPACITY) {
        if (terminated && value != 0) valid = false;
        if (value == 0) terminated = true;
        if (text) text[position - sizeof(MAGIC)] = static_cast<char>(value);
      }
    }
  }
  file.close();
  return valid && terminated && (crc ^ UINT32_C(0xffffffff)) == stored_crc;
}

// A present primary is authoritative, including when it is corrupt or from a
// newer format. Only an absent primary permits restoration of a valid backup.
// An uncommitted temp is discarded, never promoted. Load may tolerate failed
// housekeeping; save must finish it before beginning another transaction.
template <typename Filesystem>
bool recover(Filesystem* fs, char* text, bool require_cleanup) {
  bool primary;
  if (!presence(fs, ROOM_TOPIC_PRIMARY_PATH, primary)) return false;
  if (primary) {
    if (!readImage(fs, ROOM_TOPIC_PRIMARY_PATH, text)) return false;
    const bool temp_removed = removeArtifact(fs, ROOM_TOPIC_TEMP_PATH);
    const bool backup_removed = removeArtifact(fs, ROOM_TOPIC_BACKUP_PATH);
    return !require_cleanup || (temp_removed && backup_removed);
  }
  bool backup;
  if (!presence(fs, ROOM_TOPIC_BACKUP_PATH, backup)) return false;
  if (backup) {
    if (!readImage(fs, ROOM_TOPIC_BACKUP_PATH, text)
        || !fs->rename(ROOM_TOPIC_BACKUP_PATH, ROOM_TOPIC_PRIMARY_PATH)) {
      return false;
    }
  }
  const bool temp_removed = removeArtifact(fs, ROOM_TOPIC_TEMP_PATH);
  return !require_cleanup || temp_removed;
}

} // namespace room_topic_detail

template <typename Filesystem>
bool loadRoomTopic(Filesystem* fs, char (&text)[ROOM_TOPIC_TEXT_CAPACITY]) {
  memset(text, 0, sizeof(text));
  if (!fs || !room_topic_detail::recover(fs, text, false)) {
    memset(text, 0, sizeof(text));
    return false;
  }
  return true;
}

// Single-writer, main-loop owned. Publish only a closed, exactly verified temp
// image. The backup sequence also works on SPIFFS, whose rename cannot replace
// an existing destination. Cleanup after the commit never changes its result.
template <typename Filesystem>
bool saveRoomTopic(Filesystem* fs, const char* text) {
  if (!fs || !text) return false;
  size_t length = 0;
  while (length < ROOM_TOPIC_TEXT_CAPACITY && text[length] != 0) ++length;
  if (length > ROOM_TOPIC_MAX_TEXT_LEN) return false;

  uint8_t image[ROOM_TOPIC_IMAGE_SIZE] = {};
  memcpy(image, room_topic_detail::MAGIC, sizeof(room_topic_detail::MAGIC));
  memcpy(image + sizeof(room_topic_detail::MAGIC), text, length);
  uint32_t crc = UINT32_C(0xffffffff);
  for (size_t index = 0; index < ROOM_TOPIC_IMAGE_SIZE - 4; ++index) {
    crc = room_topic_detail::crcByte(crc, image[index]);
  }
  crc ^= UINT32_C(0xffffffff);
  for (size_t index = 0; index < 4; ++index) {
    image[ROOM_TOPIC_IMAGE_SIZE - 4 + index] =
        static_cast<uint8_t>(crc >> (8 * index));
  }

  if (!room_topic_detail::recover(fs, nullptr, true)) return false;
#if defined(NRF52_PLATFORM) || defined(STM32_PLATFORM)
  auto file = fs->open(ROOM_TOPIC_TEMP_PATH, FILE_O_WRITE);
#elif defined(ESP32_PLATFORM)
  auto file = fs->open(ROOM_TOPIC_TEMP_PATH, "w", true);
#else
  auto file = fs->open(ROOM_TOPIC_TEMP_PATH, "w");
#endif
  if (!file) return false;
  if (file.isDirectory()) {
    file.close();
    room_topic_detail::removeArtifact(fs, ROOM_TOPIC_TEMP_PATH);
    return false;
  }
  const bool written = file.write(image, sizeof(image)) == sizeof(image);
  file.flush();
  file.close();
  if (!written || !room_topic_detail::readImage(
          fs, ROOM_TOPIC_TEMP_PATH, nullptr, image)) {
    room_topic_detail::removeArtifact(fs, ROOM_TOPIC_TEMP_PATH);
    return false;
  }

  bool had_primary;
  if (!room_topic_detail::presence(fs, ROOM_TOPIC_PRIMARY_PATH, had_primary)
      || (had_primary && !fs->rename(
              ROOM_TOPIC_PRIMARY_PATH, ROOM_TOPIC_BACKUP_PATH))) {
    room_topic_detail::removeArtifact(fs, ROOM_TOPIC_TEMP_PATH);
    return false;
  }
  if (!fs->rename(ROOM_TOPIC_TEMP_PATH, ROOM_TOPIC_PRIMARY_PATH)) {
    if (had_primary) {
      fs->rename(ROOM_TOPIC_BACKUP_PATH, ROOM_TOPIC_PRIMARY_PATH);
    }
    room_topic_detail::removeArtifact(fs, ROOM_TOPIC_TEMP_PATH);
    return false;
  }
  room_topic_detail::removeArtifact(fs, ROOM_TOPIC_BACKUP_PATH);
  return true;
}

} // namespace mesh
