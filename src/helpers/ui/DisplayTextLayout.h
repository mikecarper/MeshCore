#pragma once

#include <helpers/ui/DisplayDriver.h>

namespace mesh {
namespace ui {

// Draw complete text across a bounded number of rows. Lines may break in the
// middle of a word (which is important for SSIDs, where every space is
// significant), but never in the middle of a UTF-8 codepoint. If the text
// cannot fit in max_lines, only the final row is ellipsized.
inline int drawTextWrapped(DisplayDriver& display, int x, int y, int max_width,
                           int line_height, int max_lines, const char* str) {
  if (str == nullptr || str[0] == 0 || max_width <= 0
      || line_height <= 0 || max_lines <= 0) {
    return 0;
  }

  int lines = 0;
  size_t offset = 0;
  while (str[offset] != 0 && lines < max_lines) {
    // A display row is necessarily much shorter than this, but retain the
    // same generous bound used by DisplayDriver::drawTextEllipsized().
    char row[256];
    size_t row_length = 0;

    while (str[offset + row_length] != 0) {
      size_t codepoint_length = 1;
      const uint8_t first = (uint8_t)str[offset + row_length];
      if (first >= 0xC2 && first <= 0xDF) {
        codepoint_length = 2;
      } else if (first >= 0xE0 && first <= 0xEF) {
        codepoint_length = 3;
      } else if (first >= 0xF0 && first <= 0xF4) {
        codepoint_length = 4;
      }
      for (size_t i = 1; i < codepoint_length; ++i) {
        if (str[offset + row_length + i] == 0
            || ((uint8_t)str[offset + row_length + i] & 0xC0) != 0x80) {
          codepoint_length = 1;
          break;
        }
      }

      const size_t candidate_length = row_length + codepoint_length;
      if (candidate_length >= sizeof(row)) break;
      memcpy(row, str + offset, candidate_length);
      row[candidate_length] = 0;
      if (display.getTextWidth(row) > max_width) break;
      row_length = candidate_length;
    }

    // Ensure forward progress even for a glyph wider than the viewport.
    if (row_length == 0) {
      row_length = 1;
      while (str[offset + row_length] != 0
             && ((uint8_t)str[offset + row_length] & 0xC0) == 0x80
             && row_length + 1 < sizeof(row)) {
        ++row_length;
      }
    }

    if (lines + 1 == max_lines && str[offset + row_length] != 0) {
      display.drawTextEllipsized(x, y + lines * line_height, max_width,
                                 str + offset);
      ++lines;
      break;
    }

    memcpy(row, str + offset, row_length);
    row[row_length] = 0;
    display.setCursor(x, y + lines * line_height);
    display.print(row);
    offset += row_length;
    ++lines;
  }
  return lines;
}

}  // namespace ui
}  // namespace mesh
