#pragma once

#include "CompanionTrafficPrefs.h"
#include <stdint.h>
#include <string.h>

namespace mesh { namespace companion { namespace timezone {

// Optional footer after all existing preference fields and PMO/TXR footers.
// Integer-only images retain their exact published bytes and lengths.
static constexpr size_t FooterSize = 10;

inline bool valid(float hours) {
  // Ordered comparisons also reject NaN and either infinity.
  return hours >= -12.0f && hours <= 14.0f;
}

inline bool fractional(float hours) {
  return valid(hours) && hours != static_cast<int8_t>(hours);
}

inline void encodeFooter(uint8_t out[FooterSize], float hours) {
  static_assert(sizeof(float) == sizeof(uint32_t), "32-bit timezone float");
  out[0] = 'T'; out[1] = 'Z'; out[2] = 'F'; out[3] = 1;
  uint32_t bits;
  memcpy(&bits, &hours, sizeof(bits));
  for (unsigned i = 0; i < 4; ++i) out[4 + i] = uint8_t(bits >> (8 * i));
  const uint16_t crc = traffic::checksum(out, 8);
  out[8] = uint8_t(crc >> 8); out[9] = uint8_t(crc);
}

inline bool decodeFooter(const uint8_t* in, size_t size, float& hours) {
  if (!in || size != FooterSize || in[0] != 'T' || in[1] != 'Z'
      || in[2] != 'F' || in[3] != 1
      || traffic::checksum(in, 8) != uint16_t((in[8] << 8) | in[9]))
    return false;
  uint32_t bits = 0;
  for (unsigned i = 0; i < 4; ++i) bits |= uint32_t(in[4 + i]) << (8 * i);
  float decoded;
  memcpy(&decoded, &bits, sizeof(decoded));
  if (!valid(decoded)) return false;
  hours = decoded;
  return true;
}

} } }  // namespace mesh::companion::timezone
