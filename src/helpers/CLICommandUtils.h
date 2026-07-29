#pragma once

#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

namespace mesh {
namespace cli {

enum class StandaloneWiFiKey : uint8_t {
  None = 0,
  SSID,
  Password,
  PowerSave,
  Status,
  CLI,
};

enum class NoArgCommandMatch : uint8_t {
  NoMatch = 0,
  Exact,
  HasArguments,
};

inline StandaloneWiFiKey classifyStandaloneWiFiGet(const char* config) {
  if (config == nullptr) return StandaloneWiFiKey::None;
  if (strcmp(config, "wifi.ssid") == 0) return StandaloneWiFiKey::SSID;
  if (strcmp(config, "wifi.status") == 0) return StandaloneWiFiKey::Status;
  if (strcmp(config, "wifi.powersave") == 0) {
    return StandaloneWiFiKey::PowerSave;
  }
  if (strcmp(config, "wifi.cli") == 0) return StandaloneWiFiKey::CLI;
  return StandaloneWiFiKey::None;
}

inline StandaloneWiFiKey classifyStandaloneWiFiSet(const char* config,
                                                    const char** value) {
  if (value != nullptr) *value = nullptr;
  if (config == nullptr) return StandaloneWiFiKey::None;

  struct Entry {
    const char* name;
    StandaloneWiFiKey key;
  };
  const Entry entries[] = {
      {"wifi.ssid", StandaloneWiFiKey::SSID},
      {"wifi.pwd", StandaloneWiFiKey::Password},
      {"wifi.powersave", StandaloneWiFiKey::PowerSave},
      {"wifi.cli", StandaloneWiFiKey::CLI},
  };
  for (const Entry& entry : entries) {
    const size_t len = strlen(entry.name);
    if (strncmp(config, entry.name, len) != 0) continue;
    if (config[len] != 0 && config[len] != ' ') continue;
    if (value != nullptr) {
      *value = config[len] == ' ' ? config + len + 1 : config + len;
    }
    return entry.key;
  }
  return StandaloneWiFiKey::None;
}

inline bool standaloneWiFiSSIDValid(const char* value) {
  if (value == nullptr) return false;
  const size_t len = strlen(value);
  return len >= 1 && len <= 31;
}

inline bool standaloneWiFiPasswordValid(const char* value) {
  return value != nullptr && strlen(value) <= 63;
}

inline bool parseStandaloneWiFiPowerSave(const char* value,
                                         uint8_t& power_save) {
  if (value == nullptr) return false;
  if (strcmp(value, "min") == 0) {
    power_save = 0;
    return true;
  }
  if (strcmp(value, "none") == 0) {
    power_save = 1;
    return true;
  }
  if (strcmp(value, "max") == 0) {
    power_save = 2;
    return true;
  }
  return false;
}

inline NoArgCommandMatch matchNoArgCommand(const char* command,
                                           const char* expected) {
  if (command == nullptr || expected == nullptr) {
    return NoArgCommandMatch::NoMatch;
  }
  const size_t len = strlen(expected);
  if (strncmp(command, expected, len) != 0) {
    return NoArgCommandMatch::NoMatch;
  }
  if (command[len] == 0) return NoArgCommandMatch::Exact;
  if (command[len] != ' ') return NoArgCommandMatch::NoMatch;
  const char* rest = command + len;
  while (*rest == ' ') rest++;
  return *rest == 0 ? NoArgCommandMatch::Exact
                    : NoArgCommandMatch::HasArguments;
}

inline void formatUnknownSetting(char* reply, size_t capacity,
                                 const char* setting) {
  if (reply == nullptr || capacity == 0) return;
  snprintf(reply, capacity, "Error: unknown setting: %s",
           setting != nullptr ? setting : "");
}

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
