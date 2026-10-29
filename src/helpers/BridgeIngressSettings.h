#pragma once

#include "BridgeIngressProfile.h"
#include <stddef.h>

#if MESH_ENABLE_BRIDGE_INGRESS
#include "IdentityStore.h"

namespace mesh { namespace bridge {

// Separate from /com_prefs so old firmware and existing bridge frames keep
// their original layouts. Missing settings retain primary-radio ingress.
void loadIngressSettings(FILESYSTEM* fs);
bool handleIngressCommand(const char* command, char* reply, size_t capacity);

}} // namespace mesh::bridge
#endif
