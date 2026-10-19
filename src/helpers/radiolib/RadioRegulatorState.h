#pragma once

#include <helpers/RegulatorMode.h>

namespace mesh { namespace power {

// A boot selection never writes the radio. Once initialization starts, freeze
// it so recovery and temporary modem changes cannot apply a pending CLI edit.
class RadioRegulatorState {
  const uint8_t _modes;
  const RegulatorMode _default;
  RegulatorMode _requested = RegulatorMode::Default;
  RegulatorMode _effective = RegulatorMode::Default;
  RegulatorMode _active = RegulatorMode::Default;
  bool _started = false;

public:
  RadioRegulatorState(uint8_t modes, RegulatorMode default_mode)
      : _modes(modes), _default(default_mode) { }

  uint8_t modes() const { return _modes; }
  RegulatorMode defaultMode() const { return _default; }
  RegulatorMode activeMode() const { return _active; }

  bool configureBoot(RegulatorMode mode) {
    if (_started || !regulatorModeSupported(_modes, mode)) return false;
    _requested = mode;
    return true;
  }

  RegulatorMode beginInitialization(RegulatorMode legacy_default = RegulatorMode::Default) {
    _started = true;
    _active = RegulatorMode::Default; // no successful application in this init yet
    const RegulatorMode fallback = regulatorModeSupported(_modes, legacy_default)
        && legacy_default != RegulatorMode::Default ? legacy_default : _default;
    _effective = _requested == RegulatorMode::Default ? fallback : _requested;
    return _effective;
  }

  int16_t finishInitialization(int16_t status) {
    _active = status == 0 ? _effective : RegulatorMode::Default;
    return status;
  }
};

}} // namespace mesh::power
