#pragma once
#include <RadioProfiles.h>
#include <helpers/CLICommandUtils.h>

namespace mesh { namespace cli {
inline bool parseRadioPreamble(const char* text, uint16_t& result) {
  uint32_t value;
  if (!strcmp(text, "auto")) { result = 0; return true; }
  if (!parseUnsignedIntegerStrict(text, value) || value > RadioProfiles::MaxPreamble
      || (value != 0 && value < 8)) return false;
  result = (uint16_t)value;
  return true;
}
inline bool parseRadioPreambleSuffix(const char* input, unsigned fields, char* legacy,
                                     size_t capacity, uint16_t& preamble) {
  preamble = 0;
  if (!input || strlen(input) >= capacity) return false;
  strcpy(legacy, input);
  unsigned count = 1;
  char* last = nullptr;
  for (char* p = legacy; *p; ++p) if (*p == ',') { ++count; last = p; }
  if (count == fields) return true;
  if (count != fields + 1 || !last || !parseRadioPreamble(last + 1, preamble)) return false;
  *last = 0;
  return true;
}
} }
