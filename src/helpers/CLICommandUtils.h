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

enum class RecentRepeaterGetMatch : uint8_t {
  NoMatch = 0,
  Valid,
  Invalid,
};

enum class TerminalChannelCommandMatch : uint8_t {
  NoMatch = 0,
  Valid,
  MissingSelector,
  MissingMessage,
};

enum class TerminalArgumentCommandMatch : uint8_t {
  NoMatch = 0,
  Valid,
  MissingArgument,
};

enum class TerminalPathMode : uint8_t {
  Explicit = 0,
  Direct,
  Clear,
};

enum class TerminalPathParseResult : uint8_t {
  Valid = 0,
  Missing,
  InvalidPrefix,
  MixedPrefixSize,
  InvalidSeparator,
  TooManyHops,
  RouteTooLong,
};

// Direct-route prefixes are consumed hop by hop, so a destination cannot
// distinguish a zero-hop packet from one that arrived through repeaters.
// "ROUTED" describes the received unicast class without claiming zero hops.
inline const char* terminalInboundRouteLabel(bool is_direct_route) {
  return is_direct_route ? "ROUTED" : "FLOOD";
}

struct RecentRepeaterGetQuery {
  int page;
  uint8_t search_prefix[3];
  uint8_t search_prefix_len;
};

struct TerminalChannelMessage {
  const char* selector;
  size_t selector_len;
  const char* text;
};

struct TerminalPath {
  TerminalPathMode mode;
  uint8_t encoded_len;
  uint8_t hash_size;
  uint8_t hop_count;
  size_t byte_len;
};

inline const char* skipRecentRepeaterSpaces(const char* text) {
  while (text != nullptr && (*text == ' ' || *text == '\t')) text++;
  return text;
}

inline TerminalArgumentCommandMatch parseTerminalArgumentCommand(
    const char* command, const char* verb, const char*& argument) {
  argument = nullptr;
  if (command == nullptr || verb == nullptr || *verb == 0) {
    return TerminalArgumentCommandMatch::NoMatch;
  }

  const size_t verb_len = strlen(verb);
  for (size_t i = 0; i < verb_len; i++) {
    char actual = command[i];
    char expected = verb[i];
    if (actual >= 'A' && actual <= 'Z') actual += 'a' - 'A';
    if (expected >= 'A' && expected <= 'Z') expected += 'a' - 'A';
    if (actual != expected) return TerminalArgumentCommandMatch::NoMatch;
  }
  if (command[verb_len] != 0 && command[verb_len] != ' '
      && command[verb_len] != '\t') {
    return TerminalArgumentCommandMatch::NoMatch;
  }

  const char* cursor = skipRecentRepeaterSpaces(command + verb_len);
  if (*cursor == 0) return TerminalArgumentCommandMatch::MissingArgument;
  argument = cursor;
  return TerminalArgumentCommandMatch::Valid;
}

// Return true once terminal input has reached a login password. The caller
// can still retain the real bytes for command handling while echoing '*'.
inline bool shouldMaskTerminalInput(const char* line) {
  line = skipRecentRepeaterSpaces(line);
  const char* password = nullptr;
  return parseTerminalArgumentCommand(line, "login", password)
      == TerminalArgumentCommandMatch::Valid;
}

// Remove one complete UTF-8 code point from a terminal input buffer. Invalid
// trailing bytes are still removed safely, and the result remains terminated.
inline size_t eraseLastTerminalInput(char* line, size_t line_length) {
  if (line == nullptr || line_length == 0) return 0;

  size_t new_length = line_length - 1;
  while (new_length > 0
         && (static_cast<uint8_t>(line[new_length]) & 0xC0) == 0x80) {
    new_length--;
  }
  line[new_length] = 0;
  return new_length;
}

inline TerminalChannelCommandMatch parseTerminalChannelMessage(
    const char* command, TerminalChannelMessage& message) {
  message.selector = nullptr;
  message.selector_len = 0;
  message.text = nullptr;
  if (command == nullptr || strncmp(command, "channel", 7) != 0) {
    return TerminalChannelCommandMatch::NoMatch;
  }

  const char* cursor = command + 7;
  if (*cursor != 0 && *cursor != ' ' && *cursor != '\t') {
    return TerminalChannelCommandMatch::NoMatch;
  }
  cursor = skipRecentRepeaterSpaces(cursor);
  if (*cursor == 0) return TerminalChannelCommandMatch::MissingSelector;

  message.selector = cursor;
  while (*cursor != 0 && *cursor != ' ' && *cursor != '\t') cursor++;
  message.selector_len = static_cast<size_t>(cursor - message.selector);
  cursor = skipRecentRepeaterSpaces(cursor);
  if (*cursor == 0) return TerminalChannelCommandMatch::MissingMessage;

  message.text = cursor;
  return TerminalChannelCommandMatch::Valid;
}

