#pragma once

#include <helpers/IdentityStore.h>
#include "OtaTiming.h"

namespace mesh { namespace ota {

// A small, separate settings image shared by every OTA-capable role. Reading
// or changing speed never borrows the Companion's OTA workspace.
void beginSpeedConfig(FILESYSTEM* fs);
float speedFactor();
void formatSpeed(char* text, size_t capacity);
bool handleSpeedCommand(const char* command, char* reply, size_t capacity);

} }
