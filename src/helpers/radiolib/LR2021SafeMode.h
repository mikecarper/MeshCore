#pragma once

#if defined(MESH_W12_PRAM_EXPERIMENT) && MESH_W12_PRAM_EXPERIMENT
#include <helpers/IdentityStore.h>
#include <stddef.h>

namespace mesh { namespace lr2021 {
// Fail closed until a confirmed persistent setting has been loaded.
bool safeMode();
void loadSafeModeSettings(FILESYSTEM* fs);
bool handleSafeModeCommand(const char* command, char* reply, size_t capacity);
} }
#endif
