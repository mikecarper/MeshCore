#include "MomentaryButton.h"

#define MULTI_CLICK_WINDOW_MS  280
#define BUTTON_DEBOUNCE_MS      25

#if defined(NRF52_PLATFORM) && \
    defined(MOMENTARY_BUTTON_WAKE_FROM_SLEEP) && \
    MOMENTARY_BUTTON_WAKE_FROM_SLEEP
namespace {

// nRF52 companion builds use sd_app_evt_wait() between loop iterations. The
// button state machine remains intentionally polling-based, but it still needs
// a GPIO event to wake that poller. Both edges matter: a falling edge records
// button-down and a rising edge records release. The companion loop stays
// awake while needsPolling() reports a gesture deadline so long/multi-click
// timing can complete without another GPIO edge.
void wakeMomentaryButtonPoller() {}

}  // namespace
#endif

MomentaryButton::MomentaryButton(int8_t pin, int long_press_millis, bool reverse, bool pulldownup, bool multiclick) { 
  _pin = pin;
  _reverse = reverse;
  _pull = pulldownup;
  down_at = 0; 
  prev = _reverse ? HIGH : LOW;
  cancel = 0;
  _long_millis = long_press_millis;
  _threshold = 0;
  _press_active = false;
  _click_count = 0;
  _last_click_time = 0;
  _multi_click_window = multiclick ? MULTI_CLICK_WINDOW_MS : 0;
  _pending_click = false;
  _candidate_level = prev;
  _candidate_since = 0;
  _debouncing = false;
}

MomentaryButton::MomentaryButton(int8_t pin, int long_press_millis, int analog_threshold) {
  _pin = pin;
  _reverse = false;
  _pull = false;
  down_at = 0;
  prev = LOW;
  cancel = 0;
  _long_millis = long_press_millis;
  _threshold = analog_threshold;
  _press_active = false;
  _click_count = 0;
  _last_click_time = 0;
  _multi_click_window = MULTI_CLICK_WINDOW_MS;
  _pending_click = false;
  _candidate_level = prev;
  _candidate_since = 0;
  _debouncing = false;
}

void MomentaryButton::begin() {
  if (_pin >= 0 && _threshold == 0) {
    pinMode(_pin, _pull ? (_reverse ? INPUT_PULLUP : INPUT_PULLDOWN) : INPUT);
#if defined(NRF52_PLATFORM) && \
    defined(MOMENTARY_BUTTON_WAKE_FROM_SLEEP) && \
    MOMENTARY_BUTTON_WAKE_FROM_SLEEP
    // The ISR deliberately owns no button logic; it only makes a sleeping
    // event-driven main loop run the normal debounced polling state machine.
    attachInterrupt((uint32_t)_pin, wakeMomentaryButtonPoller, CHANGE);
#endif
  }
}

bool  MomentaryButton::isPressed() const {
  int btn = _threshold > 0 ? (analogRead(_pin) < _threshold) : digitalRead(_pin);
  return isPressed(btn);
}

void MomentaryButton::cancelClick() {
  cancel = 1;
  down_at = 0;
  _press_active = false;
  _click_count = 0;
  _last_click_time = 0;
  _pending_click = false;
}

bool MomentaryButton::isPressed(int level) const {
  if (_threshold > 0) {
    return level;
  }
  if (_reverse) {
    return level == LOW;
  } else {
    return level != LOW;
  }
}

int MomentaryButton::check(bool repeat_click) {
  if (_pin < 0) return BUTTON_EVENT_NONE;

  int event = BUTTON_EVENT_NONE;
  const uint32_t now = millis();
  const int raw_btn =
      _threshold > 0 ? (analogRead(_pin) < _threshold) : digitalRead(_pin);

  // Treat prev as the stable electrical level. A raw transition must remain
  // unchanged for a short interval before it can alter gesture state. Without
  // this, one physical release can be counted two or three times and a short
  // click can silently turn into KEY_PREV or a triple-click action.
  int btn = prev;
  if (raw_btn == prev) {
    _debouncing = false;
  } else if (!_debouncing || raw_btn != _candidate_level) {
    _candidate_level = raw_btn;
    _candidate_since = now;
    _debouncing = true;
  } else if ((uint32_t)(now - _candidate_since) >= BUTTON_DEBOUNCE_MS) {
    btn = raw_btn;
    _debouncing = false;
  }

  if (btn != prev) {
    if (isPressed(btn)) {
      down_at = now;
      _press_active = true;
    } else {
      // button UP
      if (!cancel && _press_active && _long_millis > 0) {
        if ((uint32_t)(now - down_at) >= (uint32_t)_long_millis) {
          // Normally the polling loop reports this while the button is still
          // down. Also handle release-only polling so event-driven platforms
          // cannot lose a long press if no intermediate poll occurred.
          event = BUTTON_EVENT_LONG_PRESS;
          _click_count = 0;
          _last_click_time = 0;
          _pending_click = false;
        } else {
          _click_count++;
          _last_click_time = now;
          _pending_click = true;
        }
      } else if (!cancel && _press_active) {
        _click_count++;
        _last_click_time = now;
        _pending_click = true;
      }
      _press_active = false;
      down_at = 0;
    }
    prev = btn;
  }
  if (!isPressed(btn) && cancel) {   // always clear the pending 'cancel' once button is back in UP state
    cancel = 0;
  }

  if (_long_millis > 0 && _press_active
      && (uint32_t)(now - down_at) >= (uint32_t)_long_millis) {
    if (_pending_click) {
      // long press during multi-click detection - cancel pending clicks
      cancelClick();
    } else {
      event = BUTTON_EVENT_LONG_PRESS;
      _press_active = false;
      down_at = 0;
      _click_count = 0;
      _last_click_time = 0;
      _pending_click = false;
    }
  }
  if (_press_active && repeat_click) {
    uint32_t diff = (uint32_t)(now - down_at);
    if (diff >= 700) {
      event = BUTTON_EVENT_CLICK;   // wait 700 millis before repeating the click events
    }
  }

  if (_pending_click
      && (uint32_t)(now - _last_click_time) >= (uint32_t)_multi_click_window) {
    // A press candidate first observed before the deadline may legitimately
    // become the next click even if its debounce interval crosses the window.
    // A candidate first observed at/after the deadline must not revive and
    // merge an already-expired sequence.
    const bool press_candidate_in_window = _debouncing
        && isPressed(_candidate_level)
        && (uint32_t)(_candidate_since - _last_click_time)
            < (uint32_t)_multi_click_window;
    if (press_candidate_in_window || _press_active) {
      // still pressed - wait for button release before processing clicks
      return event;
    }
    switch (_click_count) {
      case 1:
        event = BUTTON_EVENT_CLICK;
        break;
      case 2:
        event = BUTTON_EVENT_DOUBLE_CLICK;
        break;
      case 3:
        event = BUTTON_EVENT_TRIPLE_CLICK;
        break;
      default:
        // For 4+ clicks, treat as triple click?
        event = BUTTON_EVENT_TRIPLE_CLICK;
        break;
    }
    _click_count = 0;
    _last_click_time = 0;
    _pending_click = false;
  }

  return event;
}
