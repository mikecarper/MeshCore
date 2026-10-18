#pragma once

#include "CompanionTrafficPrefs.h"
#include <float.h>
#include <stdint.h>
#include <string.h>

namespace mesh { namespace companion { namespace timezone {

// Optional footer after all existing preference fields and PMO/TXR footers.
// Integer-only images retain their exact published bytes and lengths.
static constexpr size_t FooterSize = 10;

// All supported preference ABIs use IEEE binary32. Checking its bits avoids
// duplicated soft-float comparisons on small ARM targets and remains strict
// when a board's compiler enables finite-math optimizations.
#if defined(__GNUC__)
__attribute__((noinline))
#endif
inline bool valid(float hours) {
  static_assert(sizeof(float) == sizeof(uint32_t) && FLT_RADIX == 2
      && FLT_MANT_DIG == 24 && FLT_MAX_EXP == 128, "IEEE binary32 timezone");
  uint32_t bits;
  memcpy(&bits, &hours, sizeof(bits));
  // Positive binary32 bits are ordered numerically. For negative values,
  // their magnitude is ordered likewise above the sign bit. The thresholds
  // are exactly -12 and +14; either sign of NaN/infinity exceeds its limit.
  return bits <= ((bits & 0x80000000UL) ? 0xc1400000UL : 0x41600000UL);
}

inline bool fractional(float hours) {
  return valid(hours) && hours != static_cast<int8_t>(hours);
}

inline void encodeFooter(uint8_t out[FooterSize], float hours) {
  static_assert(sizeof(float) == sizeof(uint32_t), "32-bit timezone float");
  out[0] = 'T'; out[1] = 'Z'; out[2] = 'F'; out[3] = 1;
#if defined(__BYTE_ORDER__) && __BYTE_ORDER__ == __ORDER_LITTLE_ENDIAN__
  memcpy(out + 4, &hours, sizeof(hours));
#else
  uint32_t bits;
  memcpy(&bits, &hours, sizeof(bits));
  for (unsigned i = 0; i < 4; ++i) out[4 + i] = uint8_t(bits >> (8 * i));
#endif
  const uint16_t crc = traffic::checksum(out, 8);
  out[8] = uint8_t(crc >> 8); out[9] = uint8_t(crc);
}

inline bool decodeFooter(const uint8_t* in, size_t size, float& hours) {
  if (!in || size != FooterSize) return false;
#if defined(__BYTE_ORDER__) && __BYTE_ORDER__ == __ORDER_LITTLE_ENDIAN__
  uint32_t magic;
  memcpy(&magic, in, sizeof(magic));
  if (magic != 0x01465a54UL) return false;  // TZF, version 1
#else
  if (memcmp(in, "TZF\1", 4) != 0) return false;
#endif
  if (traffic::checksum(in, 8) != uint16_t((in[8] << 8) | in[9])) return false;
  float decoded;
#if defined(__BYTE_ORDER__) && __BYTE_ORDER__ == __ORDER_LITTLE_ENDIAN__
  memcpy(&decoded, in + 4, sizeof(decoded));
#else
  uint32_t bits = 0;
  for (unsigned i = 0; i < 4; ++i) bits |= uint32_t(in[4 + i]) << (8 * i);
  memcpy(&decoded, &bits, sizeof(decoded));
#endif
  if (!valid(decoded)) return false;
  hours = decoded;
  return true;
}

} } }  // namespace mesh::companion::timezone
