#pragma once

#include <stdint.h>

// Keep runtime preference writes scoped to the file that owns the changed
// value. Observer builds have two independently durable images:
//
//   Common   -> /com_prefs  (radio, identity-facing settings, admin password)
//   Observer -> /mqtt_prefs (WiFi, MQTT, timezone, SNMP, alerts)
//
// Cross-file migration and the mixed-owner bridge.source setter need Both.
// Rewriting both images for every other CLI setter adds a second verified
// write/rename transaction and can exceed config.meshcore.io's five-second
// serial command deadline.
namespace PrefsSaveRouting {

enum class Scope : uint8_t {
  Common = 0,
  Observer,
  Both,
};

struct Plan {
  bool common;
  bool observer;
};

constexpr Plan planFor(Scope scope) {
  return scope == Scope::Common ? Plan{true, false}
       : scope == Scope::Observer ? Plan{false, true}
       : Plan{true, true};
}

}  // namespace PrefsSaveRouting
