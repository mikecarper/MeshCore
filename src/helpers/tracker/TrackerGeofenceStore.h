#pragma once

#include <stdint.h>
#include <string.h>
#include "TrackerGeofence.h"
#include "../FilePresence.h"
#include "../TrackerRoutePolicy.h"

namespace mesh { namespace tracker { namespace geofence {

// Separate from TrackerRecord: upgrades do not change the tracker wire or
// storage ABI. Last-known state is written only when it changes.
struct Record {
  Box box;
  Result last_known = Result::Unknown;
  bool previous_box = false;
  uint32_t generation = 0;
};
enum class StoreResult : uint8_t { Success, NotFound, Invalid, Unavailable, Conflict };

inline bool isValidRecord(const Record& record) {
  if (record.last_known != Result::Unknown && record.last_known != Result::Inside
      && record.last_known != Result::Outside) return false;
  if (record.previous_box && (!record.box.configured || record.last_known != Result::Outside)) return false;
  if (record.box.configured) return isValid(record.box);
  return record.last_known == Result::Unknown && !record.box.south && !record.box.north
      && !record.box.west && !record.box.east;
}

namespace store_detail {
static constexpr size_t kSize = 40;
static const char kBank0[] = "/geofence.0";
static const char kBank1[] = "/geofence.1";
static const char kTemp[] = "/geofence.tmp";

inline bool encode(const Record& record, uint8_t image[kSize]) {
  if (!image || !record.generation || !isValidRecord(record)) return false;
  memset(image, 0, kSize);
  image[0] = 'G'; image[1] = 'F'; image[2] = 'N'; image[3] = 1;
  route_detail::put32(image + 4, record.generation);
  image[8] = record.box.configured ? 1 : 0;
  image[9] = static_cast<uint8_t>(record.last_known);
  image[10] = record.previous_box ? 1 : 0;
  route_detail::put32(image + 12, static_cast<uint32_t>(record.box.south));
  route_detail::put32(image + 16, static_cast<uint32_t>(record.box.north));
  route_detail::put32(image + 20, static_cast<uint32_t>(record.box.west));
  route_detail::put32(image + 24, static_cast<uint32_t>(record.box.east));
  route_detail::put32(image + 36, route_detail::crc(image, 36));
  return true;
}

inline bool decode(const uint8_t image[kSize], Record& out) {
  if (!image || image[0] != 'G' || image[1] != 'F' || image[2] != 'N' || image[3] != 1
      || !route_detail::get32(image + 4) || image[8] > 1 || image[9] > 2
      || image[10] > 1 || image[11] || route_detail::get32(image + 28)
      || route_detail::get32(image + 32)
      || route_detail::get32(image + 36) != route_detail::crc(image, 36)) return false;
  Record candidate;
  candidate.generation = route_detail::get32(image + 4);
  candidate.box.configured = image[8] != 0;
  candidate.last_known = static_cast<Result>(image[9]);
  candidate.previous_box = image[10] != 0;
  candidate.box.south = static_cast<int32_t>(route_detail::get32(image + 12));
  candidate.box.north = static_cast<int32_t>(route_detail::get32(image + 16));
  candidate.box.west = static_cast<int32_t>(route_detail::get32(image + 20));
  candidate.box.east = static_cast<int32_t>(route_detail::get32(image + 24));
  if (!isValidRecord(candidate)) return false;
  out = candidate;
  return true;
}

template <typename FS>
bool readImage(FS* fs, const char* path, Record& out, const uint8_t* expected = nullptr) {
#if defined(RP2040_PLATFORM)
  auto file = fs->open(path, "r");
#else
  auto file = fs->open(path);
#endif
  if (!file) return false;
  uint8_t image[kSize];
  bool okay = !file.isDirectory() && file.size() == sizeof(image);
  if (okay) okay = static_cast<size_t>(file.read(image, sizeof(image))) == sizeof(image);
  file.close();
  return okay && (!expected || memcmp(image, expected, sizeof(image)) == 0) && decode(image, out);
}

template <typename FS>
StoreResult inspect(FS* fs, Record& out, uint8_t& active) {
  out = Record(); active = 0xff;
  bool present[2];
  if (!fs || !filePresence(fs, kBank0, present[0]) || !filePresence(fs, kBank1, present[1]))
    return StoreResult::Unavailable;
  if (!present[0] && !present[1]) return StoreResult::NotFound;
  const char* paths[] = { kBank0, kBank1 };
  for (uint8_t bank = 0; bank < 2; ++bank) {
    if (!present[bank]) continue;
    Record item;
    if (!readImage(fs, paths[bank], item) || ((item.generation - 1) & 1) != bank) {
      out = Record(); return StoreResult::Unavailable;
    }
    if (active != 0xff) {
      const uint32_t high = item.generation > out.generation ? item.generation : out.generation;
      const uint32_t low = item.generation < out.generation ? item.generation : out.generation;
      if (high - low != 1) { out = Record(); return StoreResult::Unavailable; }
    }
    if (active == 0xff || item.generation > out.generation) { out = item; active = bank; }
  }
  return StoreResult::Success;
}

template <typename FS>
bool removeArtifact(FS* fs, const char* path) {
  bool present;
  if (!filePresence(fs, path, present)) return false;
  if (!present) return true;
  fs->remove(path);
  return filePresence(fs, path, present) && !present;
}
} // namespace store_detail

template <typename FS>
StoreResult load(FS* fs, Record& record) {
  uint8_t active;
  return store_detail::inspect(fs, record, active);
}

template <typename FS>
StoreResult save(FS* fs, Record& record) {
  if (!fs) return StoreResult::Unavailable;
  if (!isValidRecord(record)) return StoreResult::Invalid;
  Record current;
  uint8_t active;
  const StoreResult loaded = store_detail::inspect(fs, current, active);
  if (loaded != StoreResult::Success && loaded != StoreResult::NotFound) return loaded;
  if (record.generation != current.generation) return StoreResult::Conflict;
  if (current.generation == UINT32_MAX) return StoreResult::Unavailable;
  Record proposed = record;
  proposed.generation++;
  uint8_t image[store_detail::kSize];
  if (!store_detail::encode(proposed, image)
      || !store_detail::removeArtifact(fs, store_detail::kTemp)) return StoreResult::Unavailable;
#if defined(NRF52_PLATFORM) || defined(STM32_PLATFORM)
  auto file = fs->open(store_detail::kTemp, FILE_O_WRITE);
#elif defined(ESP32_PLATFORM)
  auto file = fs->open(store_detail::kTemp, "w", true);
#else
  auto file = fs->open(store_detail::kTemp, "w");
#endif
  if (!file) return StoreResult::Unavailable;
  if (file.isDirectory()) {
    file.close(); store_detail::removeArtifact(fs, store_detail::kTemp); return StoreResult::Unavailable;
  }
  const bool written = file.write(image, sizeof(image)) == sizeof(image);
  file.flush(); file.close();
  if (!written || !store_detail::readImage(fs, store_detail::kTemp, current, image)) {
    store_detail::removeArtifact(fs, store_detail::kTemp); return StoreResult::Unavailable;
  }
  const char* dest = ((proposed.generation - 1) & 1) ? store_detail::kBank1 : store_detail::kBank0;
  if (!store_detail::removeArtifact(fs, dest) || !fs->rename(store_detail::kTemp, dest)) {
    store_detail::removeArtifact(fs, store_detail::kTemp); return StoreResult::Unavailable;
  }
  if (!store_detail::readImage(fs, dest, current, image)) return StoreResult::Unavailable;
  record.generation = proposed.generation;
  return StoreResult::Success;
}

}}} // namespace mesh::tracker::geofence
