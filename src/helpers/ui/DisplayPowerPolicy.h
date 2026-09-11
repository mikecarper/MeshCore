#pragma once

#include <stdint.h>
#include <string.h>

namespace mesh { namespace ui {

enum class DisplayMode : uint8_t { Off, On, Button, Pairing, ButtonPairing, Automatic };
enum class DisplayWake : uint8_t { Boot, Button, Message };
enum class DisplayInboxMode : uint8_t { History, Pending, Unread };

inline const char* displayInboxModeName(DisplayInboxMode mode) {
  switch (mode) {
    case DisplayInboxMode::Pending: return "pending";
    case DisplayInboxMode::Unread: return "unread";
    default: return "history";
  }
}

struct DisplayPowerProfile {
  DisplayMode mode = DisplayMode::Button;
  uint16_t seconds = 15;
};
struct DisplayPowerPrefs {
  DisplayPowerProfile battery;
  DisplayPowerProfile usb;
  DisplayInboxMode inbox = DisplayInboxMode::History;
};

inline DisplayPowerPrefs& displayPowerPrefs() {
  static DisplayPowerPrefs prefs;
  return prefs;
}
inline const char* displayModeName(DisplayMode mode) {
  switch (mode) {
    case DisplayMode::Off: return "off";
    case DisplayMode::On: return "on";
    case DisplayMode::Button: return "button";
    case DisplayMode::Pairing: return "pairing";
    case DisplayMode::ButtonPairing: return "button-pairing";
    case DisplayMode::Automatic: return "automatic";
  }
  return "off";
}
inline bool parseDisplayMode(const char* text, DisplayMode& mode) {
  for (uint8_t i = 0; i <= uint8_t(DisplayMode::Automatic); ++i) {
    if (strcmp(text, displayModeName(DisplayMode(i))) == 0) {
      mode = DisplayMode(i);
      return true;
    }
  }
  return false;
}
inline bool displayModeSupportsPairing(DisplayMode mode) {
  return mode == DisplayMode::Pairing || mode == DisplayMode::ButtonPairing;
}
inline bool validDisplayProfile(const DisplayPowerProfile& profile) {
  return uint8_t(profile.mode) <= uint8_t(DisplayMode::Automatic)
      && profile.seconds >= 1 && profile.seconds <= 3600;
}

// Pure, wrap-safe policy shared by every UI. Radio traffic, refreshes and USB
// presence are not user activity. An active pairing request has its own expiry.
class DisplayPowerPolicy {
  DisplayPowerProfile _profile;
  bool _initialized = false, _usb = false, _connected = false, _pairing = false;
  bool _timed = false;
  uint32_t _deadline = 0;
public:
  void update(const DisplayPowerPrefs& prefs, bool usb, bool connected,
              bool pairing, uint32_t now) {
    const auto& selected = usb ? prefs.usb : prefs.battery;
    const bool reset = !_initialized || _usb != usb || _profile.mode != selected.mode;
    const bool timeout_changed = _profile.seconds != selected.seconds;
    const bool just_connected = connected && !_connected;
    _profile = selected;
    _usb = usb;
    _connected = connected;
    _pairing = pairing;
    if (reset) {
      _timed = false;
      _initialized = true;
      wake(DisplayWake::Boot, now);
    } else if (timeout_changed && _timed) {
      _deadline = now + uint32_t(_profile.seconds) * 1000;
    }
    if (just_connected && _profile.mode == DisplayMode::Automatic) _timed = false;
    if (_timed && int32_t(now - _deadline) >= 0) _timed = false;
  }
  bool wake(DisplayWake reason, uint32_t now) {
    if (!_initialized) return false;
    bool allowed = _profile.mode == DisplayMode::On;
    if (reason == DisplayWake::Button) {
      allowed |= _profile.mode == DisplayMode::Button
          || _profile.mode == DisplayMode::ButtonPairing
          || _profile.mode == DisplayMode::Automatic;
    } else {
      allowed |= _profile.mode == DisplayMode::Automatic && !_connected;
    }
    if (!allowed) return false;
    _timed = true;
    uint32_t duration = uint32_t(_profile.seconds) * 1000;
    if (reason == DisplayWake::Boot && duration > 4000) duration = 4000;
    _deadline = now + duration;
    return true;
  }
  void dismiss() { _timed = false; }
  bool on() const {
    if (!_initialized || _profile.mode == DisplayMode::Off) return false;
    if (_profile.mode == DisplayMode::On) return true;
    if (_pairing && (_profile.mode == DisplayMode::Automatic
        || displayModeSupportsPairing(_profile.mode))) return true;
    return _profile.mode != DisplayMode::Pairing && _timed;
  }
};

} }
