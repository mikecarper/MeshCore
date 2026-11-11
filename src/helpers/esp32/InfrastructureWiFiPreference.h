#pragma once

#include <Preferences.h>
#include <stdint.h>

namespace mesh {
namespace wifi {

// Infrastructure roles share one master policy without changing their binary
// NodePrefs layout or the saved preferences of individual WiFi services.
struct InfrastructureWiFiPreference {
  bool loaded = false;
  bool healthy = false;
  bool enabled = false;
};

inline InfrastructureWiFiPreference& infrastructureWiFiPreferenceState() {
  static InfrastructureWiFiPreference state;
  return state;
}

inline bool loadInfrastructureWiFiPreference() {
  auto& state = infrastructureWiFiPreferenceState();
  if (state.loaded) return state.healthy;
  state.loaded = true;
  Preferences nvs;
  // Opening read/write makes a fresh namespace a quiet, ordinary first boot.
  // Unavailable/corrupt storage must not revive a previously disabled radio.
  if (!nvs.begin("mesh-wifi", false)) return false;
  const bool present = nvs.isKey("infra_enabled");
  const uint8_t value = present && nvs.getType("infra_enabled") == PT_U8
      ? nvs.getUChar("infra_enabled", 255) : present ? 255 : 1;
  nvs.end();
  state.healthy = value <= 1;
  state.enabled = state.healthy && value != 0;
  return state.healthy;
}

inline bool infrastructureWiFiEnabled() {
  loadInfrastructureWiFiPreference();
  return infrastructureWiFiPreferenceState().enabled;
}

inline bool saveInfrastructureWiFiEnabled(bool enabled) {
  loadInfrastructureWiFiPreference();
  auto& state = infrastructureWiFiPreferenceState();
  if (state.healthy && state.enabled == enabled) return true;
  Preferences nvs;
  if (!nvs.begin("mesh-wifi", false)) return false;
  const bool present = nvs.isKey("infra_enabled");
  const bool old_typed = present && nvs.getType("infra_enabled") == PT_U8;
  const uint8_t previous = old_typed ? nvs.getUChar("infra_enabled", 255) : 255;
  const uint8_t value = enabled ? 1 : 0;
  // One atomic NVS value, verified before the CLI acknowledges persistence.
  const bool saved = nvs.putUChar("infra_enabled", value) == 1
      && nvs.getType("infra_enabled") == PT_U8
      && nvs.getUChar("infra_enabled", 255) == value;
  if (!saved) {
    // A write can report failure after changing its value. Restore the old
    // setting; an invalid old image rolls back to the safe disabled policy.
    if (!present) nvs.remove("infra_enabled");
    else nvs.putUChar("infra_enabled", previous <= 1 ? previous : 0);
  }
  nvs.end();
  if (!saved) return false;
  state.enabled = enabled;
  state.healthy = true;
  return true;
}

} // namespace wifi
} // namespace mesh