inline bool parseTerminalChannelIndex(const TerminalChannelMessage& message,
                                      size_t max_channels,
                                      size_t& channel_index) {
  if (message.selector == nullptr || message.selector_len == 0
      || max_channels == 0) {
    return false;
  }

  size_t value = 0;
  for (size_t i = 0; i < message.selector_len; i++) {
    const char c = message.selector[i];
    if (c < '0' || c > '9') return false;
    const size_t digit = static_cast<size_t>(c - '0');
    if (digit >= max_channels
        || value > (max_channels - 1 - digit) / 10) {
      return false;
    }
    value = value * 10 + digit;
  }
  if (value >= max_channels) return false;
  channel_index = value;
  return true;
}

inline bool terminalChannelNameMatches(const TerminalChannelMessage& message,
                                       const char* channel_name) {
  return message.selector != nullptr && channel_name != nullptr
      && strlen(channel_name) == message.selector_len
      && memcmp(channel_name, message.selector, message.selector_len) == 0;
}

inline int recentRepeaterHexNibble(char c) {
  if (c >= '0' && c <= '9') return c - '0';
  if (c >= 'a' && c <= 'f') return c - 'a' + 10;
  if (c >= 'A' && c <= 'F') return c - 'A' + 10;
  return -1;
}

inline bool terminalPathKeywordMatches(const char* text,
                                       const char* keyword) {
  const size_t keyword_len = strlen(keyword);
  if (strncmp(text, keyword, keyword_len) != 0) return false;
  text = skipRecentRepeaterSpaces(text + keyword_len);
  return *text == 0;
}

// Parse: direct | clear | <hop>[<separator><hop> ...]
// Each explicit hop is a one-, two-, or three-byte hexadecimal prefix. All
// hops must use the same width because that width is encoded once for the
// complete MeshCore direct path. Separators may be commas, spaces, tabs, or
// mixtures of a comma and surrounding whitespace.
inline TerminalPathParseResult parseTerminalPath(
    const char* input, uint8_t* output, size_t output_capacity,
    uint8_t max_hops, TerminalPath& result) {
  result.mode = TerminalPathMode::Explicit;
  result.encoded_len = 0;
  result.hash_size = 0;
  result.hop_count = 0;
  result.byte_len = 0;

  input = skipRecentRepeaterSpaces(input);
  if (input == nullptr || *input == 0) {
    return TerminalPathParseResult::Missing;
  }
  if (terminalPathKeywordMatches(input, "direct")) {
    result.mode = TerminalPathMode::Direct;
    return TerminalPathParseResult::Valid;
  }
  if (terminalPathKeywordMatches(input, "clear")) {
    result.mode = TerminalPathMode::Clear;
    return TerminalPathParseResult::Valid;
  }

  uint8_t hash_size = 0;
  uint8_t hop_count = 0;
  size_t offset = 0;
  while (*input != 0) {
    input = skipRecentRepeaterSpaces(input);
    const char* token = input;
    size_t token_len = 0;
    while (input[token_len] != 0 && input[token_len] != ','
           && input[token_len] != ' ' && input[token_len] != '\t') {
      token_len++;
    }
    if (token_len != 2 && token_len != 4 && token_len != 6) {
      return TerminalPathParseResult::InvalidPrefix;
    }

    const uint8_t token_hash_size = static_cast<uint8_t>(token_len / 2);
    if (hash_size == 0) {
      hash_size = token_hash_size;
      result.hash_size = hash_size;
    } else if (token_hash_size != hash_size) {
      return TerminalPathParseResult::MixedPrefixSize;
    }
    if (hop_count >= max_hops) {
      return TerminalPathParseResult::TooManyHops;
    }
    if (output == nullptr || offset + hash_size > output_capacity) {
      return TerminalPathParseResult::RouteTooLong;
    }

    for (uint8_t i = 0; i < hash_size; i++) {
      const int high = recentRepeaterHexNibble(token[i * 2]);
      const int low = recentRepeaterHexNibble(token[i * 2 + 1]);
      if (high < 0 || low < 0) {
        return TerminalPathParseResult::InvalidPrefix;
      }
      output[offset++] = static_cast<uint8_t>((high << 4) | low);
    }
    hop_count++;
    input += token_len;

    bool consumed_comma = false;
    input = skipRecentRepeaterSpaces(input);
    if (*input == ',') {
      consumed_comma = true;
      input = skipRecentRepeaterSpaces(input + 1);
    }
    if (*input == 0) {
      if (consumed_comma) return TerminalPathParseResult::InvalidPrefix;
      break;
    }
  }

  result.mode = TerminalPathMode::Explicit;
  result.encoded_len = static_cast<uint8_t>(
      ((hash_size - 1) << 6) | (hop_count & 63));
  result.hash_size = hash_size;
  result.hop_count = hop_count;
  result.byte_len = offset;
  return TerminalPathParseResult::Valid;
}

