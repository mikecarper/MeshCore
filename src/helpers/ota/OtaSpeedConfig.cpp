#if defined(ENABLE_OTA)
#include "OtaSpeedConfig.h"
#include <helpers/CLICommandUtils.h>
#include <helpers/PersistentStoreFormat.h>
#include <stdio.h>
#include <string.h>
#if defined(ESP32_PLATFORM)
#include <errno.h>
#include <sys/stat.h>
#endif

namespace mesh { namespace ota {
namespace {
FILESYSTEM* settings_fs = nullptr;
float factor = OTA_SPEED_DEFAULT;
bool held = false;
bool recoverable_corrupt_store = false;
constexpr size_t ImageSize = 12;
const char* const Path = "/ota_speed";
const char* const Temp = "/ota_speed.tmp";
const char* const Backup = "/ota_speed.bak";

enum class ImageReadResult : uint8_t { Missing, Valid, Invalid, Unreadable };

// A failed open is not enough to prove a path is absent: some filesystems
// report an I/O error as a failed exists() call.  Keep those images protected.
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
  present = settings_fs->exists(path);
#endif
  return true;
}

bool removeIfPresent(const char* path) {
  bool present = false;
  return pathPresence(path, present) && (!present || settings_fs->remove(path));
}

ImageReadResult readImage(const char* path, uint8_t* image) {
  bool present = false;
  if (!settings_fs || !pathPresence(path, present)) return ImageReadResult::Unreadable;
  if (!present) return ImageReadResult::Missing;
#if defined(NRF52_PLATFORM)
  File file(*settings_fs);
  if (!file.open(path, FILE_O_READ)) return ImageReadResult::Unreadable;
#elif defined(STM32_PLATFORM)
  File file = settings_fs->open(path, FILE_O_READ);
#else
  File file = settings_fs->open(path, "r");
#endif
  if (!file) return ImageReadResult::Unreadable;
  const bool right_size = file.size() == ImageSize;
  const bool complete = right_size && file.read(image, ImageSize) == (int)ImageSize;
  file.close();
  if (!right_size) return ImageReadResult::Invalid;
  if (!complete) return ImageReadResult::Unreadable;
  float value;
  memcpy(&value, image + 4, sizeof(value));
  return !memcmp(image, "OS\1\0", 4) && validSpeed(value)
      && storage::readLE32(image + 8) == storage::updateCRC32(0xffffffffU, image, 8)
      ? ImageReadResult::Valid : ImageReadResult::Invalid;
}

bool discardCorruptSavedImagesForWrite() {
  if (!held) return true;
  if (!settings_fs || !recoverable_corrupt_store) return false;
  // This is only reached from an explicit setter after both images were read
  // completely and rejected. OTA policy is independent of identity/key data.
  if (!removeIfPresent(Path) || !removeIfPresent(Backup)) return false;
  held = false;
  recoverable_corrupt_store = false;
  return true;
}

bool saveSpeed(float value) {
  if (!settings_fs || !validSpeed(value) || !discardCorruptSavedImagesForWrite()) return false;
  uint8_t image[ImageSize] = {'O', 'S', 1, 0};
  memcpy(image + 4, &value, sizeof(value));
  storage::writeLE32(image + 8, storage::updateCRC32(0xffffffffU, image, 8));
  if (settings_fs->exists(Temp) && !settings_fs->remove(Temp)) return false;
#if defined(NRF52_PLATFORM)
  File file(*settings_fs);
  if (!file.open(Temp, FILE_O_WRITE)) return false;
#elif defined(STM32_PLATFORM)
  File file = settings_fs->open(Temp, FILE_O_WRITE);
#else
  File file = settings_fs->open(Temp, "w");
#endif
  if (!file) return false;
  bool ok = file.write(image, sizeof(image)) == sizeof(image);
  file.flush(); file.close();
  uint8_t verify[ImageSize];
  if (!ok || readImage(Temp, verify) != ImageReadResult::Valid
      || memcmp(image, verify, sizeof(image))) return false;
  if (!removeIfPresent(Backup)) return false;
  bool had_primary = false;
  if (!pathPresence(Path, had_primary)) { held = true; return false; }
  if (had_primary && !settings_fs->rename(Path, Backup)) return false;
  if (!settings_fs->rename(Temp, Path)) {
    if (had_primary && !settings_fs->rename(Backup, Path)) held = true;
    return false;
  }
  if (had_primary) settings_fs->remove(Backup);
  factor = value;  // publish only after the complete image commits
  return true;
}
}

void beginSpeedConfig(FILESYSTEM* fs) {
  settings_fs = fs; factor = OTA_SPEED_DEFAULT; held = false;
  recoverable_corrupt_store = false;
  if (!fs) return;
  uint8_t image[ImageSize];
  const ImageReadResult primary = readImage(Path, image);
  bool loaded = primary == ImageReadResult::Valid;
  ImageReadResult backup = ImageReadResult::Missing;
  if (!loaded) backup = readImage(Backup, image);
  if (!loaded && backup == ImageReadResult::Valid) {
    // A verified backup is still authoritative when storage cannot repair the
    // rename gap. Keep its pace instead of silently reverting to the faster 1x.
    memcpy(&factor, image + 4, sizeof(factor));
    if (primary == ImageReadResult::Unreadable
        || (primary != ImageReadResult::Missing && !removeIfPresent(Path))) {
      held = true;
      return;
    }
    if (!fs->rename(Backup, Path)) { held = true; return; }
    loaded = true;
  }
  if (loaded) memcpy(&factor, image + 4, sizeof(factor));
  else {
    held = primary != ImageReadResult::Missing || backup != ImageReadResult::Missing;
    // Only an explicit later setter may recreate conclusively corrupt OTA
    // policy files. An unreadable file stays untouched for a clean reboot.
    recoverable_corrupt_store = held
        && primary != ImageReadResult::Unreadable
        && backup != ImageReadResult::Unreadable
        && (primary == ImageReadResult::Invalid || backup == ImageReadResult::Invalid);
  }
}

float speedFactor() { return factor; }

void formatSpeed(char* text, size_t capacity) {
  const uint32_t scaled = (uint32_t)(factor * 1000000.0f + 0.5f);
  snprintf(text, capacity, "%u.%06u", (unsigned)(scaled / 1000000), (unsigned)(scaled % 1000000));
  if (!capacity) return;
  size_t n = strlen(text);
  while (n && text[n - 1] == '0') text[--n] = 0;
  if (n && text[n - 1] == '.') text[n - 1] = 0;
}

bool handleSpeedCommand(const char* command, char* reply, size_t capacity) {
  const char* value = nullptr;
  bool require_value = false, read_only = false;
  const char* const forms[] = {"set ota.speed", "get ota.speed", "ota config speed", "ota cfg speed", "ota set speed", "ota speed"};
  for (size_t i = 0; i < sizeof(forms) / sizeof(forms[0]); ++i) {
    const size_t n = strlen(forms[i]);
    if (!strncmp(command, forms[i], n) && (command[n] == 0 || command[n] == ' ')) {
      value = command + n;
      while (*value == ' ') ++value;
      require_value = i == 0; read_only = i == 1;
      break;
    }
  }
  if (!value) return false;
  if ((require_value && !*value) || (read_only && *value)) {
    snprintf(reply, capacity, "ERR usage: set ota.speed <0.05..3> | get ota.speed");
    return true;
  }
  if (*value) {
    float parsed;
    if (!cli::parseDecimalStrict(value, parsed) || !validSpeed(parsed)) {
      snprintf(reply, capacity, "ERR ota.speed must be 0.05..3 (1=current speed)");
      return true;
    }
    if (!saveSpeed(parsed)) {
      snprintf(reply, capacity, "ERR ota.speed save failed; speed unchanged");
      return true;
    }
  }
  char number[16]; formatSpeed(number, sizeof(number));
  snprintf(reply, capacity, *value ? "OK ota.speed=%sx (saved)" : "> ota.speed=%sx", number);
  return true;
}

} }
#endif
