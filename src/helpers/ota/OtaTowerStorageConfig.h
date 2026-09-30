#pragma once
#if defined(NRF52_PLATFORM) && defined(OTA_SD_DUAL_STORE)
#include <helpers/IdentityStore.h>

namespace mesh { namespace ota {
// Saved in internal LittleFS, not on the optional/removable card. Selection
// stays fixed until reboot so an active transfer can never change its source.
void beginTowerStorageConfig(FILESYSTEM* fs);
bool towerSdEnabledAtBoot();
bool towerSdConfigured();
bool setTowerSdEnabled(bool enabled);
bool handleTowerSdCommand(const char* value, char* reply, size_t capacity);
} }
#endif
