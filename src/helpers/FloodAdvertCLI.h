#pragma once

#include "FloodAdvertLimiter.h"
#include <Utils.h>
#include <stdio.h>

namespace mesh {
namespace cli {

inline const char* floodAdvertLimitReason(uint8_t reasons) {
  if (reasons & FloodAdvertLimiter::BadListRule) return "bad";
  if (reasons & FloodAdvertLimiter::ReceiveHistory) return "history";
  return "quota";
}

// Keep pages inside the 160-byte reply shared by USB and authenticated LoRa CLI.
// Row numbers refer to the current filtered list, not permanent table slots.
inline bool handleFloodAdvertGet(FloodAdvertLimiter* limiter, const char* command,
                                 char* reply, uint32_t now) {
  static const char prefix[] = "get flood.advert";
  const size_t prefix_len = sizeof(prefix) - 1;
  if (strncmp(command, prefix, prefix_len) != 0) return false;
  const char* arg = command + prefix_len;
  if (*arg && *arg != ' ' && *arg != '\t' && *arg != '\r' && *arg != '\n') return false;
  while (*arg == ' ' || *arg == '\t') ++arg;
  const bool detail = strncmp(arg, "key", 3) == 0 && (arg[3] == ' ' || arg[3] == '\t');
  if (detail) {
    arg += 3;
    while (*arg == ' ' || *arg == '\t') ++arg;
  }
  unsigned number = 1;
  if (*arg >= '0' && *arg <= '9') {
    number = 0;
    do {
      number = number * 10 + unsigned(*arg++ - '0');
      if (number > 65535) break;
    } while (*arg >= '0' && *arg <= '9');
  } else if (detail) {
    number = 0;
  }
  while (*arg == ' ' || *arg == '\t' || *arg == '\r' || *arg == '\n') ++arg;
  if (*arg || number == 0 || number > 65535) {
    strcpy(reply, "ERR: get flood.advert [page] | key <index>");
    return true;
  }
  if (!limiter) {
    strcpy(reply, "ERR: advert limiter unavailable on this role");
    return true;
  }

  static constexpr size_t PAGE_SIZE = 3;
  FloodAdvertLimiter::LimitedEntry rows[PAGE_SIZE];
  const size_t offset = detail ? number - 1 : (number - 1) * PAGE_SIZE;
  const size_t total = limiter->listLimited(now, offset, rows, detail ? 1 : PAGE_SIZE);
  if (total == 0 && !detail && number == 1) {
    strcpy(reply, "> no rate-limited adverts");
  } else if (offset >= total) {
    strcpy(reply, "ERR: advert list index out of range");
  } else if (detail) {
    const auto& row = rows[0];
    char key[PUB_KEY_SIZE * 2 + 1];
    Utils::toHex(key, row.key, PUB_KEY_SIZE);
    snprintf(reply, 160, "> %s\n%s wait=%lus sent=%u/%u hops=%u recovery=%lus",
             key, floodAdvertLimitReason(row.reasons), (unsigned long)((row.wait_ms + 999) / 1000),
             unsigned(row.forwarded), unsigned(row.quota), unsigned(row.hops),
             (unsigned long)((row.recovery_ms + 999) / 1000));
  } else {
    size_t used = snprintf(reply, 160, "> page %u/%u limited=%u", number,
                           unsigned((total + PAGE_SIZE - 1) / PAGE_SIZE), unsigned(total));
    for (size_t i = 0; i < PAGE_SIZE && offset + i < total && used < 159; ++i) {
      char key[FloodAdvertLimiter::PREFIX_BYTES * 2 + 1];
      Utils::toHex(key, rows[i].key, FloodAdvertLimiter::PREFIX_BYTES);
      used += snprintf(reply + used, 160 - used, "\n%u %s %s wait=%lus",
                       unsigned(offset + i + 1), key, floodAdvertLimitReason(rows[i].reasons),
                       (unsigned long)((rows[i].wait_ms + 999) / 1000));
    }
  }
  return true;
}

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
