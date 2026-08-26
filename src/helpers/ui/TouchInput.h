#pragma once

#include <stdint.h>

namespace mesh {
namespace ui {

enum class TouchAction : uint8_t {
  None,
  Previous,
  Next,
  Select,
  VerticalPrevious,
  VerticalNext,
};

// Converts one-finger touch samples into the three actions understood by the
// button-oriented companion UI. An action is emitted only after release so a
// drag cannot activate the page under the user's finger.
class TouchInput {
  bool _active = false;
  bool _reverse_swipes;
  bool _separate_vertical_swipes;
  uint8_t _center_zone_percent;
  uint8_t _touch_samples = 0;
  uint8_t _release_samples = 0;
  int _start_x = 0;
  int _start_y = 0;
  int _last_x = 0;
  int _last_y = 0;

  static int magnitude(int value) { return value < 0 ? -value : value; }

  TouchAction swipeAction(bool negative_direction) const {
    const bool next = negative_direction != _reverse_swipes;
    return next ? TouchAction::Next : TouchAction::Previous;
  }

  TouchAction verticalSwipeAction(bool negative_direction) const {
    if (!_separate_vertical_swipes) return swipeAction(negative_direction);
    const bool next = negative_direction != _reverse_swipes;
    return next ? TouchAction::VerticalNext
                : TouchAction::VerticalPrevious;
  }

public:
  explicit TouchInput(bool reverse_swipes = false,
                      bool separate_vertical_swipes = false,
                      uint8_t center_zone_percent = 34)
      : _reverse_swipes(reverse_swipes),
        _separate_vertical_swipes(separate_vertical_swipes),
        _center_zone_percent(center_zone_percent > 100
                                 ? 100
                                 : center_zone_percent) {}

  TouchAction update(bool touched, int x, int y, int width, int height,
                     bool bottom_selector = false) {
    if (touched) {
      _release_samples = 0;
      if (!_active) {
        _active = true;
        _start_x = x;
        _start_y = y;
        _touch_samples = 0;
      }
      if (_touch_samples != UINT8_MAX) ++_touch_samples;
      _last_x = x;
      _last_y = y;
      return TouchAction::None;
    }

    if (!_active) return TouchAction::None;
    // A moving FT5x06-family controller can briefly report no points between
    // two valid samples. Require a stable release so that gap cannot split one
    // swipe into a swipe followed by an endpoint tap.
    if (++_release_samples < 2) return TouchAction::None;
    _active = false;
    _release_samples = 0;

    // A contact seen only once has no measurable direction. Ignoring it is
    // safer than treating the endpoint of a fast swipe as an opposite tap.
    if (_touch_samples < 2 || width <= 0 || height <= 0) {
      return TouchAction::None;
    }

    const int dx = _last_x - _start_x;
    const int dy = _last_y - _start_y;
    const int abs_dx = magnitude(dx);
    const int abs_dy = magnitude(dy);
    const int horizontal_threshold = width / 8 > 8 ? width / 8 : 8;
    const int vertical_threshold = height / 8 > 8 ? height / 8 : 8;

    if (abs_dx >= abs_dy && abs_dx >= horizontal_threshold) {
      return swipeAction(dx < 0);
    }
    if (abs_dy > abs_dx && abs_dy >= vertical_threshold) {
      return verticalSwipeAction(dy < 0);
    }

    // Message screens may reserve the otherwise empty ends of their bottom
    // status bar as forgiving arrow buttons. Keep the label in the middle
    // inert so an imprecise arrow tap cannot accidentally close the screen.
    if (bottom_selector && _separate_vertical_swipes
        && _start_y >= (height * 3) / 4) {
      if (_start_x < width / 4) {
        return TouchAction::VerticalPrevious;
      }
      if (_start_x >= (width * 3) / 4) {
        return TouchAction::VerticalNext;
      }
      return TouchAction::None;
    }

    // Taps use three broad zones so every companion action remains available:
    // left = previous, center = select, right = next.
    // Use the initial position for a tap. If a short swipe falls just below
    // the movement threshold, its endpoint may be in the opposite tap zone on
    // displays whose touch X axis is inverted.
    const int side_percent = (100 - _center_zone_percent) / 2;
    const int center_left = (width * side_percent) / 100;
    const int center_right = width - center_left;
    if (_start_x < center_left) return TouchAction::Previous;
    if (_start_x >= center_right) return TouchAction::Next;
    return TouchAction::Select;
  }

  void reset() {
    _active = false;
    _touch_samples = 0;
    _release_samples = 0;
  }
};

}  // namespace ui
}  // namespace mesh
