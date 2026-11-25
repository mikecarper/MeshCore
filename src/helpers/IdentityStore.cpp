#include "IdentityStore.h"

#include "FilePresence.h"
#if defined(ARDUINO_ARCH_ESP32)
#include "esp32/PrivateStorage.h"
#include "UsbDataLock.h"
#include <algorithm>
#include <mbedtls/platform_util.h>

static bool decodeIdentity(const char* name, const uint8_t* record, mesh::LocalIdentity& id) {
  uint8_t secret[PRV_KEY_SIZE];
  const bool sealed = mesh::privateStorage::isSealedIdentity(record);
  if (sealed) {
    if (!mesh::privateStorage::openIdentity(name, record, secret)) return false;
  } else {
    memcpy(secret, record + PUB_KEY_SIZE, sizeof(secret));
  }
  id.readFrom(secret, sizeof(secret));  // Recompute the public key before accepting either format.
  mbedtls_platform_zeroize(secret, sizeof(secret));
  return sealed || memcmp(id.pub_key, record, PUB_KEY_SIZE) == 0;
}
#endif
#if defined(ESP32_PLATFORM)
#include <Preferences.h>
#include <nvs.h>
#endif
#if defined(NRF52_PLATFORM) || defined(STM32_PLATFORM)
#include "AtomicFileWriter.h"
#else
#include "ContactFileTransaction.h"
#endif

#if defined(ARDUINO_ARCH_ESP32)
static bool writeIdentityRecord(FILESYSTEM* fs, const char* path, const char* name,
                                const mesh::LocalIdentity& id, const uint8_t* tail,
                                size_t tailSize, bool locked) {
  uint8_t record[mesh::privateStorage::IDENTITY_BYTES];
  if (locked) {
    uint8_t secret[PRV_KEY_SIZE];
    const bool ok = id.writeTo(secret, sizeof(secret)) == sizeof(secret)
        && mesh::privateStorage::sealIdentity(name, secret, record);
    mbedtls_platform_zeroize(secret, sizeof(secret));
    if (!ok) return false;
  } else {
    memcpy(record, id.pub_key, PUB_KEY_SIZE);
    if (id.writeTo(record + PUB_KEY_SIZE, PRV_KEY_SIZE) != PRV_KEY_SIZE) return false;
  }
  mesh::ContactFileTransaction transaction(fs, path, mesh::filePresence<FILESYSTEM>);
  const bool written = transaction && transaction.write(record, sizeof(record)) == sizeof(record)
      && (!tailSize || transaction.write(tail, tailSize) == tailSize);
  mbedtls_platform_zeroize(record, sizeof(record));
  return transaction.commit(written);
}

bool IdentityStore::setStorageProtection(const char* name, bool locked) {
  char path[40];
  if (snprintf(path, sizeof(path), "%s/%s.id", _dir, name) >= (int)sizeof(path)
      || !recover(name)) return false;
  File file = _fs->open(path, "r");
  uint8_t record[mesh::privateStorage::IDENTITY_BYTES], tail[32];
  if (!file || file.size() < sizeof(record) || file.size() > sizeof(record) + sizeof(tail)) return false;
  const size_t tailSize = file.size() - sizeof(record);
  mesh::LocalIdentity id;
  const bool ok = file.read(record, sizeof(record)) == sizeof(record)
      && decodeIdentity(name, record, id) && (!tailSize || file.read(tail, tailSize) == tailSize);
  const bool convert = ok && mesh::privateStorage::isSealedIdentity(record) != locked;
  mbedtls_platform_zeroize(record, sizeof(record));
  file.close();
  if (!ok || (convert && !writeIdentityRecord(_fs, path, name, id, tail, tailSize, locked))) return false;

  // A failed cleanup during a previous publish can leave the old format live.
  for (const char* suffix : {".tmp", ".bak"}) {
    char artifact[48];
    snprintf(artifact, sizeof(artifact), "%s%s", path, suffix);
    bool present;
    if (!mesh::filePresence(_fs, artifact, present)) return false;
    if (present) {
      _fs->remove(artifact);
      if (!mesh::filePresence(_fs, artifact, present) || present) return false;
    }
  }
  return true;
}
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
#if defined(ESP32_PLATFORM)
  // A 4 MiB LoRa partition migration must boot the old app0 receiver before
  // the new Full image can run. The bridge staged the original key in stable
  // NVS; that old receiver may have formatted the newly sized SPIFFS and
  // generated a temporary key. Restore the original before normal startup
  // accepts either that temporary key or a missing identity.
  if (strcmp(_dir, "/identity") == 0 && strcmp(name, "_main") == 0) {
    // Probe without creating an empty migration namespace on ordinary boots.
    // Direct NVS distinguishes a clean absence (without Preferences logging)
    // from storage/type failures that must not accept a temporary/new key.
    nvs_handle_t migration_handle;
    const esp_err_t opened = nvs_open("mesh-pt-migrate", NVS_READONLY,
                                      &migration_handle);
    if (opened != ESP_OK && opened != ESP_ERR_NVS_NOT_FOUND)
      return IdentityLoadResult::Unreadable;
    if (opened == ESP_OK) {
      uint8_t pending_value = 0;
      const esp_err_t flag = nvs_get_u8(migration_handle, "id-pending",
                                       &pending_value);
      if ((flag != ESP_OK && flag != ESP_ERR_NVS_NOT_FOUND)
          || (flag == ESP_OK && pending_value > 1)) {
        nvs_close(migration_handle);
        return IdentityLoadResult::Unreadable;
      }
      const bool pending = flag == ESP_OK && pending_value == 1;
      uint8_t file_bytes[PUB_KEY_SIZE + PRV_KEY_SIZE];
      size_t staged = sizeof(file_bytes);
      const esp_err_t identity_result = pending
          ? nvs_get_blob(migration_handle, "identity", file_bytes, &staged) : ESP_OK;
      nvs_close(migration_handle);
      if (pending) {
        if (identity_result != ESP_OK || staged != sizeof(file_bytes))
          return IdentityLoadResult::Unreadable;
        mesh::LocalIdentity original;
#if defined(ARDUINO_ARCH_ESP32)
        if (!decodeIdentity(name, file_bytes, original)) return IdentityLoadResult::Unreadable;
#else
        uint8_t export_bytes[sizeof(file_bytes)];
        memcpy(export_bytes, file_bytes + PUB_KEY_SIZE, PRV_KEY_SIZE);
        memcpy(export_bytes + PRV_KEY_SIZE, file_bytes, PUB_KEY_SIZE);
        original.readFrom(export_bytes, sizeof(export_bytes));
#endif
        if (!saveWithRetry(name, original)) return IdentityLoadResult::Unreadable;
        File restored = _fs->open(filename, "r");
        uint8_t verified[sizeof(file_bytes)];
#if defined(ARDUINO_ARCH_ESP32)
        mesh::LocalIdentity checked;
#endif
        const bool matches = restored
            && restored.read(verified, sizeof(verified)) == sizeof(verified)
#if defined(ARDUINO_ARCH_ESP32)
            && decodeIdentity(name, verified, checked) && checked.matches(original);
#else
            && memcmp(file_bytes, verified, sizeof(file_bytes)) == 0;
#endif
        if (restored) restored.close();
        if (!matches) return IdentityLoadResult::Unreadable;
        Preferences migration_nvs;
        if (!migration_nvs.begin("mesh-pt-migrate", false))
          return IdentityLoadResult::Unreadable;
        // Clear the pending flag first: a power cut cannot then leave a
        // pending flag pointing at already removed key bytes.
        const bool cleared = migration_nvs.remove("id-pending");
        if (cleared) migration_nvs.remove("identity");
        migration_nvs.end();
        if (!cleared) return IdentityLoadResult::Unreadable;
      }
    }
  }
