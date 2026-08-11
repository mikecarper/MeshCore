#pragma once

#include <stddef.h>
#include <stdint.h>
#include <string.h>

namespace mesh {

enum class TerminalDisplayCategory : uint8_t {
  None = 0,
  Adverts,
  Channels,
  Emergency,
};

enum class TerminalDisplayParseResult : uint8_t {
  StatusAll = 0,
  StatusOne,
  Updated,
  InvalidCategory,
  InvalidValue,
};

struct TerminalDisplayCommand {
  TerminalDisplayCategory category;
  bool enabled;
};

// Runtime-only terminal filters. Binary Companion delivery and offline queueing
// are deliberately unaffected by these display choices.
class TerminalDisplayFilter {
public:
  TerminalDisplayFilter()
      : _adverts(false), _channels(false), _emergency(true) {}

  bool isEnabled(TerminalDisplayCategory category) const {
    if (category == TerminalDisplayCategory::Adverts) return _adverts;
    if (category == TerminalDisplayCategory::Channels) return _channels;
    if (category == TerminalDisplayCategory::Emergency) return _emergency;
    return false;
  }

  void setEnabled(TerminalDisplayCategory category, bool enabled) {
    if (category == TerminalDisplayCategory::Adverts) _adverts = enabled;
    else if (category == TerminalDisplayCategory::Channels) _channels = enabled;
    else if (category == TerminalDisplayCategory::Emergency) _emergency = enabled;
  }

  bool shouldShowAdvert() const { return _adverts; }

  bool shouldShowChannel(bool is_emergency) const {
    return is_emergency ? _emergency : _channels;
  }

private:
  bool _adverts;
  bool _channels;
  bool _emergency;
};

inline char terminalDisplayAsciiLower(char value) {
  return value >= 'A' && value <= 'Z' ? value + ('a' - 'A') : value;
}

inline bool terminalDisplayTokenEquals(const char* token, size_t token_len,
                                       const char* expected) {
  if (token == NULL || expected == NULL || strlen(expected) != token_len) {
    return false;
  }
  for (size_t i = 0; i < token_len; i++) {
    if (terminalDisplayAsciiLower(token[i])
        != terminalDisplayAsciiLower(expected[i])) {
      return false;
    }
  }
  return true;
}

inline const char* skipTerminalDisplaySpaces(const char* text) {
  while (text != NULL && (*text == ' ' || *text == '\t')) text++;
  return text;
}

inline const char* terminalDisplayCategoryName(
    TerminalDisplayCategory category) {
  if (category == TerminalDisplayCategory::Adverts) return "adverts";
  if (category == TerminalDisplayCategory::Channels) return "channels";
  if (category == TerminalDisplayCategory::Emergency) return "emergency";
  return "unknown";
}

inline TerminalDisplayParseResult parseTerminalDisplayCommand(
    const char* arguments, TerminalDisplayCommand& command) {
  command.category = TerminalDisplayCategory::None;
  command.enabled = false;

  const char* cursor = skipTerminalDisplaySpaces(arguments);
  if (cursor == NULL || *cursor == 0) {
    return TerminalDisplayParseResult::StatusAll;
  }

  const char* category_token = cursor;
  while (*cursor != 0 && *cursor != ' ' && *cursor != '\t') cursor++;
  const size_t category_len = (size_t)(cursor - category_token);
  if (terminalDisplayTokenEquals(category_token, category_len, "advert")
      || terminalDisplayTokenEquals(category_token, category_len, "adverts")
      || terminalDisplayTokenEquals(category_token, category_len,
                                    "advertisements")) {
    command.category = TerminalDisplayCategory::Adverts;
  } else if (terminalDisplayTokenEquals(category_token, category_len,
                                        "channel")
      || terminalDisplayTokenEquals(category_token, category_len,
                                    "channels")) {
    command.category = TerminalDisplayCategory::Channels;
  } else if (terminalDisplayTokenEquals(category_token, category_len,
                                        "emergency")) {
    command.category = TerminalDisplayCategory::Emergency;
  } else {
    return TerminalDisplayParseResult::InvalidCategory;
  }

  cursor = skipTerminalDisplaySpaces(cursor);
  if (*cursor == 0) return TerminalDisplayParseResult::StatusOne;

  const char* value_token = cursor;
  while (*cursor != 0 && *cursor != ' ' && *cursor != '\t') cursor++;
  const size_t value_len = (size_t)(cursor - value_token);
  cursor = skipTerminalDisplaySpaces(cursor);
  if (*cursor != 0) return TerminalDisplayParseResult::InvalidValue;

  if (terminalDisplayTokenEquals(value_token, value_len, "on")) {
    command.enabled = true;
  } else if (terminalDisplayTokenEquals(value_token, value_len, "off")) {
    command.enabled = false;
  } else {
    return TerminalDisplayParseResult::InvalidValue;
  }
  return TerminalDisplayParseResult::Updated;
}

}  // namespace mesh
