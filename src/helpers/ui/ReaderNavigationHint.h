#pragma once

#include "DisplayDriver.h"
#include "TouchInput.h"

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

// Enlarge the header exit target without enlarging text. Reserve this same
// height for content, touch detection and diagnostic outlines.
inline int readerTouchHeaderHeight(int content_height) {
  return content_height + 8 < 24 ? 24 : content_height + 8;
}

// Touch readers use five equal, full-width hit cells. A 24-unit row is 72
// physical pixels high on both Indicator render profiles; keep the same font.
inline TouchNavigationBar makeReaderTouchBar(DisplayDriver& text, int bottom,
                                              int exit_height = 0) {
  TouchNavigationBar bar;
  const int line_height = text.textLineHeight();
  bar.height = line_height + 4 < 24 ? 24 : line_height + 4;
  if (bar.height > bottom) bar.height = bottom;
  bar.top = bottom - bar.height;
  bar.exit_height = exit_height < 0 ? 0 : exit_height > bar.top ? bar.top : exit_height;
  return bar;
}

inline void drawReaderTouchBar(DisplayDriver& text, const TouchNavigationBar& bar) {
  static const char* const labels[] = {"4<<", "2<", ">1", ">>3", "X"};
  text.setColor(UIColor::window_bkg);
  text.fillRect(0, bar.top, text.width(), bar.height);
  text.setColor(UIColor::corp_blue);
  text.drawRect(0, bar.top, text.width(), 1);
  const int line_height = text.textLineHeight();
  const int y = bar.top + (bar.height - line_height) / 2;
  for (int cell = 0; cell < 5; ++cell) {
    const int left = text.width() * cell / 5;
    const int right = text.width() * (cell + 1) / 5;
    text.drawTextCentered((left + right) / 2, y, labels[cell]);
  }
}

}  // namespace ui
}  // namespace mesh
