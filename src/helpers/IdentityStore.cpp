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
  return loadResult(name, id) == IdentityLoadResult::Loaded;
}

bool IdentityStore::load(const char *name, mesh::LocalIdentity& id, char display_name[], int max_name_sz) {
  return loadResult(name, id, display_name, max_name_sz)
      == IdentityLoadResult::Loaded;
}

IdentityLoadResult IdentityStore::loadResult(
    const char *name, mesh::LocalIdentity& id) {
  return loadResult(name, id, nullptr, 0);
}

IdentityLoadResult IdentityStore::loadResult(
    const char *name, mesh::LocalIdentity& id, char display_name[],
    int max_name_sz) {
  char filename[40];
  if (snprintf(filename, sizeof(filename), "%s/%s.id", _dir, name)
      >= (int)sizeof(filename)) return IdentityLoadResult::Unreadable;
  for (unsigned attempt = 0; attempt < IO_ATTEMPTS; ++attempt) {
    if (!recover(name)) continue;
    bool present = false;
    if (!mesh::filePresence(_fs, filename, present)) continue;
    if (!present) return IdentityLoadResult::Missing;
#if defined(RP2040_PLATFORM)
    File file = _fs->open(filename, "r");
#else
    File file = _fs->open(filename);
#endif
    if (!file || file.isDirectory()) {
      file.close();
      continue;
    }
    mesh::LocalIdentity loaded;
    if (!loaded.readFrom(file)) {
      file.close();
      continue;
    }
    // The legacy display name is optional. A key-only image remains valid;
    // neither a short name nor a partial key may leak into the caller's state.
    if (display_name != nullptr && max_name_sz > 0) {
      const int n = max_name_sz > 32 ? 32 : max_name_sz;
      char loaded_name[32];
      if (file.read(reinterpret_cast<uint8_t*>(loaded_name), n) == n) {
        loaded_name[n - 1] = 0;
        memcpy(display_name, loaded_name, n);
      }
    }
    file.close();
    id = loaded;
    return IdentityLoadResult::Loaded;
  }
  // An existing file which cannot be read is materially different from a
  // fresh device. Startup callers must preserve it instead of replacing the
  // radio's private key after a transient or localized flash failure.
  return IdentityLoadResult::Unreadable;
}

bool IdentityStore::saveWithRetry(const char* name, const mesh::LocalIdentity& id) {
  for (unsigned attempt = 0; attempt < IO_ATTEMPTS; ++attempt) {
    if (save(name, id)) return true;
  }
  return false;
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
