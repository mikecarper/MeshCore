#pragma once

#include <stdint.h>

namespace mesh {
namespace cli {

// Mobile keyboards commonly capitalize the first word entered into a CLI
// field. Command verbs are identifiers, so normalize only that first token and
// leave every argument (names, passwords, keys, and other values) untouched.
inline void normalizeCommandVerb(char* command) {
  if (command == nullptr) return;

  while (*command == ' ' || *command == '\t') command++;
  while (*command != 0 && *command != ' ' && *command != '\t') {
    if (*command >= 'A' && *command <= 'Z') {
      *command = static_cast<char>(*command - 'A' + 'a');
    }
    command++;
  }
}

// Parse the decimal syntax used by CLI settings without pulling a general
// strtod implementation into small firmware images. Scientific notation,
// NaN, infinity, and trailing junk are intentionally rejected.
inline bool parseDecimalStrict(const char* text, float& result) {
  if (text == nullptr) return false;
  while (*text == ' ' || *text == '\t') text++;

  bool negative = false;
  if (*text == '-' || *text == '+') {
    negative = *text == '-';
    text++;
  }

  uint32_t whole = 0;
  bool saw_digit = false;
  while (*text >= '0' && *text <= '9') {
    uint8_t digit = static_cast<uint8_t>(*text++ - '0');
    if (whole > (0xFFFFFFFFUL - digit) / 10UL) return false;
    whole = whole * 10UL + digit;
    saw_digit = true;
  }

  float value = static_cast<float>(whole);
  if (*text == '.') {
    float place = 0.1f;
    text++;
    while (*text >= '0' && *text <= '9') {
      value += static_cast<float>(*text++ - '0') * place;
      place *= 0.1f;
      saw_digit = true;
    }
  }

  while (*text == ' ' || *text == '\t') text++;
  if (!saw_digit || *text != 0) return false;
  result = negative ? -value : value;
  return true;
}

}  // namespace cli
}  // namespace mesh
