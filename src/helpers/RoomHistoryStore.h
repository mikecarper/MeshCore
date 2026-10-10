#pragma once

#include <stddef.h>
#include <stdint.h>
#include <string.h>
#include "FilePresence.h"

namespace mesh {

static constexpr uint8_t ROOM_HISTORY_MAX_RECORDS = 32;
static constexpr uint8_t ROOM_HISTORY_JOURNAL_RECORDS = 64;
static constexpr size_t ROOM_HISTORY_TEXT_CAPACITY = 152;
static constexpr size_t ROOM_HISTORY_RECORD_SIZE = 192;
static constexpr size_t ROOM_HISTORY_HEADER_SIZE = 12;
static constexpr size_t ROOM_HISTORY_MAX_FILE_SIZE = ROOM_HISTORY_HEADER_SIZE
    + ROOM_HISTORY_JOURNAL_RECORDS * ROOM_HISTORY_RECORD_SIZE;
static const char ROOM_HISTORY_PATH[] = "/room_history";
static const char ROOM_HISTORY_TEMP_PATH[] = "/room_history.tmp";
static const char ROOM_HISTORY_BACKUP_PATH[] = "/room_history.bak";
static const char ROOM_HISTORY_CONFIG_PATH[] = "/room_hist_cfg";
static const char ROOM_HISTORY_CONFIG_TEMP_PATH[] = "/room_hist_cfg.tmp";
static const char ROOM_HISTORY_CONFIG_BACKUP_PATH[] = "/room_hist_cfg.bak";

struct RoomHistoryRecord {
  uint8_t author[32];
  uint32_t timestamp;
  char text[ROOM_HISTORY_TEXT_CAPACITY];
};

struct RoomHistoryState {
  uint8_t count = 0;
  uint8_t total_records = 0;
  bool needs_repair = false;
  bool present = false;
};

namespace room_history_detail {

inline uint32_t crcBytes(const uint8_t* data, size_t length) {
  uint32_t crc = UINT32_C(0xffffffff);
  for (size_t index = 0; index < length; ++index) {
    crc ^= data[index];
    for (uint8_t bit = 0; bit < 8; ++bit) {
      crc = (crc >> 1) ^ ((crc & 1) ? UINT32_C(0xedb88320) : 0);
    }
  }
  return crc ^ UINT32_C(0xffffffff);
}

inline void put32(uint8_t* data, uint32_t value) {
  for (uint8_t index = 0; index < 4; ++index) data[index] = value >> (8 * index);
}

inline uint32_t get32(const uint8_t* data) {
  uint32_t value = 0;
  for (uint8_t index = 0; index < 4; ++index) value |= uint32_t(data[index]) << (8 * index);
  return value;
}

template <typename Filesystem>
bool presence(Filesystem* fs, const char* path, bool& present) {
  return filePresence(fs, path, present);
}

template <typename Filesystem>
bool removeArtifact(Filesystem* fs, const char* path) {
  bool present;
  if (!presence(fs, path, present)) return false;
  if (!present) return true;
  fs->remove(path);
  return presence(fs, path, present) && !present;
}

template <typename Filesystem>
auto openRead(Filesystem* fs, const char* path)
#if defined(RP2040_PLATFORM)
    -> decltype(fs->open(path, static_cast<const char*>(nullptr))) { return fs->open(path, "r"); }
#else
    -> decltype(fs->open(path)) { return fs->open(path); }
#endif

template <typename Filesystem>
auto openWrite(Filesystem* fs, const char* path, bool append)
#if defined(NRF52_PLATFORM) || defined(STM32_PLATFORM)
    -> decltype(fs->open(path, FILE_O_WRITE)) {
  (void)append;
  return fs->open(path, FILE_O_WRITE); // verified temp absence makes this a fresh file
}
#elif defined(ESP32_PLATFORM)
    -> decltype(fs->open(path, static_cast<const char*>(nullptr), true)) { return fs->open(path, append ? "a" : "w", true); }
#else
    -> decltype(fs->open(path, static_cast<const char*>(nullptr))) { return fs->open(path, append ? "a" : "w"); }
#endif

inline void makeHeader(uint8_t (&image)[ROOM_HISTORY_HEADER_SIZE]) {
  const uint8_t prefix[] = { 'R', 'H', 'J', 1, 192, 0,
                            ROOM_HISTORY_MAX_RECORDS, ROOM_HISTORY_JOURNAL_RECORDS };
  memcpy(image, prefix, sizeof(prefix));
  put32(image + 8, crcBytes(image, 8));
}

template <typename File>
bool readHeader(File& file) {
  uint8_t actual[ROOM_HISTORY_HEADER_SIZE], expected[ROOM_HISTORY_HEADER_SIZE];
  makeHeader(expected);
  return static_cast<size_t>(file.read(actual, sizeof(actual))) == sizeof(actual)
      && memcmp(actual, expected, sizeof(actual)) == 0;
}

inline bool encodeRecord(const RoomHistoryRecord& record,
                         uint8_t (&image)[ROOM_HISTORY_RECORD_SIZE]) {
  size_t length = 0;
  while (length < ROOM_HISTORY_TEXT_CAPACITY && record.text[length] != 0) ++length;
  bool author_nonzero = false;
  for (uint8_t byte : record.author) author_nonzero |= byte != 0;
  if (length == ROOM_HISTORY_TEXT_CAPACITY || record.timestamp == 0 || !author_nonzero) return false;
  memset(image, 0, sizeof(image));
  memcpy(image, record.author, sizeof(record.author));
  put32(image + 32, record.timestamp);
  memcpy(image + 36, record.text, length);
  put32(image + 188, crcBytes(image, 188));
  return true;
}

enum class RecordRead { Valid, Corrupt, Unreadable };

// An incomplete physical tail is detected from file size by scanJournal. A
// short read inside a physically present record is an I/O failure, not proof
// that the record is corrupt and may safely be discarded.
template <typename File>
RecordRead readRecord(File& file, RoomHistoryRecord* record = nullptr,
                      const uint8_t* expected = nullptr) {
  uint8_t image[ROOM_HISTORY_RECORD_SIZE];
  if (static_cast<size_t>(file.read(image, sizeof(image))) != sizeof(image)) return RecordRead::Unreadable;
  bool author_nonzero = false;
  for (size_t index = 0; index < 32; ++index) author_nonzero |= image[index] != 0;
  bool terminated = false;
  for (size_t index = 36; index < 188; ++index) {
    if (terminated && image[index] != 0) return RecordRead::Corrupt;
    if (image[index] == 0) terminated = true;
  }
  if (!author_nonzero || get32(image + 32) == 0 || !terminated
      || get32(image + 188) != crcBytes(image, 188)) return RecordRead::Corrupt;
  if (expected && memcmp(image, expected, sizeof(image)) != 0) return RecordRead::Corrupt;
  if (record) {
    memcpy(record->author, image, sizeof(record->author));
    record->timestamp = get32(image + 32);
    memcpy(record->text, image + 36, sizeof(record->text));
  }
  return RecordRead::Valid;
}

template <typename Filesystem>
bool scanJournal(Filesystem* fs, const char* path, RoomHistoryState& result) {
  auto file = openRead(fs, path);
  if (!file) return false;
  const size_t size = file.size();
  bool valid = !file.isDirectory() && size >= ROOM_HISTORY_HEADER_SIZE
      && size <= ROOM_HISTORY_MAX_FILE_SIZE && readHeader(file);
  RoomHistoryState staged;
  staged.present = true;
  const size_t physical_records = size >= ROOM_HISTORY_HEADER_SIZE
      ? (size - ROOM_HISTORY_HEADER_SIZE) / ROOM_HISTORY_RECORD_SIZE : 0;
  for (size_t index = 0; valid && index < physical_records; ++index) {
    const RecordRead read = readRecord(file);
    if (read == RecordRead::Unreadable) valid = false;
    else if (read == RecordRead::Corrupt) {
      // A failed append can leave only the final physical record corrupt.
      // Later complete records prove this is older storage corruption: keep
      // the entire primary and do not publish a prefix for automatic repair.
      valid = index + 1 == physical_records;
      break;
    }
    else ++staged.total_records;
  }
  staged.count = staged.total_records > ROOM_HISTORY_MAX_RECORDS
      ? ROOM_HISTORY_MAX_RECORDS : staged.total_records;
  staged.needs_repair = size != ROOM_HISTORY_HEADER_SIZE
      + staged.total_records * ROOM_HISTORY_RECORD_SIZE;
  file.close();
  if (valid) result = staged;
  return valid;
}

template <typename Filesystem>
bool readConfig(Filesystem* fs, const char* path, bool& enabled) {
  auto file = openRead(fs, path);
  if (!file) return false;
  uint8_t image[16];
  bool valid = !file.isDirectory() && file.size() == sizeof(image)
      && static_cast<size_t>(file.read(image, sizeof(image))) == sizeof(image);
  file.close();
  const uint8_t magic[] = { 'R', 'H', 'C', 1 };
  valid = valid && memcmp(image, magic, sizeof(magic)) == 0 && image[4] <= 1
      && get32(image + 12) == crcBytes(image, 12);
  for (size_t index = 5; valid && index < 12; ++index) valid = image[index] == 0;
  if (valid) enabled = image[4] != 0;
  return valid;
}

// Primary authority is preserved even when corrupt or from a future format.
// Only an absent primary permits restoring backup; temp is never promoted.
template <typename Filesystem, typename Validator>
bool recover(Filesystem* fs, const char* primary, const char* temp,
              const char* backup, Validator validate, bool strict, bool& found) {
  if (!presence(fs, primary, found)) return false;
  if (found) {
    if (!validate(primary)) return false;
    const bool cleaned_temp = removeArtifact(fs, temp);
    const bool cleaned_backup = removeArtifact(fs, backup);
    return !strict || (cleaned_temp && cleaned_backup);
  }
  bool had_backup;
  if (!presence(fs, backup, had_backup)) return false;
  if (had_backup) {
    if (!validate(backup) || !fs->rename(backup, primary)) return false;
    found = true;
  }
  const bool cleaned_temp = removeArtifact(fs, temp);
  return !strict || cleaned_temp;
}

template <typename Filesystem>
bool publish(Filesystem* fs, const char* primary, const char* temp, const char* backup) {
  bool had_primary;
  if (!presence(fs, primary, had_primary)
      || (had_primary && !fs->rename(primary, backup))) {
    removeArtifact(fs, temp);
    return false;
  }
  if (!fs->rename(temp, primary)) {
    if (had_primary) fs->rename(backup, primary);
    removeArtifact(fs, temp);
    return false;
  }
  removeArtifact(fs, backup); // post-commit cleanup cannot turn success into failure
  return true;
}

struct EmptyProvider {
  bool operator()(uint8_t, RoomHistoryRecord&) const { return false; }
};

} // namespace room_history_detail

template <typename Filesystem>
bool loadRoomHistoryConfig(Filesystem* fs, bool& enabled) {
  if (!fs) return false;
  bool found, staged = false;
  const auto validate = [&](const char* path) { return room_history_detail::readConfig(fs, path, staged); };
  if (!room_history_detail::recover(fs, ROOM_HISTORY_CONFIG_PATH,
          ROOM_HISTORY_CONFIG_TEMP_PATH, ROOM_HISTORY_CONFIG_BACKUP_PATH,
          validate, false, found)) return false;
  enabled = staged;
  return true;
}

template <typename Filesystem>
bool saveRoomHistoryConfig(Filesystem* fs, bool enabled) {
  if (!fs) return false;
  bool found, previous = false;
  const auto validate = [&](const char* path) { return room_history_detail::readConfig(fs, path, previous); };
  if (!room_history_detail::recover(fs, ROOM_HISTORY_CONFIG_PATH,
          ROOM_HISTORY_CONFIG_TEMP_PATH, ROOM_HISTORY_CONFIG_BACKUP_PATH,
          validate, true, found)) return false;
  uint8_t image[16] = { 'R', 'H', 'C', 1, uint8_t(enabled ? 1 : 0) };
  room_history_detail::put32(image + 12, room_history_detail::crcBytes(image, 12));
  auto file = room_history_detail::openWrite(fs, ROOM_HISTORY_CONFIG_TEMP_PATH, false);
  if (!file) return false;
  if (file.isDirectory()) {
    file.close();
    room_history_detail::removeArtifact(fs, ROOM_HISTORY_CONFIG_TEMP_PATH);
    return false;
  }
  const bool written = file.write(image, sizeof(image)) == sizeof(image);
  file.flush(); file.close();
  bool readback = !enabled;
  if (!written || !room_history_detail::readConfig(fs, ROOM_HISTORY_CONFIG_TEMP_PATH, readback)
      || readback != enabled) {
    room_history_detail::removeArtifact(fs, ROOM_HISTORY_CONFIG_TEMP_PATH);
    return false;
  }
  return room_history_detail::publish(fs, ROOM_HISTORY_CONFIG_PATH,
                                      ROOM_HISTORY_CONFIG_TEMP_PATH, ROOM_HISTORY_CONFIG_BACKUP_PATH);
}

// Validate the complete readable prefix before calling the consumer. The
// consumer receives only the newest 32 records, oldest first. A later read or
// consumer failure leaves state unchanged; boot callers must clear any partial
// destination ring. No live ring reload is needed for ordinary appends.
template <typename Filesystem, typename Consumer>
bool loadRoomHistory(Filesystem* fs, RoomHistoryState& state, Consumer consume) {
  if (!fs) return false;
  RoomHistoryState staged;
  bool found;
  const auto validate = [&](const char* path) { return room_history_detail::scanJournal(fs, path, staged); };
  if (!room_history_detail::recover(fs, ROOM_HISTORY_PATH, ROOM_HISTORY_TEMP_PATH,
          ROOM_HISTORY_BACKUP_PATH, validate, false, found)) return false;
  if (found && staged.count != 0) {
    auto file = room_history_detail::openRead(fs, ROOM_HISTORY_PATH);
    if (!file) return false;
    bool valid = !file.isDirectory() && room_history_detail::readHeader(file)
        && file.seek(ROOM_HISTORY_HEADER_SIZE
                     + (staged.total_records - staged.count) * ROOM_HISTORY_RECORD_SIZE);
    RoomHistoryRecord record;
    for (uint8_t index = 0; valid && index < staged.count; ++index) {
      valid = room_history_detail::readRecord(file, &record) == room_history_detail::RecordRead::Valid
          && consume(index, record);
    }
    file.close();
    if (!valid) return false;
  }
  state = staged;
  return true;
}

// Provider(index, record) supplies an immutable, oldest-first RAM snapshot.
// One record at a time is rendered and read back; there is no second 6 KB ring.
// Used to enable history, compact 64 journal entries, repair a failed tail, or
// clear with count zero. State changes only after atomic publication succeeds.
template <typename Filesystem, typename Provider>
bool saveRoomHistorySnapshot(Filesystem* fs, RoomHistoryState& state,
                              uint8_t count, Provider provide) {
  if (!fs || count > ROOM_HISTORY_MAX_RECORDS) return false;
  RoomHistoryState old;
  bool found;
  const auto validate = [&](const char* path) { return room_history_detail::scanJournal(fs, path, old); };
  if (!room_history_detail::recover(fs, ROOM_HISTORY_PATH, ROOM_HISTORY_TEMP_PATH,
          ROOM_HISTORY_BACKUP_PATH, validate, true, found)) return false;
  auto file = room_history_detail::openWrite(fs, ROOM_HISTORY_TEMP_PATH, false);
  if (!file) return false;
  if (file.isDirectory()) {
    file.close();
    room_history_detail::removeArtifact(fs, ROOM_HISTORY_TEMP_PATH);
    return false;
  }
  uint8_t header[ROOM_HISTORY_HEADER_SIZE]; room_history_detail::makeHeader(header);
  bool valid = file.write(header, sizeof(header)) == sizeof(header);
  RoomHistoryRecord record;
  uint8_t image[ROOM_HISTORY_RECORD_SIZE];
  for (uint8_t index = 0; valid && index < count; ++index) {
    valid = provide(index, record) && room_history_detail::encodeRecord(record, image)
        && file.write(image, sizeof(image)) == sizeof(image);
  }
  file.flush(); file.close();
  if (valid) {
    auto verify = room_history_detail::openRead(fs, ROOM_HISTORY_TEMP_PATH);
    if (!verify) valid = false;
    else {
      valid = !verify.isDirectory()
          && verify.size() == ROOM_HISTORY_HEADER_SIZE + count * ROOM_HISTORY_RECORD_SIZE
          && room_history_detail::readHeader(verify);
      for (uint8_t index = 0; valid && index < count; ++index) {
        valid = provide(index, record) && room_history_detail::encodeRecord(record, image)
            && room_history_detail::readRecord(verify, nullptr, image) == room_history_detail::RecordRead::Valid;
      }
      verify.close();
    }
  }
  if (!valid) {
    room_history_detail::removeArtifact(fs, ROOM_HISTORY_TEMP_PATH);
    return false;
  }
  if (!room_history_detail::publish(fs, ROOM_HISTORY_PATH, ROOM_HISTORY_TEMP_PATH,
                                   ROOM_HISTORY_BACKUP_PATH)) return false;
  state.count = state.total_records = count;
  state.needs_repair = false;
  state.present = true;
  return true;
}

template <typename Filesystem>
bool clearRoomHistory(Filesystem* fs, RoomHistoryState& state) {
  return saveRoomHistorySnapshot(fs, state, 0, room_history_detail::EmptyProvider{});
}

// Single-writer main-loop use. A failed append can leave an uncommitted tail;
// mark it for snapshot repair from the unchanged trusted live ring before a
// retry. Never ACK or update the message replay cache when this returns false.
template <typename Filesystem>
bool appendRoomHistory(Filesystem* fs, RoomHistoryState& state,
                        const uint8_t* author, uint32_t timestamp, const char* text) {
  if (!fs || !author || !text || state.needs_repair
      || state.total_records >= ROOM_HISTORY_JOURNAL_RECORDS) return false;
  RoomHistoryRecord record;
  memcpy(record.author, author, sizeof(record.author));
  record.timestamp = timestamp;
  size_t length = 0;
  while (length < ROOM_HISTORY_TEXT_CAPACITY && text[length] != 0) ++length;
  if (length == ROOM_HISTORY_TEXT_CAPACITY) return false;
  memset(record.text, 0, sizeof(record.text)); memcpy(record.text, text, length);
  uint8_t image[ROOM_HISTORY_RECORD_SIZE];
  if (!room_history_detail::encodeRecord(record, image)) return false;
  RoomHistoryState disk;
  bool found;
  const auto validate = [&](const char* path) { return room_history_detail::scanJournal(fs, path, disk); };
  if (!room_history_detail::recover(fs, ROOM_HISTORY_PATH, ROOM_HISTORY_TEMP_PATH,
          ROOM_HISTORY_BACKUP_PATH, validate, true, found)) {
    state.needs_repair = true;
    return false;
  }
  if (!state.present) {
    // A caller must load an existing journal before appending. In particular,
    // never turn a missed/failed load into an implicit clear of accepted posts.
    if (found || state.count != 0 || state.total_records != 0) {
      state.needs_repair = true;
      return false;
    }
    if (!clearRoomHistory(fs, state)) return false;
    disk = state;
    found = true;
  }
  if (!found || disk.needs_repair || disk.total_records != state.total_records
      || disk.count != state.count) {
    state.needs_repair = true;
    return false;
  }
  const size_t expected_size = ROOM_HISTORY_HEADER_SIZE
      + state.total_records * ROOM_HISTORY_RECORD_SIZE;
  auto file = room_history_detail::openWrite(fs, ROOM_HISTORY_PATH, true);
  if (!file) { state.needs_repair = true; return false; }
  if (file.isDirectory()) { file.close(); state.needs_repair = true; return false; }
  const bool written = file.size() == expected_size
      && file.write(image, sizeof(image)) == sizeof(image);
  file.flush(); file.close();
  bool verified = false;
  if (written) {
    auto verify = room_history_detail::openRead(fs, ROOM_HISTORY_PATH);
    if (verify) {
      verified = !verify.isDirectory()
          && verify.size() == expected_size + ROOM_HISTORY_RECORD_SIZE
          && room_history_detail::readHeader(verify) && verify.seek(expected_size)
          && room_history_detail::readRecord(verify, nullptr, image) == room_history_detail::RecordRead::Valid;
      verify.close();
    }
  }
  if (!verified) { state.needs_repair = true; return false; }
  ++state.total_records;
  state.count = state.total_records > ROOM_HISTORY_MAX_RECORDS ? ROOM_HISTORY_MAX_RECORDS : state.total_records;
  return true;
}

} // namespace mesh
