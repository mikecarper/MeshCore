#pragma once

#include "ColorTheme.h"
#include "TouchInput.h"

namespace mesh {
namespace ui {

struct TouchDebugArea {
  int x, y, width, height;
  TouchAction action;
};

struct TouchDebugAreas {
  TouchDebugArea areas[10];
  int count = 0;

  void add(int x, int y, int width, int height, TouchAction action) {
    if (width > 0 && height > 0 && count < 10)
      areas[count++] = {x, y, width, height, action};
  }
};

// Visual (already unmirrored), logical coordinates. The UI supplies the same
// current screen geometry to this overlay and TouchInput::update(). Tests
// compare every pixel in these rectangles with the actual stationary actions.
inline TouchDebugAreas makeTouchDebugAreas(int width, int height,
    int center_zone_percent, bool bottom_selector,
    const TouchSplitSelector* split, const TouchNavigationBar* reader) {
  TouchDebugAreas result;
  if (width <= 0 || height <= 0) return result;
  if (reader != nullptr) {
    result.add(0, 0, width, reader->exit_height, TouchAction::Select);
    result.add(0, reader->exit_height, width / 2,
        reader->top - reader->exit_height, TouchAction::Previous);
    result.add(width / 2, reader->exit_height, width - width / 2,
        reader->top - reader->exit_height, TouchAction::Next);
    const TouchAction actions[] = {TouchAction::VerticalPrevious,
        TouchAction::Previous, TouchAction::Next,
        TouchAction::VerticalNext, TouchAction::Select};
    for (int cell = 0; cell < 5; ++cell) {
      const int left = width * cell / 5;
      const int right = width * (cell + 1) / 5;
      result.add(left, reader->top, right - left, reader->height, actions[cell]);
    }
    // Retain truthfulness for layouts with space below the footer as well.
    const int below = reader->top + reader->height;
    result.add(0, below, width / 2, height - below, TouchAction::Previous);
    result.add(width / 2, below, width - width / 2, height - below, TouchAction::Next);
    return result;
  }

  const int body_bottom = bottom_selector ? height * 3 / 4 : height;
  if (bottom_selector) {
    result.add(0, body_bottom, width / 4, height - body_bottom,
               TouchAction::VerticalPrevious);
    result.add(width * 3 / 4, body_bottom, width - width * 3 / 4,
               height - body_bottom, TouchAction::VerticalNext);
  }
  if (split != nullptr) {
    result.add(0, 0, split->side_nav_width, body_bottom, TouchAction::Previous);
    result.add(width - split->side_nav_width, 0, split->side_nav_width,
               body_bottom, TouchAction::Next);
    const int bottom = split->top_y + split->height < body_bottom
        ? split->top_y + split->height : body_bottom;
    result.add(split->left_x, split->top_y, split->left_width,
               bottom - split->top_y, TouchAction::SelectLeft);
    result.add(split->right_x, split->top_y, split->right_width,
               bottom - split->top_y, TouchAction::SelectRight);
    return result;
  }
  const int side_percent = (100 - center_zone_percent) / 2;
  const int left = width * side_percent / 100;
  const int right = width - left;
  result.add(0, 0, left, body_bottom, TouchAction::Previous);
  result.add(left, 0, right - left, body_bottom, TouchAction::Select);
  result.add(right, 0, width - right, body_bottom, TouchAction::Next);
  return result;
}

inline int touchDebugAreaAt(const TouchDebugAreas& areas, int x, int y) {
  for (int i = 0; i < areas.count; ++i) {
    const auto& r = areas.areas[i];
    if (x >= r.x && x < r.x + r.width && y >= r.y && y < r.y + r.height)
      return i;
  }
  return -1;
}

inline void drawTouchDebugAreas(DisplayDriver& display, const TouchDebugAreas& areas,
                                int pressed_x = -1, int pressed_y = -1) {
  const int pressed = touchDebugAreaAt(areas, pressed_x, pressed_y);
  for (int i = 0; i < areas.count; ++i) {
    display.setColor(i == pressed ? color_theme::TOUCH_PRESSED : color_theme::TOUCH_OUTLINE);
    const auto& area = areas.areas[i];
    const int left = area.x < 0 ? 0 : area.x;
    const int top = area.y < 0 ? 0 : area.y;
    const int right = area.x + area.width < display.width()
        ? area.x + area.width - 1 : display.width() - 1;
    const int bottom = area.y + area.height < display.height()
        ? area.y + area.height - 1 : display.height() - 1;
    if (left > right || top > bottom) continue;
    // One logical pixel per dot, two pixels of gap. Use the normal drawing
    // path so native 480px and scaled 320px canvases show identical bounds.
    for (int x = left; x <= right; x += 3) {
      display.fillRect(x, top, 1, 1);
      display.fillRect(x, bottom, 1, 1);
    }
    for (int y = top; y <= bottom; y += 3) {
      display.fillRect(left, y, 1, 1);
      display.fillRect(right, y, 1, 1);
    }
    display.fillRect(right, bottom, 1, 1);
  }
}

}  // namespace ui
}  // namespace mesh
