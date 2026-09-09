#pragma once

#include "DisplayDriver.h"

namespace mesh {
namespace ui {

struct ButtonReaderHintLayout {
  int top;
  int line_height;
  int line_count;
  const char* lines[3];
};

// All gestures stay visible: no blinking between page and channel controls.
// Separate the controls where space permits, retaining the existing font.
// Narrow displays use the compact hint before needing additional rows.
inline ButtonReaderHintLayout makeButtonReaderHintLayout(
    DisplayDriver& text, int line_height, int bottom, bool show_groups = false) {
  (void)show_groups;  // Kept for source compatibility; no timed alternation.
  ButtonReaderHintLayout layout = {
      0, line_height, 1, {"4 <<-  2 <- tap -> 1  ->> 3  hold: Exit", nullptr, nullptr}};
  if (text.getTextWidth(layout.lines[0]) > text.width())
    layout.lines[0] = "4<<2<tap>1>>3 hold:X";
  if (text.getTextWidth(layout.lines[0]) > text.width()) {
    layout.line_count = 2;
    layout.lines[0] = "4<<2<tap>1>>3";
    layout.lines[1] = "hold:X";
    if (text.getTextWidth(layout.lines[0]) > text.width()
        || text.getTextWidth(layout.lines[1]) > text.width()) {
      layout.line_count = 3;
      layout.lines[0] = "4<<2<";
      layout.lines[1] = ">1>>3";
      layout.lines[2] = "holdX";
    }
  }
  layout.top = bottom - layout.line_count * line_height;
  if (layout.top < 0) layout.top = 0;
  return layout;
}

inline void drawButtonReaderHint(DisplayDriver& text,
                                 const ButtonReaderHintLayout& layout) {
  text.setColor(UIColor::window_bkg);
  text.fillRect(0, layout.top, text.width(),
                layout.line_count * layout.line_height);
  text.setColor(UIColor::secondary_txt);
  for (int row = 0; row < layout.line_count; ++row) {
    text.drawTextCentered(text.width() / 2,
                          layout.top + row * layout.line_height,
                          layout.lines[row]);
  }
}

}  // namespace ui
}  // namespace mesh
