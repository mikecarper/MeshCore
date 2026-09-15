#pragma once

#include <helpers/IdentityStore.h>
#include "OtaConfigState.h"

namespace mesh { namespace ota {

// Separate from /new_prefs: preserve compatibility with existing Companions.
// Initializing does not allocate an OTA context. Dynamic contexts load on use.
void beginCompanionOtaConfig(FILESYSTEM* fs);
bool loadCompanionOtaConfig(OtaConfigState& state);
bool saveCompanionOtaConfig(const OtaConfigState& state);

} }
