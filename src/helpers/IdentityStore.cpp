#include "IdentityStore.h"

#include "FilePresence.h"
#if defined(NRF52_PLATFORM) || defined(STM32_PLATFORM)
#include "AtomicFileWriter.h"
#else
#include "ContactFileTransaction.h"
#endif

bool IdentityStore::recover(const char* name) {
#if defined(ESP32_PLATFORM) || defined(RP2040_PLATFORM)
  char filename[40], backup[48];
  if (snprintf(filename, sizeof(filename), "%s/%s.id", _dir, name)
      >= (int)sizeof(filename)) return false;
  snprintf(backup, sizeof(backup), "%s.bak", filename);
  bool primary_exists = false, backup_exists = false;
  if (!mesh::filePresence(_fs, filename, primary_exists)
      || !mesh::filePresence(_fs, backup, backup_exists)) return false;
  if (!primary_exists && backup_exists) return _fs->rename(backup, filename);
#else
  (void)name;
#endif
  return true;
}

bool IdentityStore::load(const char *name, mesh::LocalIdentity& id) {
  if (!recover(name)) return false;
  bool loaded = false;
  char filename[40];
  sprintf(filename, "%s/%s.id", _dir, name);
  if (_fs->exists(filename)) {
#if defined(RP2040_PLATFORM)
    File file = _fs->open(filename, "r");
#else
    File file = _fs->open(filename);
#endif
    if (file) {
      loaded = id.readFrom(file);
      file.close();
    }
  }
  return loaded;
}

bool IdentityStore::load(const char *name, mesh::LocalIdentity& id, char display_name[], int max_name_sz) {
  if (!recover(name)) return false;
  bool loaded = false;
  char filename[40];
  sprintf(filename, "%s/%s.id", _dir, name);
  if (_fs->exists(filename)) {
#if defined(RP2040_PLATFORM)
    File file = _fs->open(filename, "r");
#else
    File file = _fs->open(filename);
#endif
    if (file) {
      loaded = id.readFrom(file);

      int n = max_name_sz;   // up to 32 bytes
      if (n > 32) n = 32;
      file.read((uint8_t *) display_name, n);
      display_name[n - 1] = 0;  // ensure null terminator

      file.close();
    }
  }
  return loaded;
}

bool IdentityStore::save(const char *name, const mesh::LocalIdentity& id) {
  char filename[40];
  sprintf(filename, "%s/%s.id", _dir, name);

  if (!recover(name)) return false;
  uint8_t key_data[PRV_KEY_SIZE + PUB_KEY_SIZE];
  if (id.writeTo(key_data, sizeof(key_data)) != sizeof(key_data)) return false;

  // LocalIdentity's byte-buffer export is private-key then public-key, while
  // the historical file format is public-key then private-key.
#if defined(NRF52_PLATFORM) || defined(STM32_PLATFORM)
  mesh::AtomicFileWriter writer(_fs, filename);
#else
  mesh::ContactFileTransaction writer(_fs, filename, mesh::filePresence<FILESYSTEM>);
#endif
  const bool wrote = writer
      && writer.write(&key_data[PRV_KEY_SIZE], PUB_KEY_SIZE) == PUB_KEY_SIZE
      && writer.write(key_data, PRV_KEY_SIZE) == PRV_KEY_SIZE;
  const bool success = writer.commit(wrote);
  MESH_DEBUG_PRINTLN("IdentityStore::save() atomic write - %s", success ? "OK" : "Err");
  return success;
}

bool IdentityStore::save(const char *name, const mesh::LocalIdentity& id, const char display_name[]) {
  char filename[40];
  sprintf(filename, "%s/%s.id", _dir, name);

  if (!recover(name)) return false;
  uint8_t key_data[PRV_KEY_SIZE + PUB_KEY_SIZE];
  if (id.writeTo(key_data, sizeof(key_data)) != sizeof(key_data)) return false;
  uint8_t display_data[32];
  memset(display_data, 0, sizeof(display_data));
  size_t display_len = strlen(display_name);
  if (display_len > sizeof(display_data) - 1) display_len = sizeof(display_data) - 1;
  memcpy(display_data, display_name, display_len);

#if defined(NRF52_PLATFORM) || defined(STM32_PLATFORM)
  mesh::AtomicFileWriter writer(_fs, filename);
#else
  mesh::ContactFileTransaction writer(_fs, filename, mesh::filePresence<FILESYSTEM>);
#endif
  const bool wrote = writer
      && writer.write(&key_data[PRV_KEY_SIZE], PUB_KEY_SIZE) == PUB_KEY_SIZE
      && writer.write(key_data, PRV_KEY_SIZE) == PRV_KEY_SIZE
      && writer.write(display_data, sizeof(display_data)) == sizeof(display_data);
  return writer.commit(wrote);
}