#endif
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
#if defined(ARDUINO_ARCH_ESP32)
    uint8_t record[mesh::privateStorage::IDENTITY_BYTES];
    const bool read = file.read(record, sizeof(record)) == sizeof(record)
        && decodeIdentity(name, record, loaded);
    bool locked;
    const bool policyKnown = mesh::readUsbDataLockSetting(locked);
    const bool convert = read && policyKnown && mesh::privateStorage::isSealedIdentity(record) != locked;
    mbedtls_platform_zeroize(record, sizeof(record));
    if (!read || !policyKnown) { file.close(); continue; }
    char oldName[33] = {};
    const size_t tail = std::min(file.size() - sizeof(record), size_t(32));
    if (tail && file.read(reinterpret_cast<uint8_t*>(oldName), tail) != tail) { file.close(); continue; }
    file.close();
    const bool mainIdentity = strcmp(name, "_main") == 0 && strcmp(_dir, "/identity") == 0;
    if (mainIdentity) mesh::resumeUsbStorageConversion(convert);
    else if (convert && !setStorageProtection(name, locked)) continue;
    if (tail && display_name && max_name_sz > 0 && size_t(std::min(max_name_sz, 32)) <= tail) {
      const size_t n = std::min(max_name_sz, 32);
      memcpy(display_name, oldName, n);
      display_name[n-1] = 0;
    }
#else
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
#endif
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
  return save(name, id, nullptr);
}

bool IdentityStore::save(const char *name, const mesh::LocalIdentity& id, const char display_name[]) {
  char filename[40];
  if (snprintf(filename, sizeof(filename), "%s/%s.id", _dir, name) >= (int)sizeof(filename)
      || !recover(name)) return false;
  uint8_t display_data[32] = {};
  if (display_name) strncpy(reinterpret_cast<char*>(display_data), display_name, sizeof(display_data) - 1);
#if defined(ARDUINO_ARCH_ESP32)
  bool locked;
  return mesh::readUsbDataLockSetting(locked)
      && writeIdentityRecord(_fs, filename, name, id, display_data, display_name ? sizeof(display_data) : 0, locked);
#else
  uint8_t key_data[PRV_KEY_SIZE + PUB_KEY_SIZE];
  if (id.writeTo(key_data, sizeof(key_data)) != sizeof(key_data)) return false;
#if defined(NRF52_PLATFORM) || defined(STM32_PLATFORM)
  mesh::AtomicFileWriter writer(_fs, filename);
#else
  mesh::ContactFileTransaction writer(_fs, filename, mesh::filePresence<FILESYSTEM>);
#endif
  const bool wrote = writer
      && writer.write(&key_data[PRV_KEY_SIZE], PUB_KEY_SIZE) == PUB_KEY_SIZE
      && writer.write(key_data, PRV_KEY_SIZE) == PRV_KEY_SIZE
      && (!display_name || writer.write(display_data, sizeof(display_data)) == sizeof(display_data));
  return writer.commit(wrote);
#endif
}
