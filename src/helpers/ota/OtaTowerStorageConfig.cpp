#include "OtaTowerStorageConfig.h"
#if defined(NRF52_PLATFORM) && defined(OTA_SD_DUAL_STORE)
#include <helpers/PersistentStoreFormat.h>
#include <stdio.h>
#include <string.h>

namespace mesh { namespace ota {
namespace {
FILESYSTEM* settings = nullptr;
bool active = false;
bool configured = false;
constexpr const char* Path = "/ota_sd";
constexpr const char* Temp = "/ota_sd.tmp";

bool readImage(const char* path, uint8_t image[8]) {
  if (!settings) return false;
  File file(*settings);
  if (!file.open(path, FILE_O_READ)) return false;
  bool ok = file.size() == 8 && file.read(image, 8) == 8;
  file.close();
  return ok && image[0] == 'S' && image[1] == 'D' && image[2] == 1 && image[3] <= 1 &&
      storage::readLE32(image + 4) == storage::updateCRC32(0xffffffffU, image, 4);
}
}

void beginTowerStorageConfig(FILESYSTEM* fs) {
  settings = fs;
  uint8_t image[8];
  // Fresh installs retain the existing SD variant's behavior. Corruption or
  // filesystem errors fail closed; only a genuinely absent setting defaults on.
  struct lfs_info info;
  int result = LFS_ERR_IO;
  if (settings) {
    settings->_lockFS();
    result = lfs_stat(settings->_getFS(), Path, &info);
    settings->_unlockFS();
  }
  active = configured = result == LFS_ERR_NOENT ||
      (result == 0 && readImage(Path, image) && image[3] == 1);
}
bool towerSdEnabledAtBoot() { return active; }
bool towerSdConfigured() { return configured; }

bool setTowerSdEnabled(bool enabled) {
  if (!settings) return false;
  uint8_t image[8] = {'S', 'D', 1, static_cast<uint8_t>(enabled)};
  storage::writeLE32(image + 4, storage::updateCRC32(0xffffffffU, image, 4));
  if (settings->exists(Temp) && !settings->remove(Temp)) return false;
  File file(*settings);
  if (!file.open(Temp, FILE_O_WRITE)) return false;
  bool ok = file.write(image, sizeof(image)) == sizeof(image);
  file.flush();
  file.close();
  uint8_t verify[8];
  if (!ok || !readImage(Temp, verify) || memcmp(image, verify, sizeof(image))) return false;
  // LittleFS atomically replaces an existing file, keeping the old setting
  // authoritative if power is lost before the rename commits.
  settings->_lockFS();
  const int result = lfs_rename(settings->_getFS(), Temp, Path);
  settings->_unlockFS();
  if (result != 0) return false;
  configured = enabled;
  return true;
}

bool handleTowerSdCommand(const char* value, char* reply, size_t capacity) {
  if (!value[0]) {
    snprintf(reply, capacity, "SD use: active=%s saved=%s%s; off uses internal OTA and disables SD archive/card access",
             active ? "on" : "off", configured ? "on" : "off",
             active != configured ? " (reboot required)" : "");
  } else if (!strcmp(value, "on") || !strcmp(value, "off")) {
    const bool enabled = value[1] == 'n';
    if (!setTowerSdEnabled(enabled)) {
      snprintf(reply, capacity, "ERR SD setting could not be saved in internal flash");
    } else {
      snprintf(reply, capacity, "OK SD use %s saved; %s", enabled ? "on" : "off",
               active != configured ? "reboot to activate; current transfer unchanged" : "already active");
    }
  } else {
    return false;
  }
  return true;
}
} }
#endif
