#pragma once

#include <stddef.h>
#include <stdint.h>

// A deliberately retained, versioned *defaults* image, not a runtime override.
// Browser tools may edit this one block and repair the containing image's
// checksums. Existing preferences remain authoritative on ordinary updates.
namespace mesh {
namespace browserRadioDefaults {

constexpr size_t ImageSize = 64;
struct Settings {
  uint32_t frequencyHz;
  uint32_t bandwidthHz;
  uint8_t sf;
  uint8_t cr;
  int8_t txPowerDbm;
  uint16_t preamble;
  bool txOverride;
};

inline uint16_t read16(const uint8_t* p) {
  return uint16_t(p[0]) | (uint16_t(p[1]) << 8);
}
inline uint32_t read32(const uint8_t* p) {
  return uint32_t(p[0]) | (uint32_t(p[1]) << 8)
      | (uint32_t(p[2]) << 16) | (uint32_t(p[3]) << 24);
}
inline uint32_t checksum(const uint8_t* p, size_t length) {
  uint32_t crc = 0xffffffffU;
  for (size_t n = 0; n < length; ++n) {
    crc ^= p[n];
    for (unsigned bit = 0; bit < 8; ++bit)
      crc = (crc >> 1) ^ ((crc & 1) ? 0xedb88320U : 0);
  }
  return crc ^ 0xffffffffU;
}

inline bool decode(const uint8_t* p, size_t length, Settings& result) {
  if (!p || length != ImageSize) return false;
  // Ordered volatile byte comparisons prevent LTO/vectorization from pooling
  // a second contiguous magic in target literal data (or replacing this with
  // memcmp). Only the retained flash record contains that complete prefix.
  const volatile uint8_t* magic = p;
  if (magic[0] != 0x4d || magic[1] != 0x43 || magic[2] != 0x52
      || magic[3] != 0x44 || magic[4] != 0x45 || magic[5] != 0x46
      || magic[6] != 0x41 || magic[7] != 0x55 || magic[8] != 0x4c
      || magic[9] != 0x54 || magic[10] != 0x53 || magic[11] != 0x30
      || magic[12] != 0x30 || magic[13] != 0x30 || magic[14] != 0x31
      || magic[15] != 0 || read16(p + 16) != 1
      || read16(p + 18) != ImageSize || read32(p + 60) != checksum(p, 60))
    return false;
  const uint32_t flags = read32(p + 20);
  if ((flags & ~3U) || !(flags & 1U)) return false;
  if (p[35]) return false;
  for (size_t n = 38; n < 60; ++n) if (p[n]) return false;
  Settings next;
  next.frequencyHz = read32(p + 24);
  next.bandwidthHz = read32(p + 28);
  next.sf = p[32]; next.cr = p[33];
  next.txPowerDbm = p[34] < 128 ? int8_t(p[34]) : int8_t(int16_t(p[34]) - 256);
  next.preamble = read16(p + 36);
  next.txOverride = (flags & 2U) != 0;
  if (next.frequencyHz < 150000000U || next.frequencyHz > 2500000000U
      || next.sf < 5 || next.sf > 12 || next.cr < 5 || next.cr > 8
      || (next.preamble && (next.preamble < 8 || next.preamble > 65528))
      || (!next.txOverride && p[34])
      || (next.txOverride && (next.txPowerDbm < -9 || next.txPowerDbm > 40)))
    return false;
  switch (next.bandwidthHz) {
    case 7800: case 10400: case 15600: case 20800: case 31250:
    case 41700: case 62500: case 125000: case 250000: case 500000: break;
    default: return false;
  }
  result = next;
  return true;
}

extern const uint8_t image[ImageSize];
bool read(Settings& result);

// Existing, corrupt, staged or unreadable settings are never a fresh install.
// Use metadata probes rather than a failed open as proof that files are absent.
template <typename Presence>
bool freshStore(Presence presence) {
  static const char* const paths[] = {
    "/new_prefs", "/new_prefs.bak", "/new_prefs.tmp", "/node_prefs",
    "/node_prefs.bak", "/com_prefs", "/com_prefs.bak", "/com_prefs.tmp",
    "/prefs.json", "/radio_profiles", "/radio_profiles.bak",
    "/radio_profiles.tmp", "/radio_profiles.reply"
  };
  for (const char* path : paths) {
    bool present = true;
    if (!presence(path, present) || present) return false;
  }
  return true;
}

}  // namespace browserRadioDefaults
}  // namespace mesh
