#pragma once

#include "RadioDirectionPolicy.h"
#include <stddef.h>

#if MESH_ENABLE_RADIO_DIRECTION
#include "IdentityStore.h"

namespace mesh { namespace radioDirection {

// Separate from common preferences; older installations retain both directions.
void loadDirectionSettings(FILESYSTEM* fs);
bool handleDirectionCommand(const char* command, char* reply, size_t capacity);

}} // namespace mesh::radioDirection
#endif
