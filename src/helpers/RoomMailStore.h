#pragma once

#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <SHA256.h>
#include "FilePresence.h"

namespace mesh {

static constexpr uint8_t ROOM_MAIL_MAX_OWNERS = 32;
static constexpr uint8_t ROOM_MAIL_MAX_MESSAGES = 16;
static constexpr uint8_t ROOM_MAIL_MAX_PER_OWNER = 4;
static constexpr uint8_t ROOM_MAIL_MAX_ALLOWED = 8;
static constexpr uint8_t ROOM_MAIL_RETRY_RECEIPTS = 8;
static constexpr size_t ROOM_MAIL_MAX_BODY_LENGTH = 512;
static constexpr size_t ROOM_MAIL_READ_MAX_CHUNK = 128;
static const char ROOM_MAIL_PRIMARY_PATH[] = "/room_mail";
static const char ROOM_MAIL_TEMP_PATH[] = "/room_mail.tmp";
static const char ROOM_MAIL_BACKUP_PATH[] = "/room_mail.bak";

enum class RoomMailResult : uint8_t {
  Success, Invalid, Unavailable, WriteFailure, NotFound, StaleVersion,
  Forbidden, Full, Duplicate, Mismatch
};
enum class RoomMailMode : uint8_t { Closed = 0, Public = 1, Private = 2 };
struct RoomMailSettings {
  RoomMailMode mode = RoomMailMode::Closed;
  bool mailbox_only = false;
  uint8_t allowed_count = 0;
  uint8_t allowed[ROOM_MAIL_MAX_ALLOWED][32] = {};
};
struct RoomMailStatus {
  uint32_t revision = 0;
  uint8_t count = 0;
  RoomMailSettings settings;
};
struct RoomMailMessage {
  uint32_t id = 0;
  uint64_t request_id = 0;
  uint8_t sender[32] = {};
  uint16_t length = 0;
  uint32_t created = 0;
};
struct RoomMailStats {
  uint32_t revision = 0;
  uint8_t owners = 0;
  uint8_t messages = 0;
};

// The caller supplies the authenticated full identity for owner operations.
// Public means eligible room writers; room roles and bans remain the caller's
// responsibility. The room operator is trusted: bodies are plaintext at rest.
inline bool roomMailSenderAllowed(const RoomMailSettings& settings,
                                  const uint8_t* owner, const uint8_t* sender) {
  if (!owner || !sender) return false;
  if (memcmp(owner, sender, 32) == 0) return true;
  if (settings.mode == RoomMailMode::Public) return true;
  if (settings.mode != RoomMailMode::Private) return false;
  for (uint8_t i = 0; i < settings.allowed_count && i < ROOM_MAIL_MAX_ALLOWED; ++i)
    if (memcmp(settings.allowed[i], sender, 32) == 0) return true;
  return false;
}

namespace room_mail_detail {
static constexpr size_t INDEX_HEADER_SIZE = 16;
static constexpr size_t INDEX_ENTRY_SIZE = 36;
static constexpr size_t OWNER_HEADER_SIZE = 48;
static constexpr size_t POLICY_SIZE = 260;
static constexpr size_t RECORD_SIZE = 88;
static constexpr size_t BODY_START = OWNER_HEADER_SIZE + POLICY_SIZE
    + ROOM_MAIL_RETRY_RECEIPTS * RECORD_SIZE;
static constexpr size_t STREAM_CHUNK = 64;
static const uint8_t INDEX_MAGIC[4] = {'R', 'M', 'I', 1};
static const uint8_t OWNER_MAGIC[4] = {'R', 'M', 'O', 1};
enum : uint8_t { Queued = 1, Acknowledged = 2, Deleted = 3 };
struct Entry {
  uint8_t owner[32] = {};
  uint8_t slot = 0, bank = 0, count = 0;
};
struct Index {
  uint32_t revision = 0, slots = 0;
  uint8_t count = 0, messages = 0;
};
struct Record {
  RoomMailMessage message;
  uint8_t digest[32] = {};
  uint16_t offset = 0;
  uint8_t state = 0;
};
struct Owner {
  RoomMailSettings settings;
  uint32_t revision = 0;
  uint8_t count = 0, receipts = 0;
  Record records[ROOM_MAIL_RETRY_RECEIPTS];
};
static_assert(sizeof(Owner) <= 1152, "Mailbox working metadata must remain bounded");

inline void clearSettings(RoomMailSettings& settings) {
  settings.mode = RoomMailMode::Closed;
  settings.mailbox_only = false; settings.allowed_count = 0;
  memset(settings.allowed, 0, sizeof(settings.allowed));
}
inline void clearStatus(RoomMailStatus& status) {
  status.revision = 0; status.count = 0; clearSettings(status.settings);
}

// Avoid an aggregate Owner{} assignment: some size-optimized embedded builds
// reserve a second full Owner temporary on the stack even for the error path.
inline void clearOwner(Owner& owner) {
  owner.revision = 0; owner.count = 0; owner.receipts = 0;
  clearSettings(owner.settings);
  for (uint8_t i = 0; i < ROOM_MAIL_RETRY_RECEIPTS; ++i) {
    Record& record = owner.records[i];
    record.message.id = 0; record.message.request_id = 0;
    record.message.length = 0; record.message.created = 0;
    memset(record.message.sender, 0, sizeof(record.message.sender));
    memset(record.digest, 0, sizeof(record.digest));
    record.offset = 0; record.state = 0;
  }
}

inline bool nonzero(const uint8_t* key) {
  if (!key) return false;
  for (uint8_t i = 0; i < 32; ++i) if (key[i]) return true;
  return false;
}
inline uint32_t get32(const uint8_t* p) {
  return uint32_t(p[0]) | (uint32_t(p[1]) << 8) | (uint32_t(p[2]) << 16) | (uint32_t(p[3]) << 24);
}
inline void put32(uint8_t* p, uint32_t value) {
  for (uint8_t i = 0; i < 4; ++i) p[i] = uint8_t(value >> (8 * i));
}
inline uint16_t get16(const uint8_t* p) { return uint16_t(p[0]) | (uint16_t(p[1]) << 8); }
inline void put16(uint8_t* p, uint16_t value) { p[0] = uint8_t(value); p[1] = uint8_t(value >> 8); }
inline uint64_t get64(const uint8_t* p) {
  return uint64_t(get32(p)) | (uint64_t(get32(p + 4)) << 32);
}
inline void put64(uint8_t* p, uint64_t value) {
  put32(p, uint32_t(value)); put32(p + 4, uint32_t(value >> 32));
}
inline uint32_t crc(uint32_t value, const uint8_t* bytes, size_t length) {
  while (length--) {
    value ^= *bytes++;
    for (uint8_t bit = 0; bit < 8; ++bit)
      value = (value >> 1) ^ ((value & 1) ? UINT32_C(0xedb88320) : 0);
  }
  return value;
}
inline bool zero(const uint8_t* bytes, size_t length) {
  while (length--) if (*bytes++) return false;
  return true;
}
inline bool validSettings(const RoomMailSettings& s) {
  if (uint8_t(s.mode) > 2 || s.allowed_count > ROOM_MAIL_MAX_ALLOWED) return false;
  for (uint8_t i = 0; i < s.allowed_count; ++i) {
    if (!nonzero(s.allowed[i])) return false;
    for (uint8_t j = 0; j < i; ++j) if (!memcmp(s.allowed[i], s.allowed[j], 32)) return false;
  }
  return true;
}
// Complete UTF-8 text only. Newlines and tabs are valid; embedded NUL is not.
inline bool validBody(const char* body, size_t length) {
  if (!body || !length || length > ROOM_MAIL_MAX_BODY_LENGTH) return false;
  for (size_t i = 0; i < length;) {
    const uint8_t first = uint8_t(body[i++]);
    if (!first) return false;
    if (first < 0x80) continue;
    uint8_t count; uint32_t point, minimum;
    if (first >= 0xc2 && first <= 0xdf) { count = 1; point = first & 0x1f; minimum = 0x80; }
    else if (first >= 0xe0 && first <= 0xef) { count = 2; point = first & 0x0f; minimum = 0x800; }
    else if (first >= 0xf0 && first <= 0xf4) { count = 3; point = first & 7; minimum = 0x10000; }
    else return false;
    if (count > length - i) return false;
    while (count--) {
      const uint8_t next = uint8_t(body[i++]);
      if ((next & 0xc0) != 0x80) return false;
      point = (point << 6) | (next & 0x3f);
    }
    if (point < minimum || point > 0x10ffff || (point >= 0xd800 && point <= 0xdfff)) return false;
  }
  return true;
}
inline void bodyDigest(const char* body, size_t length, uint8_t* output) {
  SHA256 sha; sha.update(body, length); sha.finalize(output, 32);
}
inline void ownerPath(const Entry& entry, char (&path)[24]) {
  snprintf(path, sizeof(path), "/room_mail_%u.%c", unsigned(entry.slot), entry.bank ? 'b' : 'a');
}
inline void encodeEntry(const Entry& entry, uint8_t (&bytes)[INDEX_ENTRY_SIZE]) {
  memcpy(bytes, entry.owner, 32); bytes[32] = entry.slot; bytes[33] = entry.bank;
  bytes[34] = entry.count; bytes[35] = 0;
}
inline void encodeRecord(const Record& r, uint8_t (&bytes)[RECORD_SIZE]) {
  memset(bytes, 0, sizeof(bytes));
  if (!r.state) return;
  memcpy(bytes, r.message.sender, 32); put64(bytes + 32, r.message.request_id);
  put32(bytes + 40, r.message.id); put32(bytes + 44, r.message.created);
  put16(bytes + 48, r.message.length); bytes[50] = r.state;
  memcpy(bytes + 52, r.digest, 32);
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

// Index validation is streamed: no 32-owner RAM registry. Keys are sorted and
// slots are stable independently of ACL eviction or index cursor changes.
template <typename Filesystem>
bool readIndex(Filesystem* fs, const char* path, Index& index,
               const uint8_t* owner = nullptr, Entry* found = nullptr,
               bool* has_owner = nullptr, const uint32_t* expected_crc = nullptr) {
  index = Index{}; if (has_owner) *has_owner = false;
  auto file = openRead(fs, path); if (!file) return false;
  uint8_t header[INDEX_HEADER_SIZE];
  bool valid = !file.isDirectory() && size_t(file.read(header, sizeof(header))) == sizeof(header);
  if (valid) valid = !memcmp(header, INDEX_MAGIC, 4) && get32(header + 4) != 0
      && header[8] <= ROOM_MAIL_MAX_OWNERS && header[9] <= ROOM_MAIL_MAX_MESSAGES
      && zero(header + 10, 6) && file.size() == INDEX_HEADER_SIZE + header[8] * INDEX_ENTRY_SIZE + 4;
  uint32_t sum = valid ? crc(UINT32_C(0xffffffff), header, sizeof(header)) : 0;
  uint8_t previous[32] = {}; uint8_t total = 0;
  if (valid) { index.revision = get32(header + 4); index.count = header[8]; index.messages = header[9]; }
  for (uint8_t i = 0; valid && i < index.count; ++i) {
    uint8_t bytes[INDEX_ENTRY_SIZE];
    valid = size_t(file.read(bytes, sizeof(bytes))) == sizeof(bytes);
    if (!valid) break;
    sum = crc(sum, bytes, sizeof(bytes));
    valid = nonzero(bytes) && bytes[32] < ROOM_MAIL_MAX_OWNERS && bytes[33] <= 1
        && bytes[34] <= ROOM_MAIL_MAX_PER_OWNER && bytes[35] == 0
        && !(index.slots & (UINT32_C(1) << bytes[32]))
        && (i == 0 || memcmp(previous, bytes, 32) < 0);
    if (!valid) break;
    index.slots |= UINT32_C(1) << bytes[32]; total += bytes[34]; memcpy(previous, bytes, 32);
    if (owner && !memcmp(owner, bytes, 32)) {
      if (found) { memcpy(found->owner, bytes, 32); found->slot = bytes[32]; found->bank = bytes[33]; found->count = bytes[34]; }
      if (has_owner) *has_owner = true;
    }
  }
  uint8_t trailer[4]; sum ^= UINT32_C(0xffffffff);
  if (valid) valid = total == index.messages && size_t(file.read(trailer, 4)) == 4
      && get32(trailer) == sum && (!expected_crc || sum == *expected_crc);
  file.close();
  if (!valid) { index = Index{}; if (has_owner) *has_owner = false; }
  return valid;
}

// Present but invalid primary metadata is authoritative. Preserve all evidence.
// Only absence permits restoration of a verified backup. Temp is uncommitted.
template <typename Filesystem>
bool recover(Filesystem* fs, Index& index, const uint8_t* owner,
             Entry& entry, bool& found, bool cleanup) {
  index = Index{}; found = false;
  bool present;
  if (!fs || !filePresence(fs, ROOM_MAIL_PRIMARY_PATH, present)) return false;
  if (present) {
    if (!readIndex(fs, ROOM_MAIL_PRIMARY_PATH, index, owner, &entry, &found)) return false;
  } else {
    if (!filePresence(fs, ROOM_MAIL_BACKUP_PATH, present)) return false;
    if (present) {
      if (!readIndex(fs, ROOM_MAIL_BACKUP_PATH, index, owner, &entry, &found)
          || !fs->rename(ROOM_MAIL_BACKUP_PATH, ROOM_MAIL_PRIMARY_PATH)) return false;
    }
  }
  if (cleanup) return removeArtifact(fs, ROOM_MAIL_TEMP_PATH) && removeArtifact(fs, ROOM_MAIL_BACKUP_PATH);
  return true;
}

// Entire selected bank is verified before releasing any requested slice. Owner
// metadata is optional: validation needs only fixed 88-byte/64-byte buffers.
template <typename Filesystem>
bool readOwner(Filesystem* fs, const Entry& entry, Owner* out,
               uint32_t selected_id = 0, size_t offset = 0,
               uint8_t* output = nullptr, size_t capacity = 0,
               const uint32_t* expected_crc = nullptr,
               RoomMailStatus* status = nullptr, bool* mailbox_only = nullptr,
               uint32_t max_revision = UINT32_MAX) {
  if (out) clearOwner(*out);
  if (status) clearStatus(*status);
  if (mailbox_only) *mailbox_only = false;
  char path[24]; ownerPath(entry, path);
  auto file = openRead(fs, path); if (!file) return false;
  uint8_t header[OWNER_HEADER_SIZE];
  bool valid = !file.isDirectory() && size_t(file.read(header, sizeof(header))) == sizeof(header);
  if (valid) valid = !memcmp(header, OWNER_MAGIC, 4) && !memcmp(header + 4, entry.owner, 32)
      && get32(header + 36) != 0 && get32(header + 36) <= max_revision
      && header[40] == entry.slot && header[41] == entry.bank
      && header[42] == entry.count && header[43] <= ROOM_MAIL_RETRY_RECEIPTS
      && zero(header + 44, 4);
  uint32_t sum = valid ? crc(UINT32_C(0xffffffff), header, sizeof(header)) : 0;
  uint8_t policy[4];
  if (valid) valid = size_t(file.read(policy, 4)) == 4 && policy[0] <= 2 && policy[1] <= 1
      && policy[2] <= ROOM_MAIL_MAX_ALLOWED && policy[3] == 0;
  if (valid) {
    sum = crc(sum, policy, 4);
    if (out) { out->revision = get32(header + 36); out->count = header[42]; out->receipts = header[43];
      out->settings.mode = RoomMailMode(policy[0]); out->settings.mailbox_only = policy[1] != 0;
      out->settings.allowed_count = policy[2]; }
    if (status) {
      status->revision = get32(header + 36); status->count = header[42];
      status->settings.mode = RoomMailMode(policy[0]);
      status->settings.mailbox_only = policy[1] != 0;
      status->settings.allowed_count = policy[2];
    }
  }
  uint8_t previous[32] = {};
  for (uint8_t i = 0; valid && i < ROOM_MAIL_MAX_ALLOWED; ++i) {
    uint8_t key[32]; valid = size_t(file.read(key, 32)) == 32;
    if (!valid) break;
    sum = crc(sum, key, 32);
    valid = i < policy[2] ? nonzero(key) && (i == 0 || memcmp(previous, key, 32) < 0) : zero(key, 32);
    if (i < policy[2]) memcpy(previous, key, 32);
    if (out) memcpy(out->settings.allowed[i], key, 32);
    if (status) memcpy(status->settings.allowed[i], key, 32);
  }
  uint32_t previous_id = 0; uint8_t queued = 0;
  size_t body_length = 0, selected_position = 0, selected_length = 0;
  for (uint8_t i = 0; valid && i < ROOM_MAIL_RETRY_RECEIPTS; ++i) {
    uint8_t bytes[RECORD_SIZE]; valid = size_t(file.read(bytes, sizeof(bytes))) == sizeof(bytes);
    if (!valid) break;
    sum = crc(sum, bytes, sizeof(bytes));
    if (i >= header[43]) { valid = zero(bytes, sizeof(bytes)); continue; }
    const uint32_t id = get32(bytes + 40); const uint16_t length = get16(bytes + 48);
    valid = nonzero(bytes) && get64(bytes + 32) != 0 && id > previous_id
        && id <= get32(header + 36) && length != 0 && length <= ROOM_MAIL_MAX_BODY_LENGTH
        && bytes[50] >= Queued && bytes[50] <= Deleted && bytes[51] == 0 && zero(bytes + 84, 4);
    previous_id = id;
    if (out) {
      Record& record = out->records[i]; memcpy(record.message.sender, bytes, 32);
      record.message.request_id = get64(bytes + 32); record.message.id = id;
      record.message.created = get32(bytes + 44); record.message.length = length;
      record.state = bytes[50]; memcpy(record.digest, bytes + 52, 32);
      record.offset = uint16_t(BODY_START + body_length);
    }
    if (bytes[50] == Queued) {
      if (id == selected_id) { selected_position = body_length; selected_length = length; }
      body_length += length; ++queued;
    }
  }
  if (valid) valid = queued == entry.count && file.size() == BODY_START + body_length + 4;
  uint8_t chunk[STREAM_CHUNK];
  for (size_t position = 0; valid && position < body_length;) {
    const size_t remaining = body_length - position;
    const size_t length = remaining < sizeof(chunk) ? remaining : sizeof(chunk);
    valid = size_t(file.read(chunk, length)) == length;
    if (!valid) break;
    sum = crc(sum, chunk, length);
    const size_t begin = selected_position + offset;
    size_t wanted = selected_length > offset ? selected_length - offset : 0;
    if (wanted > capacity) wanted = capacity;
    if (output && selected_length && position + length > begin && position < begin + wanted) {
      const size_t start = position < begin ? begin - position : 0;
      size_t end = length; if (position + end > begin + wanted) end = begin + wanted - position;
      memcpy(output + position + start - begin, chunk + start, end - start);
    }
    position += length;
  }
  uint8_t trailer[4]; sum ^= UINT32_C(0xffffffff);
  if (valid) valid = size_t(file.read(trailer, 4)) == 4 && get32(trailer) == sum
      && (!expected_crc || sum == *expected_crc);
  file.close();
  if (!valid) {
    if (out) clearOwner(*out);
    if (status) clearStatus(*status);
    if (output) memset(output, 0, capacity);
  } else if (mailbox_only) *mailbox_only = policy[1] != 0;
  return valid;
}

template <typename File>
bool writeBytes(File& file, uint32_t& sum, const uint8_t* bytes, size_t length) {
  if (file.write(bytes, length) != length) return false;
  sum = crc(sum, bytes, length); return true;
}

template <typename Filesystem>
bool writeOwner(Filesystem* fs, const Entry& entry, const Owner& owner,
                bool had_owner, const Entry& previous,
                uint32_t& written_checksum,
                uint32_t new_id = 0, const char* new_body = nullptr) {
  char path[24]; ownerPath(entry, path);
  if (!removeArtifact(fs, path)) return false;
  auto file = openWrite(fs, path); if (!file) return false;
  uint8_t header[OWNER_HEADER_SIZE] = {};
  memcpy(header, OWNER_MAGIC, 4); memcpy(header + 4, entry.owner, 32);
  put32(header + 36, owner.revision); header[40] = entry.slot; header[41] = entry.bank;
  header[42] = owner.count; header[43] = owner.receipts;
  uint32_t sum = UINT32_C(0xffffffff);
  bool okay = !file.isDirectory() && writeBytes(file, sum, header, sizeof(header));
  uint8_t policy[4] = {uint8_t(owner.settings.mode), uint8_t(owner.settings.mailbox_only), owner.settings.allowed_count, 0};
  okay = okay && writeBytes(file, sum, policy, 4);
  for (uint8_t i = 0; okay && i < ROOM_MAIL_MAX_ALLOWED; ++i)
    okay = writeBytes(file, sum, owner.settings.allowed[i], 32);
  for (uint8_t i = 0; okay && i < ROOM_MAIL_RETRY_RECEIPTS; ++i) {
    uint8_t bytes[RECORD_SIZE]; encodeRecord(owner.records[i], bytes);
    okay = writeBytes(file, sum, bytes, sizeof(bytes));
  }
  // Adafruit LittleFS File requires its filesystem at construction. Obtain
  // the source through open(), including its valid filesystem-bound failure
  // value, rather than default-constructing an optional File handle.
  Entry source_entry = previous;
  if (!had_owner) source_entry.bank = 1 - entry.bank;
  char source_path[24]; ownerPath(source_entry, source_path);
  auto source = openRead(fs, source_path);
  if (!had_owner && source) source.close();
  size_t source_position = 0;
  uint8_t chunk[STREAM_CHUNK];
  for (uint8_t i = 0; okay && i < owner.receipts; ++i) {
    const Record& record = owner.records[i]; if (record.state != Queued) continue;
    if (record.message.id != new_id && (!had_owner || !source || source.isDirectory())) { okay = false; break; }
    while (okay && record.message.id != new_id && source_position < record.offset) {
      size_t length = record.offset - source_position; if (length > sizeof(chunk)) length = sizeof(chunk);
      okay = size_t(source.read(chunk, length)) == length; source_position += length;
    }
    for (size_t position = 0; okay && position < record.message.length;) {
      size_t length = record.message.length - position; if (length > sizeof(chunk)) length = sizeof(chunk);
      if (record.message.id == new_id) {
        if (!new_body) { okay = false; break; }
        memcpy(chunk, new_body + position, length);
      } else { okay = size_t(source.read(chunk, length)) == length; source_position += length; }
      okay = okay && writeBytes(file, sum, chunk, length); position += length;
    }
  }
  if (source) source.close();
  sum ^= UINT32_C(0xffffffff); uint8_t trailer[4]; put32(trailer, sum);
  okay = okay && file.write(trailer, 4) == 4; file.flush(); file.close();
  written_checksum = sum;
  return okay;
}

// Publish a streamed sorted registry pointing at an already verified bank.
// A failed rename can leave a verified backup; recover() restores it on retry.
template <typename Filesystem>
RoomMailResult publish(Filesystem* fs, const Index& prior, const Entry& entry, bool existed,
                       uint32_t revision) {
  if (!removeArtifact(fs, ROOM_MAIL_TEMP_PATH) || !removeArtifact(fs, ROOM_MAIL_BACKUP_PATH))
    return RoomMailResult::WriteFailure;
  auto file = openWrite(fs, ROOM_MAIL_TEMP_PATH); if (!file) return RoomMailResult::WriteFailure;
  uint8_t header[INDEX_HEADER_SIZE] = {};
  memcpy(header, INDEX_MAGIC, 4); put32(header + 4, revision);
  header[8] = prior.count + (existed ? 0 : 1);
  uint8_t old_count = 0;
  Entry ignored; Index checked; bool found = false;
  if (existed && !readIndex(fs, ROOM_MAIL_PRIMARY_PATH, checked, entry.owner, &ignored, &found)) {
    file.close(); return RoomMailResult::WriteFailure;
  }
  if (found) old_count = ignored.count;
  header[9] = prior.messages - old_count + entry.count;
  uint32_t sum = UINT32_C(0xffffffff);
  bool okay = !file.isDirectory() && writeBytes(file, sum, header, sizeof(header));
  auto source = openRead(fs, ROOM_MAIL_PRIMARY_PATH);
  if (prior.revision) {
    uint8_t skipped[INDEX_HEADER_SIZE];
    okay = okay && source && !source.isDirectory() && size_t(source.read(skipped, sizeof(skipped))) == sizeof(skipped);
  }
  bool emitted = false;
  for (uint8_t i = 0; okay && i < prior.count; ++i) {
    uint8_t bytes[INDEX_ENTRY_SIZE]; okay = size_t(source.read(bytes, sizeof(bytes))) == sizeof(bytes);
    if (!okay) break;
    const int comparison = memcmp(entry.owner, bytes, 32);
    if (!emitted && comparison <= 0) {
      uint8_t added[INDEX_ENTRY_SIZE]; encodeEntry(entry, added);
      okay = writeBytes(file, sum, added, sizeof(added)); emitted = true;
    }
    if (comparison != 0) okay = okay && writeBytes(file, sum, bytes, sizeof(bytes));
  }
  if (okay && !emitted) { uint8_t bytes[INDEX_ENTRY_SIZE]; encodeEntry(entry, bytes); okay = writeBytes(file, sum, bytes, sizeof(bytes)); }
  if (source) source.close();
  sum ^= UINT32_C(0xffffffff); uint8_t trailer[4]; put32(trailer, sum);
  okay = okay && file.write(trailer, 4) == 4; file.flush(); file.close();
  Index verify;
  if (!okay || !readIndex(fs, ROOM_MAIL_TEMP_PATH, verify, nullptr, nullptr, nullptr, &sum))
    return RoomMailResult::WriteFailure;
  bool primary;
  if (!filePresence(fs, ROOM_MAIL_PRIMARY_PATH, primary)
      || (primary && !fs->rename(ROOM_MAIL_PRIMARY_PATH, ROOM_MAIL_BACKUP_PATH)))
    return RoomMailResult::WriteFailure;
  if (!fs->rename(ROOM_MAIL_TEMP_PATH, ROOM_MAIL_PRIMARY_PATH)) {
    if (primary) fs->rename(ROOM_MAIL_BACKUP_PATH, ROOM_MAIL_PRIMARY_PATH);
    return RoomMailResult::WriteFailure;
  }
  // Registry rename is the commit. Housekeeping failure never undoes success.
  removeArtifact(fs, ROOM_MAIL_BACKUP_PATH);
  return RoomMailResult::Success;
}

template <typename Filesystem>
RoomMailResult commit(Filesystem* fs, Index& index, Entry& entry, Owner& owner,
                      bool existed, uint32_t new_id = 0, const char* new_body = nullptr) {
  if (index.revision == UINT32_MAX) return RoomMailResult::Unavailable;
  Entry previous = entry;
  owner.revision = index.revision + 1; entry.bank = existed ? 1 - entry.bank : 0; entry.count = owner.count;
  uint32_t checksum;
  if (!writeOwner(fs, entry, owner, existed, previous, checksum, new_id, new_body)
      || !readOwner(fs, entry, nullptr, 0, 0, nullptr, 0, &checksum))
    return RoomMailResult::WriteFailure;
  return publish(fs, index, entry, existed, owner.revision);
}
template <typename Filesystem>
RoomMailResult load(Filesystem* fs, const uint8_t* key, Index& index, Entry& entry, Owner& owner,
                    bool& found, bool cleanup = false) {
  if (!nonzero(key)) return RoomMailResult::Invalid;
  if (!recover(fs, index, key, entry, found, false)) return RoomMailResult::Unavailable;
  if (found && (!readOwner(fs, entry, &owner) || owner.revision > index.revision))
    return RoomMailResult::Unavailable;
  // Validate the selected committed bank before removing recovery evidence.
  if (cleanup && (!removeArtifact(fs, ROOM_MAIL_TEMP_PATH)
      || !removeArtifact(fs, ROOM_MAIL_BACKUP_PATH))) return RoomMailResult::Unavailable;
  if (!found) { clearOwner(owner); return RoomMailResult::NotFound; }
  return RoomMailResult::Success;
}
} // namespace room_mail_detail

template <typename Filesystem>
RoomMailResult getRoomMailStatus(Filesystem* fs, const uint8_t* owner, RoomMailStatus& status) {
  room_mail_detail::clearStatus(status); room_mail_detail::Index index; room_mail_detail::Entry entry;
  bool found;
  if (!room_mail_detail::nonzero(owner)) return RoomMailResult::Invalid;
  if (!room_mail_detail::recover(fs, index, owner, entry, found, false)) return RoomMailResult::Unavailable;
  if (!found) return RoomMailResult::NotFound;
  return room_mail_detail::readOwner(fs, entry, nullptr, 0, 0, nullptr, 0,
      nullptr, &status, nullptr, index.revision) ? RoomMailResult::Success : RoomMailResult::Unavailable;
}

template <typename Filesystem>
RoomMailResult getRoomMailboxOnly(Filesystem* fs, const uint8_t* owner, bool& value) {
  value = false; room_mail_detail::Index index; room_mail_detail::Entry entry; bool found;
  if (!room_mail_detail::nonzero(owner)) return RoomMailResult::Invalid;
  if (!room_mail_detail::recover(fs, index, owner, entry, found, false)) return RoomMailResult::Unavailable;
  if (!found) return RoomMailResult::Success;
  return room_mail_detail::readOwner(fs, entry, nullptr, 0, 0, nullptr, 0,
      nullptr, nullptr, &value, index.revision) ? RoomMailResult::Success : RoomMailResult::Unavailable;
}

template <typename Filesystem>
RoomMailResult saveRoomMailSettings(Filesystem* fs, const uint8_t* key,
                                    const RoomMailSettings& settings, uint32_t expected_revision) {
  using namespace room_mail_detail;
  if (!nonzero(key) || !validSettings(settings)) return RoomMailResult::Invalid;
  Index index; Entry entry; Owner state; bool found;
  const auto result = load(fs, key, index, entry, state, found, true);
  if (result != RoomMailResult::Success && result != RoomMailResult::NotFound) return result;
  if (expected_revision != state.revision) return RoomMailResult::StaleVersion;
  if (!found) {
    if (index.count >= ROOM_MAIL_MAX_OWNERS) return RoomMailResult::Full;
    memcpy(entry.owner, key, 32); entry.slot = 0;
    while (index.slots & (UINT32_C(1) << entry.slot)) ++entry.slot;
  }
  state.settings = settings;
  for (uint8_t i = settings.allowed_count; i < ROOM_MAIL_MAX_ALLOWED; ++i) memset(state.settings.allowed[i], 0, 32);
  for (uint8_t i = 1; i < settings.allowed_count; ++i)
    for (uint8_t j = i; j > 0 && memcmp(state.settings.allowed[j - 1], state.settings.allowed[j], 32) > 0; --j) {
      uint8_t swap[32]; memcpy(swap, state.settings.allowed[j - 1], 32);
      memcpy(state.settings.allowed[j - 1], state.settings.allowed[j], 32); memcpy(state.settings.allowed[j], swap, 32);
    }
  return commit(fs, index, entry, state, found);
}

// Convenience for local single-loop callers. Explicit revision arguments are
// always strict: revision zero can provision only a still-absent mailbox.
template <typename Filesystem>
RoomMailResult saveRoomMailSettings(Filesystem* fs, const uint8_t* key,
                                    const RoomMailSettings& settings) {
  RoomMailStatus status;
  const auto result = getRoomMailStatus(fs, key, status);
  if (result != RoomMailResult::Success && result != RoomMailResult::NotFound) return result;
  return saveRoomMailSettings(fs, key, settings, status.revision);
}

template <typename Filesystem>
RoomMailResult listRoomMail(Filesystem* fs, const uint8_t* key, uint32_t expected_revision,
                            size_t start, RoomMailMessage* output, size_t capacity, size_t& copied) {
  copied = 0;
  if (!output || !capacity || capacity > ROOM_MAIL_MAX_PER_OWNER) return RoomMailResult::Invalid;
  room_mail_detail::Index index; room_mail_detail::Entry entry; room_mail_detail::Owner state; bool found;
  const auto result = room_mail_detail::load(fs, key, index, entry, state, found);
  if (result != RoomMailResult::Success) return result;
  if (expected_revision && expected_revision != state.revision) return RoomMailResult::StaleVersion;
  if (start > state.count) return RoomMailResult::Invalid;
  size_t cursor = 0;
  for (uint8_t i = 0; i < state.receipts && copied < capacity; ++i)
    if (state.records[i].state == room_mail_detail::Queued && cursor++ >= start) output[copied++] = state.records[i].message;
  return RoomMailResult::Success;
}

template <typename Filesystem>
RoomMailResult readRoomMail(Filesystem* fs, const uint8_t* key, uint32_t id, size_t offset,
                            uint8_t* output, size_t capacity, size_t& copied, RoomMailMessage& message) {
  copied = 0; message = RoomMailMessage{};
  if (!id || !output || !capacity || capacity > ROOM_MAIL_READ_MAX_CHUNK) return RoomMailResult::Invalid;
  room_mail_detail::Index index; room_mail_detail::Entry entry; room_mail_detail::Owner state; bool found;
  const auto result = room_mail_detail::load(fs, key, index, entry, state, found);
  if (result != RoomMailResult::Success) return result;
  for (uint8_t i = 0; i < state.receipts; ++i) {
    const auto& record = state.records[i];
    if (record.message.id != id || record.state != room_mail_detail::Queued) continue;
    if (offset > record.message.length) return RoomMailResult::Invalid;
    if (!room_mail_detail::readOwner(fs, entry, nullptr, id, offset, output, capacity)) return RoomMailResult::Unavailable;
    message = record.message; copied = message.length - offset; if (copied > capacity) copied = capacity;
    return RoomMailResult::Success;
  }
  return RoomMailResult::NotFound;
}

// Up to eight accepted submission receipts remain: queued receipts never evict,
// and the oldest retired receipt is replaced when needed. Exact nonce/body
// retries return Duplicate across reboot and after
// ACK/delete. Reusing a retained nonce with different content returns Mismatch.
// An ancient retry after its retired receipt is evicted can enqueue again.
template <typename Filesystem>
RoomMailResult sendRoomMail(Filesystem* fs, const uint8_t* sender, const uint8_t* key,
                            uint64_t request_id, const char* body, size_t length,
                            uint32_t& id, uint32_t created = 0) {
  using namespace room_mail_detail;
  id = 0;
  if (!nonzero(sender) || !nonzero(key) || !request_id || !validBody(body, length)) return RoomMailResult::Invalid;
  Index index; Entry entry; Owner state; bool found;
  const auto result = load(fs, key, index, entry, state, found, true);
  if (result != RoomMailResult::Success) return result;
  if (!roomMailSenderAllowed(state.settings, key, sender)) return RoomMailResult::Forbidden;
  uint8_t digest[32]; bodyDigest(body, length, digest);
  for (uint8_t i = 0; i < state.receipts; ++i) {
    const auto& record = state.records[i];
    if (record.message.request_id != request_id || memcmp(record.message.sender, sender, 32)) continue;
    if (record.message.length != length || memcmp(record.digest, digest, 32)) return RoomMailResult::Mismatch;
    id = record.message.id; return RoomMailResult::Duplicate;
  }
  if (state.count >= ROOM_MAIL_MAX_PER_OWNER || index.messages >= ROOM_MAIL_MAX_MESSAGES) return RoomMailResult::Full;
  if (index.revision == UINT32_MAX) return RoomMailResult::Unavailable;
  if (state.receipts == ROOM_MAIL_RETRY_RECEIPTS) {
    uint8_t retired = 0; while (retired < state.receipts && state.records[retired].state == Queued) ++retired;
    if (retired == state.receipts) return RoomMailResult::Full;
    for (uint8_t i = retired; i + 1 < state.receipts; ++i) state.records[i] = state.records[i + 1];
    --state.receipts;
  }
  Record& added = state.records[state.receipts++]; added = Record{};
  added.message.id = index.revision + 1; added.message.request_id = request_id;
  added.message.created = created; added.message.length = uint16_t(length);
  memcpy(added.message.sender, sender, 32); memcpy(added.digest, digest, 32);
  added.state = Queued; ++state.count;
  const auto saved = commit(fs, index, entry, state, true, added.message.id, body);
  if (saved == RoomMailResult::Success) id = added.message.id;
  return saved;
}

namespace room_mail_detail {
template <typename Filesystem>
RoomMailResult retire(Filesystem* fs, const uint8_t* key, uint32_t id, uint8_t kind, bool purge) {
  Index index; Entry entry; Owner state; bool found;
  const auto result = load(fs, key, index, entry, state, found, true);
  if (result != RoomMailResult::Success) return result;
  bool known = false, changed = false;
  for (uint8_t i = 0; i < state.receipts; ++i) {
    auto& record = state.records[i];
    if (!purge && record.message.id != id) continue;
    known = true;
    if (record.state == Queued) { record.state = kind; --state.count; changed = true; }
  }
  if (!known && !purge) return RoomMailResult::NotFound;
  if (!changed) return RoomMailResult::Success;
  return commit(fs, index, entry, state, true);
}
} // namespace room_mail_detail

template <typename Filesystem>
RoomMailResult acknowledgeRoomMail(Filesystem* fs, const uint8_t* owner, uint32_t id) {
  if (!id) return RoomMailResult::Invalid;
  return room_mail_detail::retire(fs, owner, id, room_mail_detail::Acknowledged, false);
}
template <typename Filesystem>
RoomMailResult deleteRoomMail(Filesystem* fs, const uint8_t* owner, uint32_t id) {
  return room_mail_detail::retire(fs, owner, id, room_mail_detail::Deleted, id == 0);
}

template <typename Filesystem>
RoomMailResult getRoomMailStats(Filesystem* fs, RoomMailStats& stats) {
  stats = RoomMailStats{}; room_mail_detail::Index index; room_mail_detail::Entry entry; bool found;
  if (!room_mail_detail::recover(fs, index, nullptr, entry, found, false)) return RoomMailResult::Unavailable;
  stats.revision = index.revision; stats.owners = index.count; stats.messages = index.messages;
  return RoomMailResult::Success;
}

// Administrator-only owner enumeration. Caller must authorize this explicitly.
// Public mailbox reads never disclose the registry or another owner's policy.
template <typename Filesystem>
RoomMailResult listRoomMailOwners(Filesystem* fs, uint32_t expected_revision, size_t start,
                                  uint8_t (*keys)[32], size_t capacity, size_t& copied,
                                  RoomMailStats& stats) {
  copied = 0;
  if (!keys || !capacity || capacity > ROOM_MAIL_MAX_OWNERS) return RoomMailResult::Invalid;
  const auto result = getRoomMailStats(fs, stats);
  if (result != RoomMailResult::Success) return result;
  if (expected_revision && expected_revision != stats.revision) return RoomMailResult::StaleVersion;
  if (start > stats.owners) return RoomMailResult::Invalid;
  if (!stats.owners) return RoomMailResult::Success;
  auto file = room_mail_detail::openRead(fs, ROOM_MAIL_PRIMARY_PATH); if (!file) return RoomMailResult::Unavailable;
  uint8_t header[room_mail_detail::INDEX_HEADER_SIZE];
  bool okay = size_t(file.read(header, sizeof(header))) == sizeof(header);
  for (size_t i = 0; okay && i < stats.owners && copied < capacity; ++i) {
    uint8_t bytes[room_mail_detail::INDEX_ENTRY_SIZE]; okay = size_t(file.read(bytes, sizeof(bytes))) == sizeof(bytes);
    if (okay && i >= start) memcpy(keys[copied++], bytes, 32);
  }
  file.close();
  if (!okay) { copied = 0; return RoomMailResult::Unavailable; }
  return RoomMailResult::Success;
}
} // namespace mesh
