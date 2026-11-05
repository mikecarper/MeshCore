#pragma once

#include <stdint.h>
#include <string.h>

namespace mesh { namespace wifi {

// Values are represented in quarter-dBm units; zero means use the default
// power for the current modem sleep mode.
inline bool parseTxPowerValue(const char* input, uint8_t& result) {
  if (!input) return false;
  while (*input == ' ' || *input == '\t') ++input;
  if (strncmp(input, "auto", 4) == 0) {
    input += 4;
    while (*input == ' ' || *input == '\t') ++input;
    if (*input) return false;
    result = 0;
    return true;
  }
  if (*input < '0' || *input > '9') return false;
  unsigned whole = 0;
  while (*input >= '0' && *input <= '9') {
    whole = whole * 10 + unsigned(*input++ - '0');
    if (whole > 21) return false;
  }
  unsigned fraction = 0;
  if (*input == '.') {
    ++input;
    if (*input < '0' || *input > '9') return false;
    fraction = unsigned(*input++ - '0') * 10;
    if (*input >= '0' && *input <= '9') fraction += unsigned(*input++ - '0');
    if (*input >= '0' && *input <= '9') return false;
  }
  while (*input == ' ' || *input == '\t') ++input;
  if (*input || (fraction != 0 && fraction != 25 && fraction != 50 && fraction != 75)) return false;
  const unsigned quarters = whole * 4 + fraction / 25;
  if (quarters < 8 || quarters > 84) return false;
  result = uint8_t(quarters);
  return true;
}

}} // namespace mesh::wifi
