#pragma once

#include <helpers/IdentityStore.h>
#include "DisplayPowerPolicy.h"

namespace mesh { namespace ui {

// Separate, versioned file: existing node and MQTT preference layouts stay
// readable by older firmware. Returns true when a saved profile was loaded.
bool loadDisplayPowerSettings(FILESYSTEM* fs, bool pairing_supported);
bool handleDisplayPowerCommand(const char* command, char* reply, size_t reply_size);
bool displayPairingSupported();
void migrateLegacyDisplayTimeout(uint16_t seconds);

} }
