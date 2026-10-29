#pragma once

#if defined(WITH_ESPNOW_BRIDGE)
#include "IdentityStore.h"
#include <stddef.h>
#include <stdint.h>

namespace mesh { namespace bridge {

// Separate from NodePrefs and the bridge wire format. Missing settings keep
// legacy broadcast delivery; an unreadable saved destination blocks startup.
void loadEspNowPeerSettings(FILESYSTEM* fs);
bool espNowPeerSettingsHealthy();
bool copyEspNowPeer(uint8_t destination[6]);
bool parseEspNowPeer(const char* text, uint8_t destination[6]);
bool saveEspNowPeer(const uint8_t destination[6]);
void formatEspNowPeer(char* reply, size_t capacity);

}} // namespace mesh::bridge
#endif
