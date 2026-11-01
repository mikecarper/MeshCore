#pragma once

#include <helpers/IdentityStore.h>
#include "DisplayPowerPolicy.h"
#include "DisplayBuildFlags.h"

namespace mesh { namespace ui {

// Separate, versioned file: existing node and MQTT preference layouts stay
// readable by older firmware. Returns true when a saved profile was loaded.
// Companion startup enables a timed boot screen without changing saved modes.
// Roles arm first_install_boot after a missing identity is saved successfully;
// the initial setup window is runtime-only and is never written to this file.
// Headless builds initialize runtime defaults and never access this file.
// A detected display can override the fresh-install timeout; saved profiles win.
bool loadDisplayPowerSettings(FILESYSTEM* fs, bool pairing_supported,
                              bool wake_on_boot = false, uint16_t default_timeout = 0);
bool handleDisplayPowerCommand(const char* command, char* reply, size_t reply_size,
                               bool inbox_supported = false);
bool displayPairingSupported();
void migrateLegacyDisplayTimeout(uint16_t seconds);

} }
