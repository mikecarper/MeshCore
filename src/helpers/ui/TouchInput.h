#pragma once

#include <stdint.h>

namespace mesh {
namespace ui {

enum class TouchAction : uint8_t {
  None,
  Previous,
  Next,
  Select,
  SelectLeft,
  SelectRight,
  VerticalPrevious,
  VerticalNext,
};

struct TouchSplitSelector {
  int left_x;
  int left_width;
  int right_x;
  int right_width;
  int top_y;
  int height;
  // Full-height page-navigation strips, zero for legacy selector layouts.
  int side_nav_width;
};

// Reader footer has five controls (<<, <, >, >>, X); the top header exits.
// All bounds are logical UI units supplied by the screen that draws them.
struct TouchNavigationBar {
  int top = 0;
  int height = 0;
  int exit_height = 0;
};

// Converts one-finger touch samples into actions understood by the
// button-oriented companion UI. An action is emitted only after release so a
// drag cannot activate the page under the user's finger.
class TouchInput {
  bool _active = false;
  bool _reverse_swipes;
  bool _reverse_vertical_swipes;
  bool _separate_vertical_swipes;
  bool _mirror_tap_x;
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
    const bool next = negative_direction != _reverse_vertical_swipes;
    return next ? TouchAction::VerticalNext
                : TouchAction::VerticalPrevious;
  }

public:
  int centerZonePercent() const { return _center_zone_percent; }
  bool hasSeparateVerticalSwipes() const { return _separate_vertical_swipes; }

  explicit TouchInput(bool reverse_swipes = false,
                      bool separate_vertical_swipes = false,
                      uint8_t center_zone_percent = 34,
                      bool mirror_tap_x = false)
      : TouchInput(reverse_swipes, separate_vertical_swipes,
                   center_zone_percent, mirror_tap_x, reverse_swipes) {}

  TouchInput(bool reverse_swipes, bool separate_vertical_swipes,
             uint8_t center_zone_percent, bool mirror_tap_x,
             bool reverse_vertical_swipes)
      : _reverse_swipes(reverse_swipes),
        _reverse_vertical_swipes(reverse_vertical_swipes),
        _separate_vertical_swipes(separate_vertical_swipes),
        _mirror_tap_x(mirror_tap_x),
        _center_zone_percent(center_zone_percent > 100
                                 ? 100
                                 : center_zone_percent) {}

