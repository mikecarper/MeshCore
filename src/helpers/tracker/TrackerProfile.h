#pragma once

#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include "../FilePresence.h"
#include "../TrackerRoutePolicy.h"

namespace mesh { namespace tracker { namespace profile {

// This is an explicit user choice, not an inference from phone proximity.
enum class Mode : uint8_t { Backyard = 0, Hiking = 1 };

struct Record {
  Mode mode = Mode::Backyard;
  uint32_t generation = 0;
};

enum class StoreResult : uint8_t { Success, NotFound, Invalid, Unavailable, Conflict };
enum class CommandResult : uint8_t { Unhandled, Query, Changed, Unchanged, Error };

inline bool isValid(Mode mode) {
  return mode == Mode::Backyard || mode == Mode::Hiking;
}

inline const char* name(Mode mode) {
  return mode == Mode::Backyard ? "backyard" : mode == Mode::Hiking ? "hiking" : "invalid";
}

// Invalid storage must not be interpreted as an authorized button transition.
inline bool toggle(Mode& mode) {
  if (!isValid(mode)) return false;
  mode = mode == Mode::Backyard ? Mode::Hiking : Mode::Backyard;
  return true;
}

// Mutates a candidate only. The caller must publish changes to durable
// storage before applying the chosen operating profile to the live tracker.
inline CommandResult parseCommand(const char* command, Record& candidate,
                                  char* reply, size_t capacity) {
  if (!command || !reply || !capacity) return CommandResult::Unhandled;
  if (!strcmp(command, "get tracker.profile")) {
    snprintf(reply, capacity, "profile=%s", name(candidate.mode));
    return CommandResult::Query;
  }
  static const char prefix[] = "set tracker.profile ";
  if (!strncmp(command, prefix, sizeof(prefix) - 1)) {
    const char* value = command + sizeof(prefix) - 1;
    Mode requested;
    if (!strcmp(value, "backyard")) requested = Mode::Backyard;
    else if (!strcmp(value, "hiking")) requested = Mode::Hiking;
    else {
      snprintf(reply, capacity, "ERR: profile must be backyard or hiking");
      return CommandResult::Error;
    }
    if (!isValid(candidate.mode)) {
      snprintf(reply, capacity, "ERR: profile storage unavailable");
      return CommandResult::Error;
    }
    if (candidate.mode == requested) {
      snprintf(reply, capacity, "OK - profile %s", name(requested));
      return CommandResult::Unchanged;
    }
    candidate.mode = requested;
    snprintf(reply, capacity, "OK - profile %s", name(requested));
    return CommandResult::Changed;
  }
  if (!strcmp(command, "set tracker.profile") ||
      !strncmp(command, "get tracker.profile ", 20)) {
    snprintf(reply, capacity, "ERR: profile must be backyard or hiking");
    return CommandResult::Error;
  }
  return CommandResult::Unhandled;
}

namespace store_detail {
static constexpr size_t kSize = 16;
static const char kBank0[] = "/trprofile.0";
static const char kBank1[] = "/trprofile.1";
static const char kTemp[] = "/trprofile.tmp";

inline bool encode(const Record& record, uint8_t image[kSize]) {
  if (!image || !record.generation || !isValid(record.mode)) return false;
  memset(image, 0, kSize);
  image[0] = 'T'; image[1] = 'P'; image[2] = 'R'; image[3] = 1;
  route_detail::put32(image + 4, record.generation);
  image[8] = static_cast<uint8_t>(record.mode);
  route_detail::put32(image + 12, route_detail::crc(image, 12));
  return true;
}

inline bool decode(const uint8_t image[kSize], Record& out) {
  if (!image || image[0] != 'T' || image[1] != 'P' || image[2] != 'R'
      || image[3] != 1 || !route_detail::get32(image + 4)
      || image[8] > 1 || image[9] || image[10] || image[11]
      || route_detail::get32(image + 12) != route_detail::crc(image, 12))
    return false;
  Record decoded;
  decoded.generation = route_detail::get32(image + 4);
  decoded.mode = static_cast<Mode>(image[8]);
  out = decoded;
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
  out = Record();
  active = 0xff;
  bool present[2];
  if (!fs || !filePresence(fs, kBank0, present[0]) || !filePresence(fs, kBank1, present[1]))
    return StoreResult::Unavailable;
  if (!present[0] && !present[1]) return StoreResult::NotFound;
  const char* paths[] = {kBank0, kBank1};
  for (uint8_t bank = 0; bank < 2; ++bank) {
    if (!present[bank]) continue;
    Record item;
    if (!readImage(fs, paths[bank], item) || ((item.generation - 1) & 1) != bank) {
      out = Record();
      return StoreResult::Unavailable;
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

// Single application-loop writer on durable storage; generation provides
// compare-and-swap and prevents stale edits from replacing a newer choice.
template <typename FS>
StoreResult save(FS* fs, Record& record) {
  if (!fs) return StoreResult::Unavailable;
  if (!isValid(record.mode)) return StoreResult::Invalid;
  Record current;
  uint8_t active;
  const StoreResult loaded = store_detail::inspect(fs, current, active);
  if (loaded != StoreResult::Success && loaded != StoreResult::NotFound) return loaded;
  if (record.generation != current.generation) return StoreResult::Conflict;
  if (current.generation == UINT32_MAX) return StoreResult::Unavailable;
  Record proposed = record;
  ++proposed.generation;
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
  file.flush();
  file.close();
  if (!written || !store_detail::readImage(fs, store_detail::kTemp, current, image)) {
    store_detail::removeArtifact(fs, store_detail::kTemp);
    return StoreResult::Unavailable;
  }
  const char* dest = ((proposed.generation - 1) & 1) ? store_detail::kBank1 : store_detail::kBank0;
  if (!store_detail::removeArtifact(fs, dest) || !fs->rename(store_detail::kTemp, dest)) {
    store_detail::removeArtifact(fs, store_detail::kTemp);
    return StoreResult::Unavailable;
  }
  if (!store_detail::readImage(fs, dest, current, image)) return StoreResult::Unavailable;
  record.generation = proposed.generation;
  return StoreResult::Success;
}

}}} // namespace mesh::tracker::profile
