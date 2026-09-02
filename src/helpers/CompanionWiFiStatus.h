#pragma once

#include <stdint.h>

namespace mesh {
namespace wifi {

// Runtime context supplied when shared WebConfig code describes a Companion-
// owned station. A stopped WebConfig session does not mean that station is off.
struct CompanionWiFiRuntimeState {
  bool requested;
  bool active;
  bool credential_reload_pending;
};

enum class CompanionWiFiFallbackState : uint8_t {
  OffDisabled,
  Inactive,
  ReconnectScheduled,
  ConnectingOrRetrying,
};

inline CompanionWiFiFallbackState classifyCompanionWiFiFallback(
    const CompanionWiFiRuntimeState& state) {
  if (!state.requested) return CompanionWiFiFallbackState::OffDisabled;
  if (!state.active) return CompanionWiFiFallbackState::Inactive;
  if (state.credential_reload_pending) {
    return CompanionWiFiFallbackState::ReconnectScheduled;
  }
  return CompanionWiFiFallbackState::ConnectingOrRetrying;
}

}  // namespace wifi
}  // namespace mesh