  TouchAction update(bool touched, int x, int y, int width, int height,
                     bool bottom_selector = false,
                     const TouchSplitSelector* split_selector = nullptr,
                     const TouchNavigationBar* navigation_bar = nullptr) {
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

    // A contact seen only once has no measurable direction. Keep ignoring it
    // on ordinary pages so the endpoint of a fast swipe cannot become an
    // opposite tap. An explicit split selector is different: its bounded
    // boxes are unambiguous stationary targets, and rejecting a quick contact
    // makes a normal tap disappear when it lands within one polling interval.
    if (_touch_samples == 0 || width <= 0 || height <= 0
        || (_touch_samples < 2 && split_selector == nullptr && navigation_bar == nullptr)) {
      return TouchAction::None;
    }

    const int dx = _last_x - _start_x;
    const int dy = _last_y - _start_y;
    const int abs_dx = magnitude(dx);
    const int abs_dy = magnitude(dy);
    const int horizontal_threshold = width / 8 > 8 ? width / 8 : 8;
    const int vertical_threshold = height / 8 > 8 ? height / 8 : 8;

    // Explicit header/footer controls are tap targets, not swipe surfaces.
    // Gestures begun in the body may cross them without activating a button.
    const bool in_reader_chrome = navigation_bar != nullptr
        && (_start_y < navigation_bar->exit_height
            || (_start_y >= navigation_bar->top
                && _start_y < navigation_bar->top + navigation_bar->height));

    if (abs_dx >= abs_dy && abs_dx >= horizontal_threshold) {
      if (in_reader_chrome) return TouchAction::None;
      return swipeAction(dx < 0);
    }
    if (abs_dy > abs_dx && abs_dy >= vertical_threshold) {
      if (in_reader_chrome) return TouchAction::None;
      return verticalSwipeAction(dy < 0);
    }

    // Some panels report a mirrored X axis. Swipe direction has its own
    // independent policy because gesture direction and stationary screen
    // coordinates are different concerns. Apply the panel correction only
    // after movement has been ruled out so it cannot alter swipe detection.
    const int tap_x = _mirror_tap_x ? width - 1 - _start_x : _start_x;

    // Explicit reader controls take priority over the older, broad bottom
    // channel-selector zone. Swipes still win over every stationary target.
    if (navigation_bar != nullptr) {
      if (tap_x < 0 || tap_x >= width || _start_y < 0 || _start_y >= height)
        return TouchAction::None;
      if (_start_y < navigation_bar->exit_height)
        return _last_y >= 0 && _last_y < navigation_bar->exit_height
            ? TouchAction::Select : TouchAction::None;
      if (_start_y >= navigation_bar->top
          && _start_y < navigation_bar->top + navigation_bar->height) {
        if (_last_y < navigation_bar->top
            || _last_y >= navigation_bar->top + navigation_bar->height)
          return TouchAction::None;
        const int end_x = _mirror_tap_x ? width - 1 - _last_x : _last_x;
        if (end_x < 0 || end_x >= width) return TouchAction::None;
        const TouchAction actions[] = {TouchAction::VerticalPrevious,
            TouchAction::Previous, TouchAction::Next,
            TouchAction::VerticalNext, TouchAction::Select};
        for (int cell = 0; cell < 5; ++cell) {
          const int left = width * cell / 5;
          const int right = width * (cell + 1) / 5;
          if (tap_x < right)
            return end_x >= left && end_x < right ? actions[cell] : TouchAction::None;
        }
      }
      // Missing an arrow must never fall through to the ordinary center tap
      // (Select), which closes the reader. Body halves move pages instead.
      if (_touch_samples < 2) return TouchAction::None;
      return tap_x < width / 2 ? TouchAction::Previous : TouchAction::Next;
    }
    // A quick single sample is accepted only inside an explicit target.
    if (_touch_samples < 2 && split_selector == nullptr) return TouchAction::None;

    // Message screens may reserve the otherwise empty ends of their bottom
    // status bar as forgiving arrow buttons. Keep the label in the middle
    // inert so an imprecise arrow tap cannot accidentally close the screen.
    if (bottom_selector && _separate_vertical_swipes
        && _start_y >= (height * 3) / 4) {
      if (tap_x < width / 4) {
        return TouchAction::VerticalPrevious;
      }
      if (tap_x >= (width * 3) / 4) {
        return TouchAction::VerticalNext;
      }
      return TouchAction::None;
    }

    // A two-choice page can turn stationary taps into explicit left/right
    // selections. Swipe detection above retains priority, so page navigation
    // gestures cannot accidentally activate either choice.
    if (split_selector != nullptr) {
      if (tap_x >= 0 && tap_x < split_selector->side_nav_width)
        return TouchAction::Previous;
      if (tap_x >= width - split_selector->side_nav_width && tap_x < width)
        return TouchAction::Next;
      if (_start_y < split_selector->top_y
          || _start_y >= split_selector->top_y + split_selector->height) {
        return TouchAction::None;
      }
      if (tap_x >= split_selector->left_x
          && tap_x < split_selector->left_x
              + split_selector->left_width) {
        return TouchAction::SelectLeft;
      }
      if (tap_x >= split_selector->right_x
          && tap_x < split_selector->right_x
              + split_selector->right_width) {
        return TouchAction::SelectRight;
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
    if (tap_x < center_left) return TouchAction::Previous;
    if (tap_x >= center_right) return TouchAction::Next;
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
