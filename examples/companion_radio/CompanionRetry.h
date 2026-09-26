#pragma once

#include <cstdio>
#include <cstring>
#include <cstdint>

#include <helpers/CLICommandUtils.h>
#include "NodePrefs.h"

namespace mesh { namespace companion {

// Companion keeps the historical core retry budget by default. These are
// local Companion controls; repeater presets and bridge prefix filters have
// different forwarding semantics and are intentionally not exposed here.
template <typename SavePrefs, typename CancelFloodRetries>
bool handleRetryCommand(CompanionNodePrefs& prefs, const char* command,
                        char* reply, size_t reply_size,
                        SavePrefs save_prefs,
                        CancelFloodRetries cancel_flood_retries) {
  if (command == nullptr || reply == nullptr || reply_size == 0) return false;

  const bool get = strncmp(command, "get ", 4) == 0;
  const bool set = strncmp(command, "set ", 4) == 0;
  if (!get && !set) return false;
  const char* key = command + 4;
  const char* value = set ? strchr(key, ' ') : nullptr;
  const size_t key_len = value == nullptr ? strlen(key) : static_cast<size_t>(value - key);
  const auto matches = [key, key_len](const char* name) {
    return strlen(name) == key_len && strncmp(key, name, key_len) == 0;
  };

  enum Field { Count, Path, GroupPath, Advert };
  Field field;
  if (matches("flood.retry.count")) field = Count;
  else if (matches("flood.retry.path")) field = Path;
  else if (matches("flood.retry.group.path")) field = GroupPath;
  else if (matches("flood.retry.advert")) field = Advert;
  else return false;

  if (get) {
    if (field == Count) {
      snprintf(reply, reply_size, "> %u", (unsigned)prefs.flood_retry_attempts);
    } else if (field == Advert) {
      snprintf(reply, reply_size, "> %s", prefs.flood_retry_advert_enabled ? "on" : "off");
    } else {
      const uint8_t gate = field == Path
          ? prefs.flood_retry_max_path : prefs.flood_retry_group_max_path;
      if (gate == 0xFF) snprintf(reply, reply_size, "> off");
      else snprintf(reply, reply_size, "> %u", (unsigned)gate);
    }
    return true;
  }

  const char* input = value == nullptr ? "" : value + 1;
  const uint8_t old_count = prefs.flood_retry_attempts;
  const uint8_t old_path = prefs.flood_retry_max_path;
  const uint8_t old_group_path = prefs.flood_retry_group_max_path;
  const uint8_t old_advert = prefs.flood_retry_advert_enabled;
  uint32_t parsed = 0;
  bool valid = true;

  if (field == Advert) {
    if (strcmp(input, "on") == 0) prefs.flood_retry_advert_enabled = 1;
    else if (strcmp(input, "off") == 0) prefs.flood_retry_advert_enabled = 0;
    else valid = false;
  } else if (field == Count) {
    valid = cli::parseUnsignedIntegerStrict(input, parsed) && parsed <= 15;
    if (valid) prefs.flood_retry_attempts = static_cast<uint8_t>(parsed);
  } else {
    if (strcmp(input, "off") == 0) parsed = 0xFF;
    else valid = cli::parseUnsignedIntegerStrict(input, parsed) && parsed <= 63;
    if (valid) {
      if (field == Path) prefs.flood_retry_max_path = static_cast<uint8_t>(parsed);
      else prefs.flood_retry_group_max_path = static_cast<uint8_t>(parsed);
    }
  }

  if (!valid) {
    const char* range = field == Count ? "0-15"
        : field == Advert ? "on|off" : "0-63|off";
    snprintf(reply, reply_size, "Error: use set %.*s <%s>",
             (int)key_len, key, range);
    return true;
  }
  if (!save_prefs()) {
    prefs.flood_retry_attempts = old_count;
    prefs.flood_retry_max_path = old_path;
    prefs.flood_retry_group_max_path = old_group_path;
    prefs.flood_retry_advert_enabled = old_advert;
    snprintf(reply, reply_size, "Error: retry setting could not be saved");
    return true;
  }
  if (old_count != 0 && prefs.flood_retry_attempts == 0) cancel_flood_retries();
  snprintf(reply, reply_size, "OK");
  return true;
}

} }  // namespace mesh::companion
