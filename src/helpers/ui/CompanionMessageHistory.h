#pragma once

#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

namespace mesh {
namespace ui {

struct CompanionMessageListLayout {
  int top;
  int row_height;
  int title_offset;
  int preview_offset;
  int divider_offset;
  int visible_rows;
};

struct CompanionMessageChromeLayout {
  bool compact_text;
  int header_divider_y;
  int origin_y;
  int message_y;
  int filter_height;
  int filter_text_offset;
};

inline CompanionMessageChromeLayout makeCompanionMessageChromeLayout(
    bool compact_text) {
  if (compact_text) {
    // At the Indicator's 3x physical mapping these are a 27px header and a
    // 42px footer around a 24px-tall fractionally scaled status font.
    return {true, 8, 11, 22, 14, 3};
  }
  return {false, 11, 14, 25, 24, 8};
}

inline CompanionMessageListLayout makeCompanionMessageListLayout(
    int display_height) {
  const int top = 20;
  const int row_height = 26;
  const int usable_height = display_height > top ? display_height - top : 0;
  return {top, row_height, 0, 11, 23, usable_height / row_height};
}

inline void formatCompanionMessageAge(char* output, size_t output_size,
                                      uint64_t elapsed_millis) {
  if (output == nullptr || output_size == 0) return;

  const uint64_t seconds = elapsed_millis / 1000ULL;
  if (seconds == 0) {
    snprintf(output, output_size, "now");
  } else if (seconds < 60ULL) {
    snprintf(output, output_size, "%lus ago", (unsigned long)seconds);
  } else if (seconds < 60ULL * 60ULL) {
    snprintf(output, output_size, "%lum ago",
             (unsigned long)(seconds / 60ULL));
  } else if (seconds < 24ULL * 60ULL * 60ULL) {
    snprintf(output, output_size, "%luh ago",
             (unsigned long)(seconds / (60ULL * 60ULL)));
  } else {
    snprintf(output, output_size, "%lud ago",
             (unsigned long)(seconds / (24ULL * 60ULL * 60ULL)));
  }
}

inline void makeCompanionMessagePreviewSingleLine(char* text) {
  if (text == nullptr) return;
  for (; *text != 0; ++text) {
    if (*text == '\r' || *text == '\n' || *text == '\t') *text = ' ';
  }
}

template <size_t Capacity, size_t MessageCapacity>
class CompanionMessageHistory {
public:
  struct Entry {
    uint64_t heard_millis;
    int channel_idx;
    char channel_name[32];
    char origin[62];
    char message[MessageCapacity];
  };

private:
  size_t _count;
  size_t _head;
  Entry _entries[Capacity];

  static void copyField(char* destination, size_t capacity,
                        const char* source) {
    if (capacity == 0) return;
    if (source == nullptr) source = "";
    strncpy(destination, source, capacity - 1);
    destination[capacity - 1] = 0;
  }

  static size_t directNameLength(const char* origin) {
    if (origin == nullptr) return 0;
    const char* route = strstr(origin, " [");
    return route == nullptr ? strlen(origin) : (size_t)(route - origin);
  }

  static bool sameDirectOrigin(const char* left, const char* right) {
    const size_t left_length = directNameLength(left);
    const size_t right_length = directNameLength(right);
    return left_length == right_length
        && memcmp(left, right, left_length) == 0;
  }

public:
  CompanionMessageHistory() : _count(0), _head(Capacity - 1) {
    static_assert(Capacity > 0, "message history capacity must be positive");
    static_assert(MessageCapacity > 1,
                  "message storage must include a terminator");
  }

  size_t count() const { return _count; }
  bool empty() const { return _count == 0; }

  void clear() {
    _count = 0;
    _head = Capacity - 1;
  }

  void add(uint64_t heard_millis, int channel_idx,
           const char* channel_name, const char* origin,
           const char* message) {
    _head = (_head + 1) % Capacity;
    if (_count < Capacity) ++_count;

    Entry& entry = _entries[_head];
    entry.heard_millis = heard_millis;
    entry.channel_idx = channel_idx;
    copyField(entry.channel_name, sizeof(entry.channel_name), channel_name);
    copyField(entry.origin, sizeof(entry.origin), origin);
    copyField(entry.message, sizeof(entry.message), message);
  }

  const Entry* newest(size_t age) const {
    if (age >= _count) return nullptr;
    const size_t index = (_head + Capacity - age) % Capacity;
    return &_entries[index];
  }

  static bool sameThread(const Entry& left, const Entry& right) {
    if (left.channel_idx >= 0 || right.channel_idx >= 0) {
      return left.channel_idx >= 0 && right.channel_idx >= 0
          && left.channel_idx == right.channel_idx;
    }
    return sameDirectOrigin(left.origin, right.origin);
  }

  bool hasNewerEntryForThread(size_t age) const {
    const Entry* candidate = newest(age);
    if (candidate == nullptr) return false;
    for (size_t newer_age = 0; newer_age < age; ++newer_age) {
      const Entry* newer = newest(newer_age);
      if (newer != nullptr && sameThread(*candidate, *newer)) return true;
    }
    return false;
  }

  static void threadLabel(const Entry& entry, char* output,
                          size_t output_size) {
    if (output == nullptr || output_size == 0) return;
    if (entry.channel_idx >= 0) {
      if (entry.channel_name[0] != 0) {
        copyField(output, output_size, entry.channel_name);
      } else {
        snprintf(output, output_size, "Channel %d", entry.channel_idx);
      }
      return;
    }

    const size_t prefix_length = directNameLength(entry.origin);
    if (prefix_length == 0) {
      snprintf(output, output_size, "Direct");
      return;
    }
    snprintf(output, output_size, "Direct: %.*s", (int)prefix_length,
             entry.origin);
  }
};

}  // namespace ui
}  // namespace mesh
