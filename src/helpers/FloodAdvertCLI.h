#pragma once

#include "FloodAdvertLimiter.h"
#include <Utils.h>

namespace mesh {
namespace cli {

// Dispatch only after the role's ordinary CLI authorization checks. Requiring
// an explicit "all" avoids treating missing/malformed keys as a global reset.
inline bool handleFloodAdvertClear(FloodAdvertLimiter* limiter, const char* command, char* reply) {
  static const char prefix[] = "clear flood.advert";
  const size_t prefix_len = sizeof(prefix) - 1;
  if (strncmp(command, prefix, prefix_len) != 0) return false;
  const char* arg = command + prefix_len;
  if (*arg && *arg != ' ' && *arg != '\t') return false;
  while (*arg == ' ' || *arg == '\t') ++arg;
  const char* end = arg;
  while (*end && *end != ' ' && *end != '\t' && *end != '\r' && *end != '\n') ++end;
  const size_t len = end - arg;
  while (*end == ' ' || *end == '\t' || *end == '\r' || *end == '\n') ++end;
  uint8_t key[PUB_KEY_SIZE];
  const bool all = len == 3 && strncmp(arg, "all", 3) == 0;
  bool valid = all;
  if (len == PUB_KEY_SIZE * 2) {
    char hex[PUB_KEY_SIZE * 2 + 1];
    memcpy(hex, arg, len);
    hex[len] = 0;
    valid = Utils::fromHex(key, PUB_KEY_SIZE, hex);
  }
  if (*end || !valid) {
    strcpy(reply, "ERR: clear flood.advert all|<64-hex-key>");
  } else if (!limiter) {
    strcpy(reply, "ERR: advert limiter unavailable on this role");
  } else {
    if (all) limiter->reset();
    else limiter->clear(key);
    strcpy(reply, all ? "OK - all advert limit history cleared" : "OK - key advert limit history cleared");
  }
  return true;
}

} // namespace cli
} // namespace mesh
