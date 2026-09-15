#if defined(ENABLE_OTA) && (defined(COMPANION_RADIO_FULL) || COMPANION_FEATURE_OTA_CLI)
#include "CompanionOtaConfig.h"
#include <helpers/PersistentStoreFormat.h>
#include <stdio.h>
#if defined(ESP32_PLATFORM)
#include <errno.h>
#include <sys/stat.h>
#endif

namespace mesh { namespace ota {
namespace {
FILESYSTEM* settings_fs = nullptr;
bool held = false;
constexpr size_t ImageSize = 16 + MAX_OTA_SIGNERS * 32;
constexpr size_t CrcOffset = ImageSize - 4;
const char* const Path = "/ota_config";
const char* const Temp = "/ota_config.tmp";
const char* const Backup = "/ota_config.bak";

// Some FS::exists implementations open the file and report an unreadable
// existing image as absent. Metadata errors must not authorize replacement.
bool pathPresence(const char* path, bool& present) {
#if defined(NRF52_PLATFORM) || defined(STM32_PLATFORM)
  struct lfs_info info;
  settings_fs->_lockFS();
  const int result = lfs_stat(settings_fs->_getFS(), path, &info);
  settings_fs->_unlockFS();
  if (result != 0 && result != LFS_ERR_NOENT) return false;
  present = result == 0;
#elif defined(ESP32_PLATFORM)
  char vfs_path[48];
  const int length = snprintf(vfs_path, sizeof(vfs_path), "/spiffs%s", path);
  if (length < 0 || static_cast<size_t>(length) >= sizeof(vfs_path)) return false;
  struct stat info;
  const int result = ::stat(vfs_path, &info);
  if (result != 0 && errno != ENOENT) return false;
  present = result == 0;
#else
  // Arduino-Pico exposes only a boolean metadata result. It can distinguish
  // file-open failures below, but not missing metadata from metadata I/O.
  present = settings_fs->exists(path);
#endif
  return true;
}

bool removeIfPresent(const char* path) {
  bool present;
  return pathPresence(path, present) && (!present || settings_fs->remove(path));
}

bool validState(const OtaConfigState& state) {
  return state.autofetch <= 2 && state.autoinstall <= 1 && state.hops <= 8
      && state.checkpoint <= 4096 && state.advert <= 10080;
}

bool decode(const uint8_t* image, OtaConfigState& state) {
  if (memcmp(image, "OC\1\0", 4) || image[7] > MAX_OTA_SIGNERS
      || storage::readLE32(image + CrcOffset)
          != storage::updateCRC32(0xffffffffU, image, CrcOffset)) return false;
  OtaConfigState candidate;
  candidate.autofetch = image[4]; candidate.autoinstall = image[5];
  candidate.hops = image[6];
  candidate.checkpoint = storage::readLE16(image + 8);
  candidate.advert = storage::readLE16(image + 10);
  if (!validState(candidate)) return false;
  for (uint8_t i = 0; i < image[7]; ++i) candidate.allow.add(image + 12 + i * 32);
  if (candidate.allow.count() != image[7]) return false;
  state = candidate;
  return true;
}

bool readImage(const char* path, uint8_t* image, OtaConfigState& state) {
#if defined(NRF52_PLATFORM)
  File file(*settings_fs);
  if (!file.open(path, FILE_O_READ)) return false;
#elif defined(STM32_PLATFORM)
  File file = settings_fs->open(path, FILE_O_READ);
#else
  File file = settings_fs->open(path, "r");
#endif
  if (!file) return false;
  const bool complete = file.size() == ImageSize
      && file.read(image, ImageSize) == (int)ImageSize;
  file.close();
  return complete && decode(image, state);
}
}

void beginCompanionOtaConfig(FILESYSTEM* fs) {
  settings_fs = fs;
  held = false;
}

bool loadCompanionOtaConfig(OtaConfigState& state) {
  if (!settings_fs) return false;
  uint8_t image[ImageSize];
  bool primary = false, backup = false;
  if (!pathPresence(Path, primary)) { held = true; return false; }
  if (primary && readImage(Path, image, state)) return true;
  if (primary) held = true;
  if (!pathPresence(Backup, backup)) { held = true; return false; }
  if (backup && readImage(Backup, image, state)) {
    // Keep the verified previous settings even if recovery cannot rename them.
    // An unreadable primary may only have suffered a transient read failure;
    // do not remove it to promote the fallback.
    if (primary || !settings_fs->rename(Backup, Path)) held = true;
    return true;
  }
  if (primary || backup) {
    held = true;  // unreadable is not permission to overwrite with defaults
    return false;
  }
  state = OtaConfigState();
  return true;
}

bool saveCompanionOtaConfig(const OtaConfigState& state) {
  if (!settings_fs || held || !validState(state)) return false;
  uint8_t image[ImageSize] = {'O', 'C', 1, 0}, verify[ImageSize];
  OtaConfigState prior;
  bool had_primary = false;
  if (!pathPresence(Path, had_primary)) { held = true; return false; }
  if (had_primary && !readImage(Path, verify, prior)) {
    held = true;
    return false;
  }
  image[4] = state.autofetch; image[5] = state.autoinstall; image[6] = state.hops;
  image[7] = state.allow.count();
  storage::writeLE16(image + 8, state.checkpoint);
  storage::writeLE16(image + 10, state.advert);
  for (uint8_t i = 0; i < state.allow.count(); ++i)
    memcpy(image + 12 + i * 32, state.allow.get(i), 32);
  storage::writeLE32(image + CrcOffset,
                    storage::updateCRC32(0xffffffffU, image, CrcOffset));
  if (!removeIfPresent(Temp)) return false;
#if defined(NRF52_PLATFORM)
  File file(*settings_fs);
  if (!file.open(Temp, FILE_O_WRITE)) return false;
#elif defined(STM32_PLATFORM)
  File file = settings_fs->open(Temp, FILE_O_WRITE);
#else
  File file = settings_fs->open(Temp, "w");
#endif
  if (!file) return false;
  const bool wrote = file.write(image, sizeof(image)) == sizeof(image);
  file.flush(); file.close();
  if (!wrote || !readImage(Temp, verify, prior)
      || memcmp(image, verify, sizeof(image))) return false;
  if (!removeIfPresent(Backup)) return false;
  if (had_primary && !settings_fs->rename(Path, Backup)) return false;
  if (!settings_fs->rename(Temp, Path)) {
    if (had_primary && !settings_fs->rename(Backup, Path)) held = true;
    return false;
  }
  if (had_primary) settings_fs->remove(Backup);
  return true;
}

} }
#endif