inline bool parseRecentRepeaterPage(const char* text, int& page) {
  text = skipRecentRepeaterSpaces(text);
  if (text == nullptr || *text == 0) {
    page = 1;
    return true;
  }

  uint32_t parsed = 0;
  bool saw_digit = false;
  while (*text >= '0' && *text <= '9') {
    saw_digit = true;
    const uint8_t digit = static_cast<uint8_t>(*text++ - '0');
    // The formatter clamps the requested page to the available page count.
    // Saturating here avoids signed overflow from hostile remote CLI input.
    if (parsed <= 65535UL) {
      parsed = parsed * 10UL + digit;
      if (parsed > 65535UL) parsed = 65535UL;
    }
  }
  text = skipRecentRepeaterSpaces(text);
  if (!saw_digit || *text != 0) return false;
  page = parsed == 0 ? 1 : static_cast<int>(parsed);
  return true;
}

inline void formatRecentRepeaterAge(char* output, size_t output_size,
                                    uint32_t age_seconds) {
  if (output == nullptr || output_size == 0) return;
  if (age_seconds >= 3600UL) {
    snprintf(output, output_size, "%luh",
             static_cast<unsigned long>(age_seconds / 3600UL));
  } else if (age_seconds >= 60UL) {
    snprintf(output, output_size, "%lum",
             static_cast<unsigned long>(age_seconds / 60UL));
  } else {
    snprintf(output, output_size, "%lus",
             static_cast<unsigned long>(age_seconds));
  }
}

// Parse the portion after `get `. Besides the existing list/page forms, this
// accepts `recent.repeaters search <2|4|6 hex> [page [N]|N]`.
inline RecentRepeaterGetMatch parseRecentRepeaterGet(
    const char* config, RecentRepeaterGetQuery& query) {
  query.page = 1;
  memset(query.search_prefix, 0, sizeof(query.search_prefix));
  query.search_prefix_len = 0;
  if (config == nullptr || strncmp(config, "recent.repeater", 15) != 0) {
    return RecentRepeaterGetMatch::NoMatch;
  }

  const char* cursor = config + 15;
  if (*cursor == 's') cursor++;
  if (*cursor != 0 && *cursor != ' ' && *cursor != '\t') {
    return RecentRepeaterGetMatch::NoMatch;
  }
  cursor = skipRecentRepeaterSpaces(cursor);
  if (*cursor == 0) return RecentRepeaterGetMatch::Valid;

  if (strncmp(cursor, "search", 6) == 0
      && (cursor[6] == 0 || cursor[6] == ' ' || cursor[6] == '\t')) {
    cursor = skipRecentRepeaterSpaces(cursor + 6);
    const char* hex = cursor;
    while (*cursor != 0 && *cursor != ' ' && *cursor != '\t') cursor++;
    const size_t hex_len = static_cast<size_t>(cursor - hex);
    if (hex_len != 2 && hex_len != 4 && hex_len != 6) {
      return RecentRepeaterGetMatch::Invalid;
    }
    for (size_t i = 0; i < hex_len; i += 2) {
      const int hi = recentRepeaterHexNibble(hex[i]);
      const int lo = recentRepeaterHexNibble(hex[i + 1]);
      if (hi < 0 || lo < 0) return RecentRepeaterGetMatch::Invalid;
      query.search_prefix[i / 2] = static_cast<uint8_t>((hi << 4) | lo);
    }
    query.search_prefix_len = static_cast<uint8_t>(hex_len / 2);

    cursor = skipRecentRepeaterSpaces(cursor);
    if (strncmp(cursor, "page", 4) == 0
        && (cursor[4] == 0 || cursor[4] == ' ' || cursor[4] == '\t')) {
      cursor = skipRecentRepeaterSpaces(cursor + 4);
    }
    return parseRecentRepeaterPage(cursor, query.page)
        ? RecentRepeaterGetMatch::Valid : RecentRepeaterGetMatch::Invalid;
  }

  if (strncmp(cursor, "page", 4) == 0
      && (cursor[4] == 0 || cursor[4] == ' ' || cursor[4] == '\t')) {
    cursor = skipRecentRepeaterSpaces(cursor + 4);
  }
  return parseRecentRepeaterPage(cursor, query.page)
      ? RecentRepeaterGetMatch::Valid : RecentRepeaterGetMatch::Invalid;
}

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
