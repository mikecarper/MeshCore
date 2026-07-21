#include "IdentityStore.h"

#if defined(NRF52_PLATFORM)
#include "AtomicFileWriter.h"
#endif

bool IdentityStore::load(const char *name, mesh::LocalIdentity& id) {
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

#if defined(NRF52_PLATFORM)
  uint8_t key_data[PRV_KEY_SIZE + PUB_KEY_SIZE];
  if (id.writeTo(key_data, sizeof(key_data)) != sizeof(key_data)) return false;

  // LocalIdentity's byte-buffer export is private-key then public-key, while
  // the historical file format is public-key then private-key.
  mesh::AtomicFileWriter writer(_fs, filename);
  const bool wrote = writer
      && writer.write(&key_data[PRV_KEY_SIZE], PUB_KEY_SIZE) == PUB_KEY_SIZE
      && writer.write(key_data, PRV_KEY_SIZE) == PRV_KEY_SIZE;
  const bool success = writer.commit(wrote);
  MESH_DEBUG_PRINTLN("IdentityStore::save() atomic write - %s", success ? "OK" : "Err");
  return success;
#elif defined(STM32_PLATFORM)
  _fs->remove(filename);
  File file = _fs->open(filename, FILE_O_WRITE);
#elif defined(RP2040_PLATFORM)
  File file = _fs->open(filename, "w");
#else
  File file = _fs->open(filename, "w", true);
#endif
#if !defined(NRF52_PLATFORM)
  if (file) {
    bool success = id.writeTo(file);
    file.close();
    MESH_DEBUG_PRINTLN("IdentityStore::save() write - %s", success ? "OK" : "Err");
    return success;
  }
#endif
  MESH_DEBUG_PRINTLN("IdentityStore::save() failed");
  return false;
}

bool IdentityStore::save(const char *name, const mesh::LocalIdentity& id, const char display_name[]) {
  char filename[40];
  sprintf(filename, "%s/%s.id", _dir, name);

#if defined(NRF52_PLATFORM)
  uint8_t key_data[PRV_KEY_SIZE + PUB_KEY_SIZE];
  if (id.writeTo(key_data, sizeof(key_data)) != sizeof(key_data)) return false;
  uint8_t display_data[32];
  memset(display_data, 0, sizeof(display_data));
  size_t display_len = strlen(display_name);
  if (display_len > sizeof(display_data) - 1) display_len = sizeof(display_data) - 1;
  memcpy(display_data, display_name, display_len);

  mesh::AtomicFileWriter writer(_fs, filename);
  const bool wrote = writer
      && writer.write(&key_data[PRV_KEY_SIZE], PUB_KEY_SIZE) == PUB_KEY_SIZE
      && writer.write(key_data, PRV_KEY_SIZE) == PRV_KEY_SIZE
      && writer.write(display_data, sizeof(display_data)) == sizeof(display_data);
  return writer.commit(wrote);
#elif defined(STM32_PLATFORM)
  _fs->remove(filename);
  File file = _fs->open(filename, FILE_O_WRITE);
#elif defined(RP2040_PLATFORM)
  File file = _fs->open(filename, "w");
#else
  File file = _fs->open(filename, "w", true);
#endif
#if !defined(NRF52_PLATFORM)
  if (file) {
    bool success = id.writeTo(file);

    uint8_t tmp[32];
    memset(tmp, 0, sizeof(tmp));
    int n = strlen(display_name);
    if (n > sizeof(tmp)-1) n = sizeof(tmp)-1;
    memcpy(tmp, display_name, n);
    success = success && file.write(tmp, sizeof(tmp)) == sizeof(tmp);

    file.close();
    return success;
  }
#endif
  return false;
}
