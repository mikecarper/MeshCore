#pragma once

#include "DisplayDriver.h"
#include "UsbKeyboardConsole.h"
#include <ctype.h>
#include <string.h>

namespace mesh { namespace ui {

inline bool usbKeyboardScreenFits(int width, int height, int line_height) {
  return width >= 96 && line_height > 0 && height >= line_height * 6;
}

// Hide arguments for common credential and key commands, including while
// editing. The real command remains in the editor only until submission.
inline size_t usbKeyboardSecretStart(const char* line) {
  char lower[UsbKeyboardConsole::CommandCapacity];
  size_t n = 0;
  while (line[n] && n + 1 < sizeof(lower)) {
    lower[n] = static_cast<char>(tolower(static_cast<unsigned char>(line[n])));
    ++n;
  }
  lower[n] = 0;
  const bool secret = strstr(lower, "password") || strstr(lower, "login ")
      || strstr(lower, "pwd") || strstr(lower, "secret") || strstr(lower, "token")
      || strstr(lower, "psk") || strstr(lower, "key") || strstr(lower, "mt.channel ");
  if (!secret) return n;
  const char* space = strchr(line, ' ');
  return space ? static_cast<size_t>(space + 1 - line) : n;
}

template <typename Display>
bool drawUsbKeyboardOverlay(Display& display, const UsbKeyboardConsole& console) {
  if (!console.visible()) return false;
  display.setTextSize(1);
  const int width = display.width(), height = display.height();
  const int line = display.textLineHeight();
  if (!usbKeyboardScreenFits(width, height, line)) return false;
  const int rows = 4;
  const int box_height = line + 4;
  const int box_y = height - box_height;
  const int top = box_y - rows * line - 2;
  display.setColor(UIColor::popup_bkg);
  display.fillRect(0, top, width, height - top);
  display.setColor(UIColor::popup_txt);
  for (int row = 0; row < rows; ++row) {
    const size_t index = console.scroll() + rows - 1 - row;
    if (index < console.lineCount())
      display.drawTextEllipsized(3, top + row * line, width - 6, console.lineFromNewest(index));
  }
  display.drawRect(0, box_y, width, box_height);
  display.drawTextLeftAlign(3, box_y + 2, ">");
  char visible[UsbKeyboardConsole::CommandCapacity];
  memcpy(visible, console.command(), console.length() + 1);
  const size_t secret = usbKeyboardSecretStart(visible);
  for (size_t i = secret; i < console.length(); ++i) visible[i] = '*';
  size_t start = 0;
  char before[UsbKeyboardConsole::CommandCapacity];
  memcpy(before, visible, console.cursor());
  before[console.cursor()] = 0;
  const int available = width - 19;
  while (start < console.cursor() && display.getTextWidth(before + start) > available)
    ++start;
  // Keep the portion after the cursor inside the same bounded text box.
  while (visible[start] && display.getTextWidth(visible + start) > available)
    visible[strlen(visible) - 1] = 0;
  display.drawTextLeftAlign(12, box_y + 2, visible + start);
  const int cursor_x = 12 + display.getTextWidth(before + start);
  display.drawTextLeftAlign(cursor_x, box_y + 2, "_");
  return true;
}

} }
