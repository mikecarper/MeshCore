#pragma once

#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include "../FilePresence.h"

namespace mesh {
namespace findmy {

// Only the OpenHaystack public advertising key is stored on the node. The
// private key and report-fetching account credentials remain with the owner.
struct Config {
  bool enabled = false;
  bool has_key = false;
  uint8_t key[28] = {};
  uint32_t generation = 0;
};

enum class CommandResult : uint8_t { Unhandled, Query, Changed, Error };
enum class StoreResult : uint8_t { Success, NotFound, Invalid, Unavailable, Conflict };

inline bool keyHasBits(const uint8_t* key) {
  uint8_t bits = 0;
  for (size_t i = 0; i < 28; ++i) bits |= key[i];
  return bits != 0;
}

inline int base64Digit(char c) {
  if (c >= 'A' && c <= 'Z') return c - 'A';
  if (c >= 'a' && c <= 'z') return c - 'a' + 26;
  if (c >= '0' && c <= '9') return c - '0' + 52;
  if (c == '+') return 62;
  if (c == '/') return 63;
  return -1;
}

// A 28-byte key is encoded as 40 Base64 characters with exactly two padding
// characters. Reject alternate encodings and nonzero unused bits.
inline bool parsePublicKey(const char* text, uint8_t output[28]) {
  if (!text || !output || strlen(text) != 40 || text[38] != '=' || text[39] != '=') return false;
  uint8_t decoded[28];
  for (size_t block = 0; block < 9; ++block) {
    int a = base64Digit(text[block * 4]);
    int b = base64Digit(text[block * 4 + 1]);
    int c = base64Digit(text[block * 4 + 2]);
    int d = base64Digit(text[block * 4 + 3]);
    if (a < 0 || b < 0 || c < 0 || d < 0) return false;
    decoded[block * 3] = uint8_t((a << 2) | (b >> 4));
    decoded[block * 3 + 1] = uint8_t((b << 4) | (c >> 2));
    decoded[block * 3 + 2] = uint8_t((c << 6) | d);
  }
  const int a = base64Digit(text[36]), b = base64Digit(text[37]);
  if (a < 0 || b < 0 || (b & 15)) return false;
  decoded[27] = uint8_t((a << 2) | (b >> 4));
  if (!keyHasBits(decoded)) return false;
  memcpy(output, decoded, sizeof(decoded));
  return true;
}

inline bool isValidConfig(const Config& config) {
  const bool bits = keyHasBits(config.key);
  return config.has_key == bits && (!config.enabled || config.has_key);
}

// The caller applies changes only after durable save succeeds. No runtime
// state is changed by a malformed command or an invalid key.
inline CommandResult parseCommand(const char* command, Config& candidate,
                                  char* reply, size_t capacity) {
  if (!command || !reply || !capacity) return CommandResult::Unhandled;
  if (!strcmp(command, "get findmy")) {
    snprintf(reply, capacity, "findmy=%s key=%s", candidate.enabled ? "on" : "off",
             candidate.has_key ? "set" : "unset");
    return CommandResult::Query;
  }
  if (!strcmp(command, "set findmy on")) {
    if (!candidate.has_key) {
      snprintf(reply, capacity, "ERR: set findmy.key first");
      return CommandResult::Error;
    }
    candidate.enabled = true;
  } else if (!strcmp(command, "set findmy off")) {
    candidate.enabled = false;
  } else if (!strcmp(command, "set findmy.key clear")) {
    candidate.enabled = false;
    candidate.has_key = false;
    memset(candidate.key, 0, sizeof(candidate.key));
  } else if (!strncmp(command, "set findmy.key ", 15)) {
    uint8_t key[28];
    if (!parsePublicKey(command + 15, key)) {
      snprintf(reply, capacity, "ERR: expected 28-byte public key in Base64");
      return CommandResult::Error;
    }
    memcpy(candidate.key, key, sizeof(key));
    candidate.has_key = true;
  } else if (!strncmp(command, "get findmy", 10) || !strncmp(command, "set findmy", 10)) {
    snprintf(reply, capacity, "ERR: unknown findmy setting");
    return CommandResult::Error;
  } else {
    return CommandResult::Unhandled;
  }
  snprintf(reply, capacity, "OK");
  return CommandResult::Changed;
}

namespace detail {
static constexpr size_t kImageSize = 44;
static const char kBank0[] = "/findmy.0";
static const char kBank1[] = "/findmy.1";
static const char kTemp[] = "/findmy.tmp";

inline uint32_t get32(const uint8_t* b) {
  return uint32_t(b[0]) | (uint32_t(b[1]) << 8) | (uint32_t(b[2]) << 16) | (uint32_t(b[3]) << 24);
}
inline void put32(uint8_t* b, uint32_t n) {
  for (unsigned i = 0; i < 4; ++i) b[i] = uint8_t(n >> (i * 8));
}
inline uint32_t crc32(const uint8_t* data, size_t size) {
  uint32_t crc = 0xffffffffu;
  while (size--) {
    crc ^= *data++;
    for (int i = 0; i < 8; ++i) crc = (crc >> 1) ^ (0xedb88320u & (0u - (crc & 1u)));
  }
  return ~crc;
}
inline bool encode(const Config& config, uint8_t* image) {
  if (!image || !config.generation || !isValidConfig(config)) return false;
  memset(image, 0, kImageSize);
  image[0] = 'F'; image[1] = 'M'; image[2] = 'Y'; image[3] = 1;
  put32(image + 4, config.generation);
  image[8] = config.enabled ? 1 : 0;
  image[9] = config.has_key ? 1 : 0;
  memcpy(image + 12, config.key, 28);
  put32(image + 40, crc32(image, 40));
  return true;
}
inline bool decode(const uint8_t* image, Config& out) {
  if (!image || image[0] != 'F' || image[1] != 'M' || image[2] != 'Y' || image[3] != 1
      || !get32(image + 4) || image[8] > 1 || image[9] > 1 || image[10] || image[11]
      || get32(image + 40) != crc32(image, 40)) return false;
  Config candidate;
  candidate.generation = get32(image + 4);
  candidate.enabled = image[8] != 0;
  candidate.has_key = image[9] != 0;
  memcpy(candidate.key, image + 12, 28);
  if (!isValidConfig(candidate)) return false;
  out = candidate;
  return true;
}
template <typename FS>
bool readImage(FS* fs, const char* path, Config& out, const uint8_t* expected = nullptr) {
#if defined(RP2040_PLATFORM)
  auto file = fs->open(path, "r");
#else
  auto file = fs->open(path);
#endif
  if (!file) return false;
  uint8_t image[kImageSize];
  bool okay = !file.isDirectory() && file.size() == sizeof(image);
  if (okay) okay = static_cast<size_t>(file.read(image, sizeof(image))) == sizeof(image);
  file.close();
  return okay && (!expected || memcmp(image, expected, sizeof(image)) == 0) && decode(image, out);
}
template <typename FS>
StoreResult inspect(FS* fs, Config& out, uint8_t& active) {
  out = Config();
  active = 0xff;
  bool present[2];
  if (!fs || !filePresence(fs, kBank0, present[0]) || !filePresence(fs, kBank1, present[1]))
    return StoreResult::Unavailable;
  if (!present[0] && !present[1]) return StoreResult::NotFound;
  const char* paths[] = {kBank0, kBank1};
  for (uint8_t bank = 0; bank < 2; ++bank) {
    if (!present[bank]) continue;
    Config read;
    if (!readImage(fs, paths[bank], read) || ((read.generation - 1) & 1) != bank) {
      out = Config(); return StoreResult::Unavailable;
    }
    if (active != 0xff) {
      const uint32_t hi = read.generation > out.generation ? read.generation : out.generation;
      const uint32_t lo = read.generation < out.generation ? read.generation : out.generation;
      if (hi - lo != 1) { out = Config(); return StoreResult::Unavailable; }
    }
    if (active == 0xff || read.generation > out.generation) { out = read; active = bank; }
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
} // namespace detail

template <typename FS>
StoreResult loadConfig(FS* fs, Config& config) {
  uint8_t active;
  return detail::inspect(fs, config, active);
}

// One main-loop writer only, on a durable filesystem. A generation is a
// compare-and-swap token. Interrupted writes cannot publish a partial image.
template <typename FS>
StoreResult saveConfig(FS* fs, Config& config) {
  if (!fs) return StoreResult::Unavailable;
  if (!isValidConfig(config)) return StoreResult::Invalid;
  Config current;
  uint8_t active;
  const StoreResult loaded = detail::inspect(fs, current, active);
  if (loaded != StoreResult::Success && loaded != StoreResult::NotFound) return loaded;
  if (config.generation != current.generation) return StoreResult::Conflict;
  if (current.generation == UINT32_MAX) return StoreResult::Unavailable;
  Config proposed = config;
  proposed.generation++;
  uint8_t image[detail::kImageSize];
  if (!detail::encode(proposed, image) || !detail::removeArtifact(fs, detail::kTemp)) return StoreResult::Unavailable;
#if defined(NRF52_PLATFORM) || defined(STM32_PLATFORM)
  auto file = fs->open(detail::kTemp, FILE_O_WRITE);
#elif defined(ESP32_PLATFORM)
  auto file = fs->open(detail::kTemp, "w", true);
#else
  auto file = fs->open(detail::kTemp, "w");
#endif
  if (!file) return StoreResult::Unavailable;
  if (file.isDirectory()) { file.close(); detail::removeArtifact(fs, detail::kTemp); return StoreResult::Unavailable; }
  const bool written = file.write(image, sizeof(image)) == sizeof(image);
  file.flush(); file.close();
  if (!written || !detail::readImage(fs, detail::kTemp, current, image)) {
    detail::removeArtifact(fs, detail::kTemp); return StoreResult::Unavailable;
  }
  const char* destination = ((proposed.generation - 1) & 1) ? detail::kBank1 : detail::kBank0;
  if (!detail::removeArtifact(fs, destination) || !fs->rename(detail::kTemp, destination)) {
    detail::removeArtifact(fs, detail::kTemp); return StoreResult::Unavailable;
  }
  if (!detail::readImage(fs, destination, current, image)) return StoreResult::Unavailable;
  config.generation = proposed.generation;
  return StoreResult::Success;
}

} // namespace findmy
} // namespace mesh
