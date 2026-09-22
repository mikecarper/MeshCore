#pragma once

#include <stdint.h>

#if defined(ESP32) || defined(RP2040_PLATFORM)
  #include <FS.h>
  #define FILESYSTEM  fs::FS
#elif defined(NRF52_PLATFORM) || defined(STM32_PLATFORM)
  #include <Adafruit_LittleFS.h>
  #define FILESYSTEM  Adafruit_LittleFS

  using namespace Adafruit_LittleFS_Namespace;
#endif
#include <Identity.h>

enum class IdentityLoadResult : uint8_t {
  Loaded,
  Missing,
  Unreadable,
};

class IdentityStore {
  FILESYSTEM* _fs;
  const char* _dir;
public:
  static constexpr unsigned IO_ATTEMPTS = 3;
  IdentityStore(FILESYSTEM& fs, const char* dir): _fs(&fs), _dir(dir) { }
  void useFileSystem(FILESYSTEM& fs) { _fs = &fs; }

  void begin() {
     if (_dir && _dir[0] == '/') { _fs->mkdir(_dir); } }
  // Recover an interrupted non-replacing rename before deciding this is a
  // fresh identity. False means the previous image must remain protected.
  bool recover(const char* name);
  IdentityLoadResult loadResult(const char *name, mesh::LocalIdentity& id);
  IdentityLoadResult loadResult(const char *name, mesh::LocalIdentity& id,
                                char display_name[], int max_name_sz);
  bool load(const char *name, mesh::LocalIdentity& id);
  bool load(const char *name, mesh::LocalIdentity& id, char display_name[], int max_name_sz);
  bool save(const char *name, const mesh::LocalIdentity& id);
  // Startup must not run with a new identity that was never made durable.
  bool saveWithRetry(const char *name, const mesh::LocalIdentity& id);
  bool save(const char *name, const mesh::LocalIdentity& id, const char display_name[]);
};
