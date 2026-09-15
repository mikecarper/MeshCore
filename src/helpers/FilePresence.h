#pragma once

#include <stdio.h>
#if defined(ESP32_PLATFORM)
#include <errno.h>
#include <sys/stat.h>
#endif

namespace mesh {
// Unlike ESP32 FS::exists(), this probe does not open the file. A failed read
// must not turn a previously committed image into an apparently fresh store.
template <typename Filesystem>
bool filePresence(Filesystem* fs, const char* path, bool& present) {
  if (fs == nullptr || path == nullptr) return false;
#if defined(ESP32_PLATFORM)
  char absolute[128];
  const int length = snprintf(absolute, sizeof(absolute), "/spiffs%s", path);
  if (length < 0 || static_cast<size_t>(length) >= sizeof(absolute)) return false;
  struct stat info;
  const int result = ::stat(absolute, &info);
  if (result != 0 && errno != ENOENT) return false;
  present = result == 0;
#elif defined(NRF52_PLATFORM) || defined(STM32_PLATFORM)
  struct lfs_info info;
  fs->_lockFS();
  const int result = lfs_stat(fs->_getFS(), path, &info);
  fs->_unlockFS();
  if (result != 0 && result != LFS_ERR_NOENT) return false;
  present = result == 0;
#else
  // Arduino-Pico exposes only boolean metadata status. Its exists() does not
  // open the file, but cannot distinguish absent files from metadata errors.
  present = fs->exists(path);
#endif
  return true;
}
} // namespace mesh
