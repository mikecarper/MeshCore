#pragma once

#include <stddef.h>
#include <stdint.h>

namespace mesh {
namespace discovery {

static constexpr uint8_t NODE_RESPONSES = 1;
static constexpr uint8_t REGION_RESPONSES = 2;
static constexpr uint8_t DEFAULT_RESPONSES = NODE_RESPONSES | REGION_RESPONSES;
static constexpr size_t PREFS_SIZE = 8;
static constexpr size_t COMMON_LEGACY_SIZE = 877;
static constexpr size_t LEGACY_RS232_TAIL_SIZE = 10;

inline uint16_t checksum(const uint8_t* bytes, size_t length) {
  uint16_t crc = 0xffff;
  for (size_t i = 0; i < length; ++i) {
    crc ^= static_cast<uint16_t>(bytes[i]) << 8;
    for (unsigned bit = 0; bit < 8; ++bit)
      crc = static_cast<uint16_t>((crc & 0x8000) ? (crc << 1) ^ 0x1021 : crc << 1);
  }
  return crc;
}

// The record is omitted when both response types are enabled. Keep it before
// existing platform footers, whose readers locate those policies at EOF.
inline void encodePrefs(uint8_t* out, uint8_t mask) {
  out[0] = 'D'; out[1] = 'R'; out[2] = 'P'; out[3] = 1;
  out[4] = mask <= DEFAULT_RESPONSES ? mask : DEFAULT_RESPONSES;
  out[5] = 0;
  const uint16_t crc = checksum(out, 6);
  out[6] = static_cast<uint8_t>(crc >> 8);
  out[7] = static_cast<uint8_t>(crc);
}

inline bool decodePrefs(const uint8_t* in, size_t size, uint8_t& mask) {
  mask = DEFAULT_RESPONSES;
  if (!in || size != PREFS_SIZE || in[0] != 'D' || in[1] != 'R'
      || in[2] != 'P' || in[3] != 1 || in[4] > DEFAULT_RESPONSES || in[5] != 0
      || checksum(in, 6) != static_cast<uint16_t>((in[6] << 8) | in[7]))
    return false;
  mask = in[4];
  return true;
}

template <typename File>
uint8_t readPrefs(File& file, size_t* next_record = nullptr) {
  uint8_t mask = DEFAULT_RESPONSES;
  uint8_t bytes[PREFS_SIZE] = {};
  const size_t size = file.size();
  size_t offset = COMMON_LEGACY_SIZE;
  if (next_record) *next_record = offset;
  if (size < offset + PREFS_SIZE || !file.seek(offset)
      || file.read(bytes, sizeof(bytes)) != sizeof(bytes))
    return mask;
  // The source image decides whether a UART power tail exists. Depending on
  // this build's bridge flags would lose saved settings across role changes.
  if (bytes[0] == 'R' && bytes[1] == 'S' && bytes[2] == 'P' && bytes[3] == 1) {
    offset += LEGACY_RS232_TAIL_SIZE;
    if (next_record) *next_record = offset;
    if (size < offset + PREFS_SIZE || !file.seek(offset)
        || file.read(bytes, sizeof(bytes)) != sizeof(bytes))
      return mask;
  }
  if (decodePrefs(bytes, sizeof(bytes), mask) && next_record) *next_record = offset + PREFS_SIZE;
  return mask;
}

} // namespace discovery
} // namespace mesh
