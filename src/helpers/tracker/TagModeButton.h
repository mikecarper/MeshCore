#pragma once

#include <stdint.h>

namespace mesh { namespace tracker {

// Poll from the application loop, not an interrupt. The caller owns the
// wake interrupt and must keep polling while needsPolling() is true. A wake
// from ordinary System ON sleep preserves this object and is not a new boot.
class TagModeButton {
 public:
  static constexpr uint32_t kDebounceMs = 25;
  static constexpr uint32_t kMaxClickMs = 700;
  static constexpr uint32_t kMultiClickMs = 500;

  // A button already held during startup or after state-losing wake is
  // ignored until it has been released and debounced. Do not call this on an
  // ordinary interrupt wake: doing so would discard a new press.
  void begin(bool pressed, uint32_t now) {
    initialized_ = true;
    stable_pressed_ = pressed;
    candidate_pressed_ = pressed;
    debouncing_ = false;
    suppress_until_release_ = pressed;
    click_count_ = 0;
    candidate_since_ = now;
    pressed_since_ = now;
  }

  // True exactly once when three debounced short presses are released, with
  // no more than 500 ms between consecutive releases. A long hold discards
  // any partial sequence and never fires this gesture.
  bool update(bool pressed, uint32_t now) {
    if (!initialized_) {
      begin(pressed, now);
      return false;
    }

    if (click_count_ && uint32_t(now - last_release_) > kMultiClickMs)
      click_count_ = 0;

    if (pressed == stable_pressed_) {
      debouncing_ = false;
    } else if (!debouncing_ || candidate_pressed_ != pressed) {
      candidate_pressed_ = pressed;
      candidate_since_ = now;
      debouncing_ = true;
    } else if (uint32_t(now - candidate_since_) >= kDebounceMs) {
      stable_pressed_ = pressed;
      debouncing_ = false;
      if (pressed) {
        // Measure from the start of the stable candidate, not the end of
        // debounce, so a 700 ms physical hold is never counted as a click.
        pressed_since_ = candidate_since_;
      } else {
        const bool short_press = !suppress_until_release_
            && uint32_t(candidate_since_ - pressed_since_) < kMaxClickMs;
        suppress_until_release_ = false;
        if (!short_press) {
          click_count_ = 0;
        } else {
          ++click_count_;
          last_release_ = now;
          if (click_count_ == 3) {
            click_count_ = 0;
            return true;
          }
        }
      }
    }
    return false;
  }

  bool needsPolling() const {
    return initialized_ && (debouncing_ || stable_pressed_ || click_count_ != 0);
  }

 private:
  bool initialized_ = false;
  bool stable_pressed_ = false;
  bool candidate_pressed_ = false;
  bool debouncing_ = false;
  bool suppress_until_release_ = false;
  uint8_t click_count_ = 0;
  uint32_t candidate_since_ = 0;
  uint32_t pressed_since_ = 0;
  uint32_t last_release_ = 0;
};

}} // namespace mesh::tracker
