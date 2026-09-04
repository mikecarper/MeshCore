#pragma once

#if defined(NRF52_PLATFORM) || defined(STM32_PLATFORM)
#include <InternalFileSystem.h>
#endif

namespace mesh {

// Adafruit LittleFS has no default File constructor: even a closed handle
// needs a valid filesystem owner. Its owner-only constructor does no I/O, so
// InternalFS is a safe fallback when the requested filesystem is unavailable.
template <typename Filesystem>
auto emptyFile(Filesystem* fs)
#if defined(RP2040_PLATFORM)
    -> decltype(fs->open(static_cast<const char*>(nullptr), "r")) {
  using FileType = decltype(fs->open(static_cast<const char*>(nullptr), "r"));
#else
    -> decltype(fs->open(static_cast<const char*>(nullptr))) {
  using FileType = decltype(fs->open(static_cast<const char*>(nullptr)));
#endif
#if defined(NRF52_PLATFORM) || defined(STM32_PLATFORM)
  if (fs != nullptr) return FileType(*fs);
  return FileType(InternalFS);
#else
  (void)fs;
  return FileType();
#endif
}

// Arduino-ESP32 SPIFFS can return a truthy directory handle when a missing
// pathname is opened for reading. A state-file reader must not interpret that
// zero-length directory as a valid empty file. Keep real directory enumeration
// on the filesystem's ordinary open() API.
template <typename Filesystem>
auto openFileRead(Filesystem* fs, const char* path)
#if defined(RP2040_PLATFORM)
    -> decltype(fs->open(path, "r")) {
  using FileType = decltype(fs->open(path, "r"));
#else
    -> decltype(fs->open(path)) {
  using FileType = decltype(fs->open(path));
#endif
  if (fs == nullptr || path == nullptr || !fs->exists(path)) return emptyFile(fs);
#if defined(RP2040_PLATFORM)
  FileType file = fs->open(path, "r");
#else
  FileType file = fs->open(path);
#endif
  if (file && file.isDirectory()) file.close();
  return file;
}

}  // namespace mesh
