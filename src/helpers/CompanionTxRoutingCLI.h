#pragma once

#include <RadioTxPolicy.h>
#include <stddef.h>
#include <stdlib.h>

namespace mesh { namespace companion {

struct TxRouteCommand {
  bool matched = false;
  bool valid = false;
  bool channel = false;
  bool set = false;
  bool quoted = false;
  char target[70] = {};
  uint8_t policy = RADIO_TX_AUTO;
};

inline bool txRouteSpace(char c) { return c == ' ' || c == '\t'; }

inline TxRouteCommand parseTxRouteCommand(const char* text) {
  TxRouteCommand result;
  if (!text) return result;
  if (!strncmp(text, "get ", 4)) result.set = false;
  else if (!strncmp(text, "set ", 4)) result.set = true;
  else return result;
  text += 4;
  const char* family = nullptr;
  if (!strncmp(text, "tx.user", 7)) family = "tx.user";
  else if (!strncmp(text, "tx.channel", 10)) { family = "tx.channel"; result.channel = true; }
  else return result;
  text += strlen(family);
  if (*text && !txRouteSpace(*text)) return result;
  result.matched = true;
  while (txRouteSpace(*text)) ++text;
  const char* end = text + strlen(text);
  while (end > text && txRouteSpace(end[-1])) --end;
  if (result.set) {
    const char* mode = end;
    while (mode > text && !txRouteSpace(mode[-1])) --mode;
    char value[8];
    const size_t length = end - mode;
    if (mode == text || length >= sizeof(value)) return result;
    memcpy(value, mode, length); value[length] = 0;
    if (!parseRadioTxPolicy(value, result.policy)) return result;
    end = mode;
    while (end > text && txRouteSpace(end[-1])) --end;
  }
  if (end > text && *text == '"') {
    if (end - text < 2 || end[-1] != '"') return result;
    result.quoted = true;
    ++text; --end;
  }
  const size_t length = end - text;
  if (length >= sizeof(result.target) || ((result.set || result.quoted) && !length)) return result;
  if (memchr(text, '"', length) || memchr(text, '\r', length) || memchr(text, '\n', length)) return result;
  memcpy(result.target, text, length);
  result.target[length] = 0;
  result.valid = true;
  return result;
}

// Explicit key: avoids confusing a contact named like hexadecimal with a key.
inline bool parseTxRouteKey(const char* text, uint8_t key[32], size_t& bytes) {
  const size_t length = strlen(text);
  if (length < 12 || length > 64 || (length & 1)) return false;
  bytes = length / 2;
  for (size_t i = 0; i < bytes; ++i) {
    uint8_t value = 0;
    for (unsigned n = 0; n < 2; ++n) {
      char c = text[2 * i + n];
      if (c >= 'A' && c <= 'F') c += 'a' - 'A';
      if (c >= '0' && c <= '9') value = (value << 4) | (c - '0');
      else if (c >= 'a' && c <= 'f') value = (value << 4) | (c - 'a' + 10);
      else return false;
    }
    key[i] = value;
  }
  return true;
}

} }  // namespace mesh::companion
