#pragma once

#include <RadioTxPolicy.h>
#include <stddef.h>
#include <stdint.h>

namespace mesh { namespace companion { namespace traffic {

// A checked append-only preference footer. The existing preference image and
// any board-specific footer retain their published positions.
static constexpr size_t FooterSize = 8;

inline uint16_t checksum(const uint8_t* bytes, size_t size) {
  uint16_t crc = 0xffff;
  for (size_t i = 0; i < size; ++i) {
    crc ^= static_cast<uint16_t>(bytes[i]) << 8;
    for (unsigned bit = 0; bit < 8; ++bit)
      crc = (crc & 0x8000) ? static_cast<uint16_t>((crc << 1) ^ 0x1021)
                           : static_cast<uint16_t>(crc << 1);
  }
  return crc;
}

inline void encodeFooter(uint8_t out[FooterSize], uint8_t media, uint8_t ssh) {
  out[0] = 'T'; out[1] = 'X'; out[2] = 'R'; out[3] = 1;
  out[4] = encodeRadioTxPolicy(media);
  out[5] = encodeRadioTxPolicy(ssh);
  const uint16_t crc = checksum(out, 6);
  out[6] = static_cast<uint8_t>(crc >> 8);
  out[7] = static_cast<uint8_t>(crc);
}

inline bool validPolicyByte(uint8_t value) {
  return value == 0 || (value >= 0xa1 && value <= 0xa4);
}

inline bool decodeFooter(const uint8_t* in, size_t size,
                         uint8_t& media, uint8_t& ssh) {
  if (!in || size != FooterSize || in[0] != 'T' || in[1] != 'X'
      || in[2] != 'R' || in[3] != 1
      || !validPolicyByte(in[4]) || !validPolicyByte(in[5])
      || checksum(in, 6) != static_cast<uint16_t>((in[6] << 8) | in[7]))
    return false;
  media = decodeRadioTxPolicy(in[4]);
  ssh = decodeRadioTxPolicy(in[5]);
  return true;
}

} } }  // namespace mesh::companion::traffic
