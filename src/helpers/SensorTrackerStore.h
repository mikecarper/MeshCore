#pragma once

#include <stddef.h>
#include <stdint.h>
#include <string.h>
#include "FilePresence.h"
#include "TrackerRoutePolicy.h"

namespace mesh {
namespace tracker {

static constexpr size_t kPathCapacity = 64;
static constexpr uint8_t kUnknownPath = 0xff;
static constexpr uint32_t kReplyWindowSeconds = 20;
static constexpr uint32_t kGpsAcquireSeconds = 120;
static constexpr size_t kTrackerImageSize = 160;
static const char kTrackerBank0Path[] = "/tracker.0";
static const char kTrackerBank1Path[] = "/tracker.1";
static const char kTrackerTempPath[] = "/tracker.tmp";

struct TrackerRecord {
  bool enabled = false;
  uint8_t owner[32] = {};
  uint32_t normal_interval = 1800;
  uint32_t lost_interval = 60;
  RouteState route;
  uint8_t path_len = kUnknownPath;
  uint8_t path[kPathCapacity] = {};
  uint8_t last_lost = 0; // 0 unknown, 1 no, 2 yes
  uint32_t last_request_tag = 0; // Persisted nonce floor, bounded near RTC time.
  uint32_t generation = 0;
};

enum class StoreResult : uint8_t { Success, NotFound, Invalid, Unavailable, Conflict };

inline size_t trackerPathByteLength(uint8_t encoded_length) {
  return static_cast<size_t>(encoded_length & 63) * ((encoded_length >> 6) + 1);
}

inline bool isValidTrackerRecord(const TrackerRecord& record) {
  uint8_t owner_bits = 0;
  for (size_t index = 0; index < sizeof(record.owner); ++index) owner_bits |= record.owner[index];
  if (!owner_bits || record.normal_interval < 60 || record.normal_interval > 86400
      || record.lost_interval < 60 || record.lost_interval > 86400
      || record.last_lost > 2 || !isValidRouteState(record.route)) return false;
  if (record.last_request_tag && (!record.route.latest_observed
      || uint64_t(record.last_request_tag) > uint64_t(record.route.latest_observed) + 300)) return false;
  // Match Packet::isValidPathLen: four-byte hashes are reserved, not supported.
  return record.path_len == kUnknownPath || ((record.path_len >> 6) != 3
      && trackerPathByteLength(record.path_len) <= kPathCapacity);
}

namespace tracker_store_detail {
inline void failClosed(TrackerRecord& record) {
  record = TrackerRecord();
  record.route = route_detail::failClosed();
}

inline bool encode(const TrackerRecord& record, uint8_t* image) {
  if (!image || !record.generation || !isValidTrackerRecord(record)) return false;
  memset(image, 0, kTrackerImageSize);
  image[0] = 'S'; image[1] = 'T'; image[2] = 'R'; image[3] = 1;
  route_detail::put32(image + 4, record.generation);
  image[8] = record.enabled ? 1 : 0;
  image[9] = record.last_lost;
  image[10] = record.path_len;
  route_detail::put32(image + 12, record.normal_interval);
  route_detail::put32(image + 16, record.lost_interval);
  memcpy(image + 20, record.owner, sizeof(record.owner));
  if (!encodeRouteState(record.route, image + 52, kRouteStateImageSize)) return false;
  if (record.path_len != kUnknownPath) memcpy(image + 84, record.path, trackerPathByteLength(record.path_len));
  route_detail::put32(image + 148, record.last_request_tag);
  route_detail::put32(image + 156, route_detail::crc(image, 156));
  return true;
}

inline bool decode(const uint8_t* image, TrackerRecord& record) {
  if (!image || image[0] != 'S' || image[1] != 'T' || image[2] != 'R' || image[3] != 1
      || !route_detail::get32(image + 4) || image[8] > 1 || image[11]
      || route_detail::get32(image + 156) != route_detail::crc(image, 156)) return false;
  TrackerRecord decoded;
  decoded.generation = route_detail::get32(image + 4);
  decoded.enabled = image[8] != 0;
  decoded.last_lost = image[9];
  decoded.path_len = image[10];
  decoded.normal_interval = route_detail::get32(image + 12);
  decoded.lost_interval = route_detail::get32(image + 16);
  decoded.last_request_tag = route_detail::get32(image + 148);
  memcpy(decoded.owner, image + 20, sizeof(decoded.owner));
  if (!decodeRouteState(image + 52, kRouteStateImageSize, decoded.route)
      || !isValidTrackerRecord(decoded)) return false;
  const size_t path_bytes = decoded.path_len == kUnknownPath ? 0 : trackerPathByteLength(decoded.path_len);
  memcpy(decoded.path, image + 84, path_bytes);
  for (size_t index = 84 + path_bytes; index < 148; ++index) if (image[index]) return false;
  for (size_t index = 152; index < 156; ++index) if (image[index]) return false;
  record = decoded;
  return true;
}

template <typename Filesystem>
bool readImage(Filesystem* fs, const char* path, TrackerRecord& record,
               const uint8_t* expected = nullptr) {
#if defined(RP2040_PLATFORM)
  auto file = fs->open(path, "r");
#else
  auto file = fs->open(path);
#endif
  if (!file) return false;
  uint8_t image[kTrackerImageSize];
  bool okay = !file.isDirectory() && file.size() == sizeof(image);
  if (okay) okay = static_cast<size_t>(file.read(image, sizeof(image))) == sizeof(image);
  file.close();
  return okay && (!expected || memcmp(image, expected, sizeof(image)) == 0) && decode(image, record);
}

// A bad present bank is never treated as an absent store or replaced. Temps
// are uncommitted and ignored. A power cut during publication leaves the older
// active bank intact; the inactive bank is removed only after verifying temp.
template <typename Filesystem>
StoreResult inspect(Filesystem* fs, TrackerRecord& record, uint8_t& active_bank) {
  record = TrackerRecord();
  active_bank = 0xff;
  bool present[2];
  if (!fs || !filePresence(fs, kTrackerBank0Path, present[0])
      || !filePresence(fs, kTrackerBank1Path, present[1])) {
    failClosed(record);
    return StoreResult::Unavailable;
  }
  if (!present[0] && !present[1]) return StoreResult::NotFound;
  const char* paths[2] = { kTrackerBank0Path, kTrackerBank1Path };
  for (uint8_t bank = 0; bank < 2; ++bank) {
    if (!present[bank]) continue;
    TrackerRecord read;
    if (!readImage(fs, paths[bank], read) || ((read.generation - 1) & 1) != bank) {
      failClosed(record);
      return StoreResult::Unavailable;
    }
    if (active_bank != 0xff) {
      const uint32_t high = read.generation > record.generation ? read.generation : record.generation;
      const uint32_t low = read.generation < record.generation ? read.generation : record.generation;
      if (high - low != 1) {
        failClosed(record);
        return StoreResult::Unavailable;
      }
    }
    if (read.generation > record.generation) {
      record = read;
      active_bank = bank;
    }
  }
  return StoreResult::Success;
}

template <typename Filesystem>
bool removeArtifact(Filesystem* fs, const char* path) {
  bool present;
  if (!filePresence(fs, path, present)) return false;
  if (!present) return true;
  fs->remove(path);
  return filePresence(fs, path, present) && !present;
}
} // namespace tracker_store_detail

template <typename Filesystem>
StoreResult loadTracker(Filesystem* fs, TrackerRecord& record) {
  uint8_t bank;
  return tracker_store_detail::inspect(fs, record, bank);
}

// Single writer, main-loop owned, durable filesystems only. generation is a
// compare-and-swap token: refresh after a conflict or ambiguous failed save.
// No radio transmission is permitted unless its reservation save succeeds.
template <typename Filesystem>
StoreResult saveTracker(Filesystem* fs, TrackerRecord& record) {
  if (!fs) return StoreResult::Unavailable;
  if (!isValidTrackerRecord(record)) return StoreResult::Invalid;
  TrackerRecord current;
  uint8_t bank;
  const StoreResult loaded = tracker_store_detail::inspect(fs, current, bank);
  if (loaded != StoreResult::Success && loaded != StoreResult::NotFound) return loaded;
  if (record.generation != current.generation) return StoreResult::Conflict;
  if (record.last_request_tag < current.last_request_tag) return StoreResult::Invalid;
  if (current.generation == UINT32_MAX) return StoreResult::Unavailable;
  TrackerRecord proposed = record;
  proposed.generation = current.generation + 1;
  uint8_t image[kTrackerImageSize];
  if (!tracker_store_detail::encode(proposed, image)
      || !tracker_store_detail::removeArtifact(fs, kTrackerTempPath)) return StoreResult::Unavailable;
#if defined(NRF52_PLATFORM) || defined(STM32_PLATFORM)
  auto file = fs->open(kTrackerTempPath, FILE_O_WRITE);
#elif defined(ESP32_PLATFORM)
  auto file = fs->open(kTrackerTempPath, "w", true);
#else
  auto file = fs->open(kTrackerTempPath, "w");
#endif
  if (!file) return StoreResult::Unavailable;
  if (file.isDirectory()) {
    file.close();
    tracker_store_detail::removeArtifact(fs, kTrackerTempPath);
    return StoreResult::Unavailable;
  }
  const bool written = file.write(image, sizeof(image)) == sizeof(image);
  file.flush();
  file.close();
  if (!written || !tracker_store_detail::readImage(fs, kTrackerTempPath, current, image)) {
    tracker_store_detail::removeArtifact(fs, kTrackerTempPath);
    return StoreResult::Unavailable;
  }
  const char* destination = ((proposed.generation - 1) & 1) ? kTrackerBank1Path : kTrackerBank0Path;
  if (!tracker_store_detail::removeArtifact(fs, destination)
      || !fs->rename(kTrackerTempPath, destination)) {
    tracker_store_detail::removeArtifact(fs, kTrackerTempPath);
    return StoreResult::Unavailable;
  }
  if (!tracker_store_detail::readImage(fs, destination, current, image)) return StoreResult::Unavailable;
  record.generation = proposed.generation;
  return StoreResult::Success;
}

} // namespace tracker
} // namespace mesh
