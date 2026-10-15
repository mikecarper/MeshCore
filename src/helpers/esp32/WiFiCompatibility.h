#pragma once

#include <stdint.h>
#include <string.h>
#if defined(ESP32)
#include <Preferences.h>
#endif

namespace mesh {
namespace wifi {

enum class StationProtocol : uint8_t { Bgn = 0, Bg = 1 };
enum class CompanionMode : uint8_t { Normal = 0, WiFiOnly = 1 };
enum class CompatibilitySaveResult : uint8_t {
  Saved, InvalidValue, RadioConflict, StorageError
};
using StationProtocolSaveResult = CompatibilitySaveResult;
using CompanionModeSaveResult = CompatibilitySaveResult;

namespace compatibility_detail {
template <typename Value> struct BootPreference {
  bool loaded = false;
  bool load_ok = false;
  Value active = static_cast<Value>(0);
  Value configured = static_cast<Value>(0);
};

inline BootPreference<StationProtocol>& protocolPreference() {
  static BootPreference<StationProtocol> state;
  return state;
}

inline BootPreference<CompanionMode>& modePreference() {
  static BootPreference<CompanionMode> state;
  return state;
}

#if defined(ESP32)
template <typename Value>
inline bool loadForBoot(const char* key, BootPreference<Value>& state,
                        bool reject_nondefault = false) {
  if (state.loaded) return state.load_ok;
  Preferences prefs;
  const bool opened = prefs.begin("mesh-wifi", false);
  uint8_t stored = 0;
  if (opened) {
    stored = prefs.getUChar(key, 0);
    prefs.end();
  }
  if (stored > 1 || reject_nondefault) stored = 0;
  state.active = state.configured = static_cast<Value>(stored);
  // Freeze even an unavailable/corrupt store to the safe default this boot.
  state.loaded = true;
  state.load_ok = opened;
  return opened;
}

template <typename Value>
inline CompatibilitySaveResult save(const char* key, Value value,
                                     BootPreference<Value>& state) {
  if (static_cast<uint8_t>(value) > 1) {
    return CompatibilitySaveResult::InvalidValue;
  }
  if (!state.loaded) return CompatibilitySaveResult::StorageError;
  Preferences prefs;
  if (!prefs.begin("mesh-wifi", false)) {
    return CompatibilitySaveResult::StorageError;
  }
  const uint8_t stored = static_cast<uint8_t>(value);
  const bool saved = prefs.putUChar(key, stored) == sizeof(uint8_t)
      && prefs.getUChar(key, 255) == stored;
  if (!saved) {
    // A failed verification must not knowingly leave the requested mode saved.
    prefs.putUChar(key, static_cast<uint8_t>(state.configured));
  }
  prefs.end();
  if (!saved) return CompatibilitySaveResult::StorageError;
  state.configured = value;
  return CompatibilitySaveResult::Saved;
}
#endif
} // namespace compatibility_detail

// Reads are deliberately NVS-free: boot explicitly loads these before either
// radio starts; saving only changes the configuration for the next reboot.
inline StationProtocol activeStationProtocol() {
  return compatibility_detail::protocolPreference().active;
}
inline StationProtocol configuredStationProtocol() {
  return compatibility_detail::protocolPreference().configured;
}
inline bool stationProtocolRestartPending() {
  return configuredStationProtocol() != activeStationProtocol();
}
inline const char* stationProtocolName(StationProtocol value) {
  return value == StationProtocol::Bg ? "bg" : "bgn";
}
inline bool parseStationProtocol(const char* value, StationProtocol* out) {
  if (value == nullptr || out == nullptr) return false;
  if (strcmp(value, "bgn") == 0) *out = StationProtocol::Bgn;
  else if (strcmp(value, "bg") == 0) *out = StationProtocol::Bg;
  else return false;
  return true;
}

inline CompanionMode activeCompanionMode() {
  return compatibility_detail::modePreference().active;
}
inline CompanionMode configuredCompanionMode() {
  return compatibility_detail::modePreference().configured;
}
inline bool companionModeRestartPending() {
  return configuredCompanionMode() != activeCompanionMode();
}
inline const char* companionModeName(CompanionMode value) {
  return value == CompanionMode::WiFiOnly ? "wifi-only" : "normal";
}
inline bool parseCompanionMode(const char* value, CompanionMode* out) {
  if (value == nullptr || out == nullptr) return false;
  if (strcmp(value, "normal") == 0) *out = CompanionMode::Normal;
  else if (strcmp(value, "wifi-only") == 0) *out = CompanionMode::WiFiOnly;
  else return false;
  return true;
}

#if defined(ESP32)
inline bool loadCompanionModeForBoot() {
  return compatibility_detail::loadForBoot(
      "wifi_only", compatibility_detail::modePreference());
}
inline CompatibilitySaveResult saveCompanionMode(CompanionMode value) {
  return compatibility_detail::save(
      "wifi_only", value, compatibility_detail::modePreference());
}
#endif

} // namespace wifi
} // namespace mesh
