#include <Arduino.h>
#include <stdlib.h>
#include <initializer_list>
#include "DataStore.h"
#include <helpers/FileRead.h>
#include <helpers/AdvertDataHelpers.h>
#if defined(ESP32_PLATFORM) || defined(RP2040_PLATFORM)
#include <helpers/ContactFileTransaction.h>
#endif
#if defined(ESP32_PLATFORM)
#include <errno.h>
#include <sys/stat.h>
#endif
#if COMPANION_FEATURE_READER
#include <helpers/bible/ReaderBookmarkFiles.h>
#endif

#if defined(NRF52_PLATFORM) || defined(STM32_PLATFORM)
#include <helpers/AtomicFileWriter.h>
#if defined(NRF52_PLATFORM) && defined(EXTRAFS) && !defined(QSPIFLASH)
#include <helpers/nrf52/InternalSecondaryFsRepair.h>
#include "ResilientInternalExtraFS.h"
#endif
#if defined(NRF52_PLATFORM)
#include <helpers/nrf52/RamFallbackFileSystem.h>
#endif
#endif

// Linked presence of this symbol is the authoritative signal that this firmware actually mounts the
// internal 0xD4000 ExtraFS. OTA layout code references it weakly, so non-companion roles can reclaim the
// reserved range even though nrf52_base defines EXTRAFS globally.
#if defined(NRF52_PLATFORM) && defined(EXTRAFS) && !defined(QSPIFLASH)
extern "C" __attribute__((used)) const uint8_t g_meshcore_internal_extrafs = 1u;
extern "C" uint32_t __flash_arduino_end[];
#endif

#if defined(EXTRAFS) || defined(QSPIFLASH)
  #define MAX_BLOBRECS 100
#else
  #define MAX_BLOBRECS 20
#endif

DataStore::DataStore(FILESYSTEM& fs, mesh::RTCClock& clock) : _fs(&fs), _fsExtra(nullptr),
    _configuredFsExtra(nullptr), _clock(&clock),
#if defined(NRF52_PLATFORM) || defined(STM32_PLATFORM)
    identity_store(fs, "")
#elif defined(RP2040_PLATFORM)
    identity_store(fs, "/identity")
#else
    identity_store(fs, "/identity")
#endif
{
}

#if defined(EXTRAFS) || defined(QSPIFLASH)
DataStore::DataStore(FILESYSTEM& fs, FILESYSTEM& fsExtra, mesh::RTCClock& clock) : _fs(&fs), _fsExtra(&fsExtra),
    _configuredFsExtra(&fsExtra), _clock(&clock),
#if defined(NRF52_PLATFORM) || defined(STM32_PLATFORM)
    identity_store(fs, "")
#elif defined(RP2040_PLATFORM)
    identity_store(fs, "/identity")
#else
    identity_store(fs, "/identity")
#endif
{
}
#endif

#if !defined(NRF52_PLATFORM)
// Unlike ESP32 FS::exists(), a metadata probe must not treat failure to open
// an existing file as evidence that its durable contents are absent.
static bool companionPathPresence(FILESYSTEM* fs, const char* path,
                                  bool& present) {
#if defined(ESP32_PLATFORM)
  (void)fs; // Companion storage is mounted at the default SPIFFS VFS path.
  char vfs_path[96];
  const int length = snprintf(vfs_path, sizeof(vfs_path), "/spiffs%s", path);
  if (length < 0 || static_cast<size_t>(length) >= sizeof(vfs_path)) return false;
  struct stat info;
  const int result = ::stat(vfs_path, &info);
  if (result != 0 && errno != ENOENT) return false;
  present = result == 0;
#elif defined(STM32_PLATFORM)
  struct lfs_info info;
  fs->_lockFS();
  const int result = lfs_stat(fs->_getFS(), path, &info);
  fs->_unlockFS();
  if (result != 0 && result != LFS_ERR_NOENT) return false;
  present = result == 0;
#else
  // Arduino-Pico's exists() uses lfs_stat(), not an open operation.
  // Its public API hides metadata errors; existing-file open/read failures
  // are distinguishable below, but metadata I/O failure is not.
  present = fs->exists(path);
#endif
  return true;
}
#if defined(ESP32_PLATFORM)
// Only called after the alternate image has been read and validated in full.
// Irrecoverable settings/channels may be replaced, but the verified recovery
// source stays intact until its rename has actually succeeded.
static bool promoteCompanionRecoveryFile(FILESYSTEM* fs, const char* target,
                                         const char*& source) {
  if (source == nullptr) return true;
  bool target_exists = false, source_exists = false;
  if (!companionPathPresence(fs, target, target_exists)
      || !companionPathPresence(fs, source, source_exists) || !source_exists) {
    return false;
  }
  if (target_exists && !fs->remove(target)) return false;
  if (!fs->rename(source, target)) return false;
  source = nullptr;
  return true;
}
#endif
#endif

static File openWrite(FILESYSTEM* fs, const char* filename) {
#if defined(NRF52_PLATFORM) || defined(STM32_PLATFORM)
  fs->remove(filename);
  return fs->open(filename, FILE_O_WRITE);
#elif defined(RP2040_PLATFORM)
  return fs->open(filename, "w");
#else
  return fs->open(filename, "w", true);
#endif
}

#if defined(NRF52_PLATFORM) || defined(STM32_PLATFORM)
static bool validateLfsFilesystem(FILESYSTEM* fs);
#endif
#if defined(NRF52_PLATFORM)
static void cleanupAtomicTempFiles(FILESYSTEM* fs);
static bool contactPathPresence(FILESYSTEM* fs, const char* path,
                                bool& present, uint32_t* size = nullptr);
#endif

void DataStore::begin() {
#if defined(RP2040_PLATFORM)
  identity_store.begin();
#endif

#if defined(NRF52_PLATFORM) || defined(STM32_PLATFORM)
#if defined(NRF52_PLATFORM)
  resetContactPageState(true);
  if (_secondary_authority_unknown) {
    _contact_load_incomplete = true;
    _prefs_load_incomplete = true;
  }
  if (_primary_storage_unavailable) {
    // The base mount already failed. Do not traverse or open this unmounted
    // filesystem; both can re-enter corrupt metadata before explicit repair.
    _identity_creation_blocked = true;
    _contact_load_incomplete = true;
    _fsExtra = nullptr;
    return;
  }

  bool primary_ready = validateLfsFilesystem(_fs);
  if (!primary_ready) {
    for (uint8_t retry = 0; retry < 3 && !primary_ready; ++retry) {
      delay(3000);
      primary_ready = validateLfsFilesystem(_fs);
    }
    if (!primary_ready) {
      MESH_DEBUG_PRINTLN(
          "DataStore: primary metadata remains unreadable after delayed retries; reinitializing storage");
      primary_ready = _fs->format() && validateLfsFilesystem(_fs);
      if (!primary_ready) {
        mesh::storage::RamFallbackFileSystem* ram_primary =
            mesh::storage::createRamFallbackFileSystem();
        if (ram_primary == nullptr) {
          _primary_storage_unavailable = true;
          _identity_creation_blocked = true;
          _contact_load_incomplete = true;
          _prefs_load_incomplete = true;
          _fsExtra = nullptr;
          return;
        }
        MESH_DEBUG_PRINTLN(
            "DataStore: verified primary erase/write failed; using volatile RAM filesystem");
        useVolatilePrimaryFS(ram_primary->filesystem());
        primary_ready = true;
      } else {
        _identity_creation_blocked = false;
        _contact_load_incomplete = false;
        _prefs_load_incomplete = false;
#if defined(EXTRAFS) && !defined(QSPIFLASH)
        if (_configuredFsExtra != nullptr) {
          ResilientInternalExtraFS* extra =
              static_cast<ResilientInternalExtraFS*>(_configuredFsExtra);
          const bool scan = !extra->pageMapReady();
          if (scan) extra->resetPageMapForDestructiveRecovery();
          else extra->requirePageMapRewrite();
          if (!reinitializeInternalExtraFS(scan)) disableSecondaryFS(false);
        }
#endif
      }
    }
  }

  // A mounted filesystem can still contain an unreadable identity record.
  // Keep the normal bounded reads, then try three more times three seconds
  // apart. Only after those fail may startup wipe the unusable primary store.
  mesh::LocalIdentity identity_probe;
  IdentityLoadResult identity_state =
      identity_store.loadResult("_main", identity_probe);
  if (identity_state == IdentityLoadResult::Unreadable) {
    for (uint8_t retry = 0; retry < 3; ++retry) {
      delay(3000);
      identity_state = identity_store.loadResult("_main", identity_probe);
      if (identity_state != IdentityLoadResult::Unreadable) break;
    }
  }
  if (identity_state == IdentityLoadResult::Unreadable) {
    MESH_DEBUG_PRINTLN(
        "DataStore: identity remains unreadable after delayed retries; reinitializing primary storage");
    const bool primary_reinitialized = _fs->format()
        && validateLfsFilesystem(_fs);
    if (!primary_reinitialized) {
      mesh::storage::RamFallbackFileSystem* ram_primary =
          mesh::storage::createRamFallbackFileSystem();
      if (ram_primary == nullptr) {
        _primary_storage_unavailable = true;
        _identity_creation_blocked = true;
        _contact_load_incomplete = true;
        _prefs_load_incomplete = true;
        _fsExtra = nullptr;
        return;
      }
      MESH_DEBUG_PRINTLN(
          "DataStore: verified primary erase/write failed; using volatile RAM filesystem");
      useVolatilePrimaryFS(ram_primary->filesystem());
    } else {
      _identity_creation_blocked = false;
      _contact_load_incomplete = false;
      _prefs_load_incomplete = false;
#if defined(EXTRAFS) && !defined(QSPIFLASH)
      if (_configuredFsExtra != nullptr) {
        ResilientInternalExtraFS* extra =
            static_cast<ResilientInternalExtraFS*>(_configuredFsExtra);
        const bool scan = !extra->pageMapReady();
        if (scan) extra->resetPageMapForDestructiveRecovery();
        else extra->requirePageMapRewrite();
        if (!reinitializeInternalExtraFS(scan)) {
          // The primary identity is now a fresh authoritative install. An
          // unusable old secondary must not block that new identity.
          disableSecondaryFS(false);
        }
      }
#endif
    }
  }
#if defined(EXTRAFS) && !defined(QSPIFLASH)
  // Validate primary first: automatic secondary recovery must never hide a
  // primary/identity fault. Retry or rebuild before loading any RAM state.
  recoverInternalExtraFSOnBoot();
#else
  if (_fsExtra != nullptr && !validateLfsFilesystem(_fsExtra)) {
    // Automatic destructive recovery is restricted to reserved internal
    // ExtraFS. Removable/external QSPI still requires an explicit repair.
    MESH_DEBUG_PRINTLN("DataStore: secondary LittleFS metadata is corrupt; preserving it and using primary storage");
    disableSecondaryFS(true);
  }
#endif
  if (primary_ready) cleanupAtomicTempFiles(_fs);
  if (_fsExtra != nullptr) cleanupAtomicTempFiles(_fsExtra);
#endif
  #if defined(EXTRAFS) || defined(QSPIFLASH)
  if (_fsExtra != nullptr && !migrateToSecondaryFS()) {
    MESH_DEBUG_PRINTLN("DataStore: one or more secondary filesystem migrations remain pending");
  }
  #endif
#if defined(NRF52_PLATFORM)
  // A journal-stat failure leaves the contact/channel authority unknowable.
  // Do not mutate the reconstructable advert cache on either candidate store
  // while that fail-closed latch is active.
  if (!_contact_load_incomplete) checkAdvBlobFile();
#else
  checkAdvBlobFile();
#endif
#else
  // init 'blob store' support
  _fs->mkdir("/bl");
#endif
}

#if defined(ESP32)
  #include <SPIFFS.h>
  #include <nvs_flash.h>
#elif defined(RP2040_PLATFORM)
  #include <LittleFS.h>
#elif defined(NRF52_PLATFORM) || defined(STM32_PLATFORM)
  #if defined(QSPIFLASH)
    #include <CustomLFS_QSPIFlash.h>
  #elif defined(EXTRAFS)
    #include "ResilientInternalExtraFS.h"
  #else 
    #include <InternalFileSystem.h>
  #endif
#endif

#if defined(NRF52_PLATFORM) || defined(STM32_PLATFORM)
struct LfsTraversalState {
  lfs_size_t visited;
  lfs_size_t block_count;
};

int _countLfsBlock(void *p, lfs_block_t block){
  LfsTraversalState* state = (LfsTraversalState*)p;
  // Valid blocks are [0, block_count).  A traversal longer than block_count
  // indicates a metadata cycle even if each individual block number is valid.
  if (block >= state->block_count || state->visited >= state->block_count) {
    MESH_DEBUG_PRINTLN("ERROR: LittleFS traversal out of bounds/cyclic at block %lu",
                       (unsigned long)block);
    return LFS_ERR_CORRUPT;
  }
  state->visited++;
  return 0;
}

lfs_ssize_t _getLfsUsedBlockCount(FILESYSTEM* fs) {
  LfsTraversalState state = {0, fs->_getFS()->cfg->block_count};
  int err = lfs_traverse(fs->_getFS(), _countLfsBlock, &state);
  if (err) {
    MESH_DEBUG_PRINTLN("ERROR: lfs_traverse() error: %d", err);
    return -1;
  }
  return state.visited;
}

static bool validateLfsFilesystem(FILESYSTEM* fs) {
  return fs != nullptr && _getLfsUsedBlockCount(fs) >= 0;
}
#endif

#if defined(NRF52_PLATFORM)
static void cleanupAtomicTempFiles(FILESYSTEM* fs) {
  if (fs == nullptr) return;

  static const char* fixed_temp_paths[] = {
      "/_main.id.tmp", "/new_prefs.tmp", "/channels2.tmp",
      "/contacts3.tmp", "/contacts4.mig.tmp", "/adv_blobs.tmp",
      "/.extrafs.mig.tmp", "/extrafs.badpages.tmp",
      "/extrafs.badpages.bak.tmp"};
  for (size_t i = 0; i < sizeof(fixed_temp_paths) / sizeof(fixed_temp_paths[0]); i++) {
    if (fs->exists(fixed_temp_paths[i])) fs->remove(fixed_temp_paths[i]);
  }

  // Include the old ten-bucket range as well as the current five-bucket
  // layout so interrupted preview builds cannot strand full LittleFS blocks.
  for (uint8_t page = 0; page < mesh::storage::CONTACT_PAGE_COUNT; page++) {
    char path[28];
    snprintf(path, sizeof(path), "/contacts4_%02u.tmp", (unsigned)page);
    if (fs->exists(path)) fs->remove(path);
  }
  for (uint8_t bucket = 0; bucket < 10; bucket++) {
    char path[24];
    snprintf(path, sizeof(path), "/adv4_%02u.tmp", (unsigned)bucket);
    if (fs->exists(path)) fs->remove(path);
  }
}

#if defined(EXTRAFS) && !defined(QSPIFLASH)
bool DataStore::recoverInternalExtraFSOnBoot() {
  if (_configuredFsExtra == nullptr) return false;

  ResilientInternalExtraFS* extra =
      static_cast<ResilientInternalExtraFS*>(_configuredFsExtra);
  if (_primary_storage_unavailable
      || !mesh::storage::isExpectedInternalExtraFsGeometry(
          extra->getFlashAddr(), extra->getFlashSize(), extra->getBlockSize())
      || !mesh::storage::isInternalExtraFsReservedByApplication(
          (uint32_t)(uintptr_t)__flash_arduino_end)) {
    disableSecondaryFS(true);
    MESH_DEBUG_PRINTLN("DataStore: refusing automatic ExtraFS recovery outside reserved 100 KiB region");
    return false;
  }

  if (!extra->pageMapReady()) {
    MESH_DEBUG_PRINTLN(
        "DataStore: both ExtraFS page maps are unreadable; scanning and rebuilding reserved secondary storage");
    extra->resetPageMapForDestructiveRecovery();
    if (!reinitializeInternalExtraFS(true)) {
      disableSecondaryFS(true);
      return false;
    }
    _secondary_authority_unknown = false;
    _contact_load_incomplete = false;
    _identity_creation_blocked = false;
    _prefs_load_incomplete = false;
    return true;
  }

  if (extra->recoveryPending()) {
    MESH_DEBUG_PRINTLN("DataStore: running requested boot-time ExtraFS scan");
    if (!reinitializeInternalExtraFS(true)) {
      disableSecondaryFS(true);
      return false;
    }
    _secondary_authority_unknown = false;
    _contact_load_incomplete = false;
    _identity_creation_blocked = false;
    _prefs_load_incomplete = false;
    return true;
  }

  const mesh::storage::InternalSecondaryFsRecoveryResult result =
      mesh::storage::recoverInternalSecondaryFilesystem(
          [this]() -> bool {
            return _fsExtra == _configuredFsExtra
                && validateLfsFilesystem(_fsExtra);
          },
          [this, extra]() -> bool {
            MESH_DEBUG_PRINTLN("DataStore: retrying internal ExtraFS mount before recovery");
            _fsExtra = nullptr;
            extra->end();
            if (!extra->Adafruit_LittleFS::begin()) return false;
            _fsExtra = _configuredFsExtra;
            return true;
          },
          [this]() -> bool {
            MESH_DEBUG_PRINTLN("DataStore: internal ExtraFS remains unusable after mount retry; scanning and rebuilding (secondary-only data may be lost)");
            return reinitializeInternalExtraFS(true);
          });
  if (result == mesh::storage::InternalSecondaryFsRecoveryResult::Failed) {
    disableSecondaryFS(true);
    return false;
  }

  extra->acknowledgeRecoveredBootHint();
  if (extra->pageMapNeedsSave() && !extra->savePageMap(*_fs)) {
    disableSecondaryFS(true);
    return false;
  }

  // This runs only before migration and user-data loading. Clear the initial
  // mount quarantine, not errors from a later incomplete contact/prefs load.
  // Migration below establishes authority again and can re-latch any error.
  _secondary_authority_unknown = false;
  _contact_load_incomplete = false;
  _identity_creation_blocked = false;
  _prefs_load_incomplete = false;
  return true;
}

bool DataStore::reinitializeInternalExtraFS(bool scan_physical_pages) {
  if (_configuredFsExtra == nullptr) return false;

  ResilientInternalExtraFS* extra =
      static_cast<ResilientInternalExtraFS*>(_configuredFsExtra);
  if (!mesh::storage::isExpectedInternalExtraFsGeometry(
          extra->getFlashAddr(), extra->getFlashSize(),
          extra->getBlockSize())
      || !mesh::storage::isInternalExtraFsReservedByApplication(
          (uint32_t)(uintptr_t)__flash_arduino_end)) {
    _fsExtra = nullptr;
    MESH_DEBUG_PRINTLN("DataStore: refusing internal ExtraFS repair with unexpected geometry");
    return false;
  }
  if (!extra->pageMapReady()) {
    if (!scan_physical_pages) {
      _fsExtra = nullptr;
      return false;
    }
    extra->resetPageMapForDestructiveRecovery();
  }

  // A physical test is destructive and must run only during early boot, before
  // radio/UI tasks can issue competing SoftDevice flash operations. Ordinary
  // USB repair and factory-reset paths retain the already-verified page map.
  if ((scan_physical_pages && !extra->scanAndRetireBadPages())
      || (extra->pageMapNeedsSave() && !extra->savePageMap(*_fs))) {
    _fsExtra = nullptr;
    MESH_DEBUG_PRINTLN("DataStore: ExtraFS physical scan or bad-page map save failed");
    return false;
  }

  const mesh::storage::InternalSecondaryFsRepairResult result =
      mesh::storage::repairInternalSecondaryFilesystem(
          [extra]() -> bool {
            // Avoid CustomLFS::formatRegion(): it probes corrupt metadata with
            // open("/") before deciding whether to unmount. end() is safe for
            // both mounted and unmounted Adafruit LittleFS instances.
            extra->end();
            return extra->format();
          },
          [extra]() -> bool {
            // Call the base mount explicitly. CustomLFS::begin() would erase
            // the complete region again if this mount failed.
            return extra->Adafruit_LittleFS::begin();
          },
          [this]() -> bool {
            return validateLfsFilesystem(_configuredFsExtra);
          });
  if (result != mesh::storage::InternalSecondaryFsRepairResult::Repaired) {
    _fsExtra = nullptr;
    switch (result) {
      case mesh::storage::InternalSecondaryFsRepairResult::FormatFailed:
        extra->setStage(ResilientInternalExtraFS::Stage::FormatFailed);
        MESH_DEBUG_PRINTLN("DataStore: internal ExtraFS repair format failed");
        break;
      case mesh::storage::InternalSecondaryFsRepairResult::MountFailed:
        extra->setStage(ResilientInternalExtraFS::Stage::MountFailed);
        MESH_DEBUG_PRINTLN("DataStore: internal ExtraFS repair mount failed");
        break;
      case mesh::storage::InternalSecondaryFsRepairResult::ValidationFailed:
        extra->setStage(ResilientInternalExtraFS::Stage::ValidationFailed);
        MESH_DEBUG_PRINTLN("DataStore: internal ExtraFS repair validation failed");
        break;
      default:
        break;
    }
    return false;
  }

  _fsExtra = _configuredFsExtra;
  extra->setStage(ResilientInternalExtraFS::Stage::Repaired);
  MESH_DEBUG_PRINTLN("DataStore: internal ExtraFS repaired and reactivated");
  return true;
}
#endif
#endif

void DataStore::markPrimaryFSUnavailable() {
#if defined(NRF52_PLATFORM)
  _primary_storage_unavailable = true;
  _identity_creation_blocked = true;
  _contact_load_incomplete = true;
  _prefs_load_incomplete = true;
#endif
}

#if defined(NRF52_PLATFORM)
void DataStore::useVolatilePrimaryFS(FILESYSTEM& fs) {
  _fs = &fs;
  identity_store.useFileSystem(fs);
  _fsExtra = nullptr;
  _configuredFsExtra = nullptr;
  _volatile_primary_fs = true;
  _primary_storage_unavailable = false;
  _secondary_authority_unknown = false;
  _identity_creation_blocked = false;
  _contact_load_incomplete = false;
  _prefs_load_incomplete = false;
}
#endif

void DataStore::disableSecondaryFS(bool authority_unknown) {
  _fsExtra = nullptr;
#if defined(NRF52_PLATFORM)
  if (authority_unknown) {
    // Without reading the secondary journal, primary may be a retired source
    // rather than an empty fresh-install store. Keep this fact across begin()'s
    // runtime-state reset and block every authority-changing write.
    _secondary_authority_unknown = true;
    _contact_load_incomplete = true;
    _identity_creation_blocked = true;
    _prefs_load_incomplete = true;
  }
#else
  (void)authority_unknown;
#endif
}

uint32_t DataStore::getStorageUsedKb() const {
#if defined(ESP32)
  return SPIFFS.usedBytes() / 1024;
#elif defined(RP2040_PLATFORM)
  FSInfo info;
  info.usedBytes = 0;
  _fs->info(info);
  return info.usedBytes / 1024;
#elif defined(NRF52_PLATFORM) || defined(STM32_PLATFORM)
  const lfs_config* config = _getContactsChannelsFS()->_getFS()->cfg;
  int usedBlockCount = _getLfsUsedBlockCount(_getContactsChannelsFS());
  if (usedBlockCount < 0) return 0;
  int usedBytes = config->block_size * usedBlockCount;
  return usedBytes / 1024;
#else
  return 0;
#endif
}

uint32_t DataStore::getStorageTotalKb() const {
#if defined(ESP32)
  return SPIFFS.totalBytes() / 1024;
#elif defined(RP2040_PLATFORM)
  FSInfo info;
  info.totalBytes = 0;
  _fs->info(info);
  return info.totalBytes / 1024;
#elif defined(NRF52_PLATFORM) || defined(STM32_PLATFORM)
  const lfs_config* config = _getContactsChannelsFS()->_getFS()->cfg;
  int totalBytes = config->block_size * config->block_count;
  return totalBytes / 1024;
#else
  return 0;
#endif
}

File DataStore::openRead(const char* filename) {
  return openRead(_fs, filename);
}

#if COMPANION_FEATURE_READER
namespace {
class ReaderBookmarkFiles {
  FILESYSTEM* _fs;
public:
  explicit ReaderBookmarkFiles(FILESYSTEM* fs) : _fs(fs) {}
  bool exists(const char* path) {
#if defined(NRF52_PLATFORM)
    bool present = false;
    // Unknown presence must take the read/abort path, never the new-file path.
    return !contactPathPresence(_fs, path, present) || present;
#else
    return _fs->exists(path);
#endif
  }
  bool remove(const char* path) {
#if defined(NRF52_PLATFORM)
    bool present = false;
    if (!contactPathPresence(_fs, path, present)) return false;
    return !present || _fs->remove(path);
#else
    return !exists(path) || _fs->remove(path);
#endif
  }
  bool rename(const char* from, const char* to) { return _fs->rename(from, to); }
  bool read(const char* path, uint8_t (&data)[mesh::bible::kBookmarkBytes]) {
    File file = mesh::openFileRead(_fs, path);
    if (!file) return false;
    if (file.size() != sizeof(data)) {
      // Known malformed record, not an I/O failure. Let the codec reject it
      // and permit a later verified replacement instead of trapping resume.
      memset(data, 0, sizeof(data));
      file.close();
      return true;
    }
    const bool ok = file.read(data, sizeof(data)) == sizeof(data);
    file.close();
    return ok;
  }
  bool write(const char* path, const uint8_t (&data)[mesh::bible::kBookmarkBytes]) {
    File file = openWrite(_fs, path);
    if (!file) return false;
    const bool ok = file.write(data, sizeof(data)) == sizeof(data);
    file.flush();
    file.close();
    return ok;
  }
};
} // namespace

bool DataStore::loadReaderBookmark(mesh::bible::Position& pos) {
  pos = mesh::bible::Position{};
#if defined(NRF52_PLATFORM)
  if (_primary_storage_unavailable) return false;
#endif
  ReaderBookmarkFiles files(_fs);
  return mesh::bible::loadReaderBookmark(files, pos);
}

bool DataStore::saveReaderBookmark(mesh::bible::Position pos) {
#if defined(NRF52_PLATFORM)
  if (_primary_storage_unavailable) return false;
#endif
  ReaderBookmarkFiles files(_fs);
  return mesh::bible::saveReaderBookmark(files, pos);
}
#endif

File DataStore::openRead(FILESYSTEM* fs, const char* filename) {
  return mesh::openFileRead(fs, filename);
}

File DataStore::openDirectory(const char* path) {
  return openDirectory(_fs, path);
}

File DataStore::openDirectory(FILESYSTEM* fs, const char* path) {
  if (fs == nullptr || path == nullptr) return mesh::emptyFile(fs);
  // SPIFFS has virtual directories: do not use the regular-file existence
  // check here. Only the explicit listing API may return directory handles.
#if defined(NRF52_PLATFORM) || defined(STM32_PLATFORM)
  File directory = fs->open(path, FILE_O_READ);
#elif defined(RP2040_PLATFORM)
  File directory = fs->open(path, "r");
#else
  File directory = fs->open(path, "r", false);
#endif
  if (directory && !directory.isDirectory()) {
    directory.close();
    return mesh::emptyFile(fs);
  }
  return directory;
}

bool DataStore::removeFile(const char* filename) {
  return _fs->remove(filename);
}

bool DataStore::removeFile(FILESYSTEM* fs, const char* filename) {
  return fs->remove(filename);
}

bool DataStore::formatFileSystem() {
#if MESH_CONTACT_CACHE && defined(ESP32_PLATFORM)
  _contact_path_reader.close();
#endif
#if defined(NRF52_PLATFORM) || defined(STM32_PLATFORM)
  #if defined(NRF52_PLATFORM)
  resetContactPageState();
  #endif
  const bool primary_success = _fs->format();
#if defined(NRF52_PLATFORM) && defined(EXTRAFS) && !defined(QSPIFLASH)
  // Factory reset/rebuild is already an explicit destructive operation. Use
  // the configured pointer so it also clears and reactivates an ExtraFS which
  // normal boot deliberately quarantined after failed traversal validation.
  bool secondary_success = true;
  if (primary_success && _configuredFsExtra != nullptr) {
    ResilientInternalExtraFS* extra =
        static_cast<ResilientInternalExtraFS*>(_configuredFsExtra);
    if (!extra->pageMapReady()) {
      extra->resetPageMapForDestructiveRecovery();
    } else {
      // The primary format erased both map files. Recreate them before the
      // secondary format so a reboot never guesses a retired page is healthy.
      extra->requirePageMapRewrite();
    }
    secondary_success = reinitializeInternalExtraFS();
  }
#else
  const bool secondary_success = !primary_success
      || _fsExtra == nullptr || _fsExtra->format();
#endif
  const bool success = primary_success && secondary_success;
#if defined(NRF52_PLATFORM)
  // A successful explicit erase creates a new empty authoritative store. A
  // failed erase must keep the boot's load quarantine latched.
  if (success) {
    _contact_load_incomplete = false;
#if MESH_CONTACT_CACHE
    _cache_load_incomplete = false;
#endif
    _identity_creation_blocked = false;
    _prefs_load_incomplete = false;
  }
#else
  if (success) {
    _identity_creation_blocked = false;
    _prefs_load_incomplete = false;
    _channel_load_incomplete = false;
#if !MESH_CONTACT_CACHE
    _uncached_contact_load_incomplete = false;
#endif
  }
#endif
  return success;
#elif defined(RP2040_PLATFORM)
  const bool success = LittleFS.format();
  if (success) {
    _identity_creation_blocked = false;
    _prefs_load_incomplete = false;
    _channel_load_incomplete = false;
    _uncached_contact_load_incomplete = false;
  }
  return success;
#elif defined(ESP32)
  bool fs_success = ((fs::SPIFFSFS *)_fs)->format();
  esp_err_t nvs_err = nvs_flash_erase(); // no need to reinit, will be done by reboot
  if (fs_success && nvs_err == ESP_OK) {
    _identity_creation_blocked = false;
    _prefs_load_incomplete = false;
    _channel_load_incomplete = false;
    _prefs_recovery_source = nullptr;
    _channel_recovery_source = nullptr;
#if MESH_CONTACT_CACHE
    _cache_load_incomplete = false;
#else
    _uncached_contact_load_incomplete = false;
#endif
  }
  return fs_success && (nvs_err == ESP_OK);
#else
  #error "need to implement format()"
#endif
}

bool DataStore::repairInternalExtraFS() {
#if defined(NRF52_PLATFORM) && defined(EXTRAFS) && !defined(QSPIFLASH)
  // A healthy active ExtraFS must never be erased. Re-running the explicit
  // command is still useful, though: it retries any verified migration which
  // failed after a previously repaired filesystem was activated.
  if (_fsExtra != nullptr) {
    CustomLFS* extra = static_cast<CustomLFS*>(_fsExtra);
    if (_fsExtra != _configuredFsExtra
        || !mesh::storage::isExpectedInternalExtraFsGeometry(
            extra->getFlashAddr(), extra->getFlashSize(), extra->getBlockSize())
        || !mesh::storage::isInternalExtraFsReservedByApplication(
            (uint32_t)(uintptr_t)__flash_arduino_end)) {
      MESH_DEBUG_PRINTLN("DataStore: refusing internal ExtraFS migration with unexpected geometry");
      return false;
    }
    cleanupAtomicTempFiles(_fsExtra);
    return migrateToSecondaryFS();
  }
  if (!reinitializeInternalExtraFS()) return false;

  cleanupAtomicTempFiles(_fsExtra);
  // The fallback primary remains intact until each destination file has been
  // copied, read back byte-for-byte, and committed by migrateToSecondaryFS().
  return migrateToSecondaryFS();
#else
  return false;
#endif
}

#if defined(NRF52_PLATFORM) && defined(EXTRAFS) && !defined(QSPIFLASH)
bool DataStore::requestInternalExtraFSBootScan() {
  if (_primary_storage_unavailable || _configuredFsExtra == nullptr) {
    return false;
  }
  ResilientInternalExtraFS* extra =
      static_cast<ResilientInternalExtraFS*>(_configuredFsExtra);
  return extra->requestBootScan(true);
}

bool DataStore::formatInternalExtraFSHealth(char* reply,
                                             size_t reply_size) const {
  if (reply == nullptr || reply_size == 0 || _configuredFsExtra == nullptr) {
    return false;
  }
  const ResilientInternalExtraFS* extra =
      static_cast<const ResilientInternalExtraFS*>(_configuredFsExtra);
  snprintf(reply, reply_size,
           "ExtraFS %s; bad=0x%07lX; detected=0x%07lX; pending=0x%07lX; usable=%luK; mount=%s; last=%s; scanned=%u; boot=0x%02X",
           extra->pageMapReady() ? "mapped" : "map-unreadable",
           (unsigned long)extra->badPages(),
           (unsigned long)extra->detectedBadPages(),
           (unsigned long)extra->pendingPages(),
           (unsigned long)(extra->usableBytes() / 1024),
           _fsExtra == _configuredFsExtra ? "active" : "unavailable",
           ResilientInternalExtraFS::stageName(extra->stage()),
           (unsigned)extra->scannedPages(),
           (unsigned)extra->bootMarkerAtInit());
  return true;
}
#endif

bool DataStore::loadMainIdentity(mesh::LocalIdentity &identity) {
#if defined(ESP32_PLATFORM) || defined(RP2040_PLATFORM)
  if (!identity_store.recover("_main")) {
    _identity_creation_blocked = true;
    return false;
  }
#endif
#if defined(NRF52_PLATFORM)
  if (_primary_storage_unavailable) return false;

  bool identity_exists = false;
  if (!contactPathPresence(_fs, "/_main.id", identity_exists)) {
    _identity_creation_blocked = true;
    return false;
  }
  if (!identity_exists) return false;

  if (!identity_store.load("_main", identity)) {
    // A present identity which cannot be read or decoded is not a fresh
    // install. Never replace it with a newly generated key this boot.
    _identity_creation_blocked = true;
    return false;
  }
  // A verified primary identity is canonical even when only contact/channel
  // authority on an unavailable secondary remains unknown.
  _identity_creation_blocked = false;
  return true;
#else
  bool identity_exists = false;
#if defined(STM32_PLATFORM)
  const char* path = "/_main.id";
#else
  const char* path = "/identity/_main.id";
#endif
  if (!companionPathPresence(_fs, path, identity_exists)) {
    _identity_creation_blocked = true;
    return false;
  }
  if (!identity_exists) return false;
  if (!identity_store.load("_main", identity)) {
    _identity_creation_blocked = true;
    return false;
  }
  _identity_creation_blocked = false;
  return true;
#endif
}

bool DataStore::canCreateMainIdentity() const {
  return !_identity_creation_blocked;
}

bool DataStore::saveMainIdentity(const mesh::LocalIdentity &identity) {
  if (_identity_creation_blocked) return false;
  return identity_store.save("_main", identity);
}

bool DataStore::loadPrefs(CompanionNodePrefs& prefs, double& node_lat,
                          double& node_lon) {
#if defined(ESP32_PLATFORM)
  // A failed open is not absence on ESP32. Try each complete, committed
  // source twice; a temporary I/O failure must not erase saved radio/PIN data.
  // Unpublished .tmp candidates never supersede a previously saved image.
  _prefs_load_incomplete = false;
  _prefs_recovery_source = nullptr;
  for (const char* path : {"/new_prefs", "/new_prefs.bak", "/node_prefs"}) {
    for (unsigned attempt = 0; attempt < 2; ++attempt) {
      bool present = false;
      if (!companionPathPresence(_fs, path, present)) continue;
      if (!present) break;
      if (!loadPrefsInt(path, prefs, node_lat, node_lon)) continue;
      if (strcmp(path, "/new_prefs") != 0) {
        _prefs_recovery_source = path;
        promoteCompanionRecoveryFile(_fs, "/new_prefs", _prefs_recovery_source);
      }
      return true;
    }
  }
  // No usable saved settings remain. Continue with the caller's defaults and
  // permit a later verified save, instead of permanently disabling settings.
  MESH_DEBUG_PRINTLN("DataStore: no recoverable preferences; defaults may replace the old image");
  return true;
#elif defined(NRF52_PLATFORM)
  if (_primary_storage_unavailable
      || (_prefs_load_incomplete && !_secondary_authority_unknown)) {
    _prefs_load_incomplete = true;
    return false;
  }

  bool new_prefs_exists = false;
  if (!contactPathPresence(_fs, "/new_prefs", new_prefs_exists)) {
    _prefs_load_incomplete = true;
    return false;
  }
  if (new_prefs_exists) {
    const bool loaded = loadPrefsInt(
        "/new_prefs", prefs, node_lat, node_lon);
    _prefs_load_incomplete = !loaded;
    return loaded;
  }

  bool legacy_prefs_exists = false;
  if (!contactPathPresence(_fs, "/node_prefs", legacy_prefs_exists)) {
    _prefs_load_incomplete = true;
    return false;
  }
  if (!legacy_prefs_exists) {
    if (_secondary_authority_unknown) {
      _prefs_load_incomplete = true;
      return false;
    }
    return true;
  }
#else
  if (_prefs_load_incomplete) return false;
#if defined(ESP32_PLATFORM) || defined(RP2040_PLATFORM)
  if (!mesh::ContactFileTransaction::recover(_fs, "/new_prefs")) {
    _prefs_load_incomplete = true;
    return false;
  }
#endif
  if (_fs->exists("/new_prefs")) {
    const bool loaded = loadPrefsInt("/new_prefs", prefs, node_lat, node_lon);
    _prefs_load_incomplete = !loaded;
    return loaded;
  }
  if (!_fs->exists("/node_prefs")) return true;
#endif

  if (!loadPrefsInt("/node_prefs", prefs, node_lat, node_lon)) {
    _prefs_load_incomplete = true;
    return false;
  }
#if defined(NRF52_PLATFORM)
  // A verified primary legacy image is authoritative even if an unavailable
  // secondary might contain a duplicate. Permit its atomic in-place upgrade.
  _prefs_load_incomplete = false;
#endif
  if (savePrefs(prefs, node_lat, node_lon)) {
    _fs->remove("/node_prefs"); // remove old only after verified replacement
  }
  return true;
}

bool DataStore::loadPrefsInt(const char *filename,
                             CompanionNodePrefs& _prefs, double& node_lat,
                             double& node_lon) {
  File file = openRead(_fs, filename);
  if (file) {
    CompanionNodePrefs loaded_prefs = _prefs;
    double loaded_lat = node_lat;
    double loaded_lon = node_lon;
    // The original image ended after ble_pin at byte 84. Later releases only
    // appended fields, sometimes as an indivisible group. Accept every format
    // actually emitted by those releases, but reject a truncated field/group
    // or an unknown tail before any value reaches the live preferences.
    static const uint32_t MIN_PREFS_SIZE = 84;
    static const uint32_t KNOWN_PREFS_SIZES[] = {
        84, 85, 90, 91, 92, 93, 140, 141, 142, 143, 144, 155,
        156, 157, 158, 159,
        159 + sizeof(loaded_prefs.bluetooth_name),
        161 + sizeof(loaded_prefs.bluetooth_name),
        168 + sizeof(loaded_prefs.bluetooth_name),
        168 + sizeof(loaded_prefs.bluetooth_name)
            + sizeof(loaded_prefs.bluetooth_mac_mode)
            + sizeof(loaded_prefs.bluetooth_mac),
        168 + sizeof(loaded_prefs.bluetooth_name)
            + sizeof(loaded_prefs.bluetooth_mac_mode)
            + sizeof(loaded_prefs.bluetooth_mac)
            + sizeof(loaded_prefs.bluetooth_stealth_peer_type)
            + sizeof(loaded_prefs.bluetooth_stealth_peer),
        168 + sizeof(loaded_prefs.bluetooth_name)
            + sizeof(loaded_prefs.bluetooth_mac_mode)
            + sizeof(loaded_prefs.bluetooth_mac)
            + sizeof(loaded_prefs.bluetooth_stealth_peer_type)
            + sizeof(loaded_prefs.bluetooth_stealth_peer)
            + sizeof(loaded_prefs.bluetooth_stealth_mode),
        226,  // prior 215-byte image plus radio timing, interference, AGC and timezone
#ifdef TBEAM_1W
        233,  // fan mode and low/high thermistor thresholds
#endif
#if defined(RP2040_PLATFORM) && defined(ENABLE_WIFI_INTERFACE)
        323,  // Pico W additionally persists its SSID/password (ESP32 uses NVS)
#endif
    };
    const uint32_t prefs_size = file.size();
    bool known_size = false;
    for (size_t i = 0;
         i < sizeof(KNOWN_PREFS_SIZES) / sizeof(KNOWN_PREFS_SIZES[0]); i++) {
      if (prefs_size == KNOWN_PREFS_SIZES[i]) {
        known_size = true;
        break;
      }
    }
    if (prefs_size < MIN_PREFS_SIZE || !known_size) {
      file.close();
      return false;
    }
    bool success = true;
    auto readField = [&file, &success](void* dest, size_t size) -> bool {
      if (!success
          || file.read(static_cast<uint8_t*>(dest), size) != size) {
        success = false;
        return false;
      }
      return true;
    };
    auto readOptionalField = [&file, &readField](void* dest,
                                                 size_t size) -> bool {
      return file.available() == 0 || readField(dest, size);
    };
    uint8_t pad[8];

    readField(&loaded_prefs.airtime_factor, sizeof(float));                                // 0
    readField(loaded_prefs.node_name, sizeof(loaded_prefs.node_name));                      // 4
    readField(pad, 4);                                                                      // 36
    readField(&loaded_lat, sizeof(loaded_lat));                                             // 40
    readField(&loaded_lon, sizeof(loaded_lon));                                             // 48
    readField(&loaded_prefs.freq, sizeof(loaded_prefs.freq));                               // 56
    readField(&loaded_prefs.sf, sizeof(loaded_prefs.sf));                                   // 60
    readField(&loaded_prefs.cr, sizeof(loaded_prefs.cr));                                   // 61
    readField(&loaded_prefs.client_repeat, sizeof(loaded_prefs.client_repeat));             // 62
    readField(&loaded_prefs.manual_add_contacts, sizeof(loaded_prefs.manual_add_contacts)); // 63
    readField(&loaded_prefs.bw, sizeof(loaded_prefs.bw));                                   // 64
    readField(&loaded_prefs.tx_power_dbm, sizeof(loaded_prefs.tx_power_dbm));               // 68
    readField(&loaded_prefs.telemetry_mode_base, sizeof(loaded_prefs.telemetry_mode_base)); // 69
    readField(&loaded_prefs.telemetry_mode_loc, sizeof(loaded_prefs.telemetry_mode_loc));   // 70
    readField(&loaded_prefs.telemetry_mode_env, sizeof(loaded_prefs.telemetry_mode_env));   // 71
    readField(&loaded_prefs.rx_delay_base, sizeof(loaded_prefs.rx_delay_base));             // 72
    readField(&loaded_prefs.advert_loc_policy, sizeof(loaded_prefs.advert_loc_policy));     // 76
    readField(&loaded_prefs.multi_acks, sizeof(loaded_prefs.multi_acks));                   // 77
    readField(&loaded_prefs.path_hash_mode, sizeof(loaded_prefs.path_hash_mode));           // 78
    readField(pad, 1);                                                                      // 79
    readField(&loaded_prefs.ble_pin, sizeof(loaded_prefs.ble_pin));                         // 80
    readOptionalField(&loaded_prefs.buzzer_quiet,
                      sizeof(loaded_prefs.buzzer_quiet));                                  // 84
    readOptionalField(&loaded_prefs.gps_enabled,
                      sizeof(loaded_prefs.gps_enabled));                                   // 85
    readOptionalField(&loaded_prefs.gps_interval,
                      sizeof(loaded_prefs.gps_interval));                                  // 86
    readOptionalField(&loaded_prefs.autoadd_config,
                      sizeof(loaded_prefs.autoadd_config));                                // 90
    readOptionalField(&loaded_prefs.autoadd_max_hops,
                      sizeof(loaded_prefs.autoadd_max_hops));                              // 91
    readOptionalField(&loaded_prefs.rx_boosted_gain,
                      sizeof(loaded_prefs.rx_boosted_gain));                               // 92
    readOptionalField(loaded_prefs.default_scope_name,
                      sizeof(loaded_prefs.default_scope_name));                            // 93
    readOptionalField(loaded_prefs.default_scope_key,
                      sizeof(loaded_prefs.default_scope_key));                             // 124
    readOptionalField(&loaded_prefs.radio_fem_rxgain,
                      sizeof(loaded_prefs.radio_fem_rxgain));                              // 140
    readOptionalField(&loaded_prefs.radio_fem_rxgain_override,
                      sizeof(loaded_prefs.radio_fem_rxgain_override));                     // 141
    readOptionalField(&loaded_prefs.vibe_quiet,
                      sizeof(loaded_prefs.vibe_quiet));                                    // 142
    readOptionalField(&loaded_prefs.radio_fem_txgain,
                      sizeof(loaded_prefs.radio_fem_txgain));                              // 143
    readOptionalField(&loaded_prefs.rx_powersaving_enabled,
                      sizeof(loaded_prefs.rx_powersaving_enabled));                        // 144
    readOptionalField(&loaded_prefs.rx_ps_rx_us,
                      sizeof(loaded_prefs.rx_ps_rx_us));                                   // 145
    readOptionalField(&loaded_prefs.rx_ps_sleep_us,
                      sizeof(loaded_prefs.rx_ps_sleep_us));                                // 149
    readOptionalField(&loaded_prefs.rx_ps_level,
                      sizeof(loaded_prefs.rx_ps_level));                                   // 153
    readOptionalField(&loaded_prefs.rx_ps_preamble,
                      sizeof(loaded_prefs.rx_ps_preamble));                                // 154
    readOptionalField(&loaded_prefs.powersaving_enabled,
                      sizeof(loaded_prefs.powersaving_enabled));                           // 155
    readOptionalField(&loaded_prefs.wifi_enabled,
                      sizeof(loaded_prefs.wifi_enabled));                                  // 156
    readOptionalField(&loaded_prefs.powersaving_policy_version,
                      sizeof(loaded_prefs.powersaving_policy_version));                    // 157
    readOptionalField(&loaded_prefs.usb_logging_enabled,
                      sizeof(loaded_prefs.usb_logging_enabled));                           // 158
    readOptionalField(loaded_prefs.bluetooth_name,
                      sizeof(loaded_prefs.bluetooth_name));                               // 159
    readOptionalField(&loaded_prefs.display_rotation_degrees,
                      sizeof(loaded_prefs.display_rotation_degrees));                      // 191
    readOptionalField(&loaded_prefs.cad_enabled,
                      sizeof(loaded_prefs.cad_enabled));                                   // 193
    readOptionalField(&loaded_prefs.cad_scan_timeout_ms,
                      sizeof(loaded_prefs.cad_scan_timeout_ms));                           // 194
    readOptionalField(&loaded_prefs.cad_retry_delay_ms,
                      sizeof(loaded_prefs.cad_retry_delay_ms));                            // 196
    readOptionalField(&loaded_prefs.cad_max_duration_ms,
                      sizeof(loaded_prefs.cad_max_duration_ms));                           // 198
    readOptionalField(&loaded_prefs.bluetooth_mac_mode,
                      sizeof(loaded_prefs.bluetooth_mac_mode));                           // 200
    readOptionalField(loaded_prefs.bluetooth_mac,
                      sizeof(loaded_prefs.bluetooth_mac));                                // 201
    readOptionalField(&loaded_prefs.bluetooth_stealth_peer_type,
                      sizeof(loaded_prefs.bluetooth_stealth_peer_type));                  // 207
    readOptionalField(loaded_prefs.bluetooth_stealth_peer,
                      sizeof(loaded_prefs.bluetooth_stealth_peer));                       // 208
    readOptionalField(&loaded_prefs.bluetooth_stealth_mode,
                      sizeof(loaded_prefs.bluetooth_stealth_mode));                       // 214
    readOptionalField(&loaded_prefs.tx_delay_factor, sizeof(loaded_prefs.tx_delay_factor));
    readOptionalField(&loaded_prefs.direct_tx_delay_factor, sizeof(loaded_prefs.direct_tx_delay_factor));
    readOptionalField(&loaded_prefs.interference_threshold, sizeof(loaded_prefs.interference_threshold));
    readOptionalField(&loaded_prefs.agc_reset_interval, sizeof(loaded_prefs.agc_reset_interval));
    readOptionalField(&loaded_prefs.tz_offset, sizeof(loaded_prefs.tz_offset));
#ifdef TBEAM_1W
    readOptionalField(loaded_prefs.fan_mode, sizeof(loaded_prefs.fan_mode));
    readOptionalField(&loaded_prefs.fan_lo, sizeof(loaded_prefs.fan_lo));
    readOptionalField(&loaded_prefs.fan_hi, sizeof(loaded_prefs.fan_hi));
    loaded_prefs.fan_mode[sizeof(loaded_prefs.fan_mode) - 1] = 0;
#endif
#if defined(RP2040_PLATFORM) && defined(ENABLE_WIFI_INTERFACE)
    readOptionalField(loaded_prefs.wifi_ssid, sizeof(loaded_prefs.wifi_ssid));
    readOptionalField(loaded_prefs.wifi_pwd, sizeof(loaded_prefs.wifi_pwd));
    loaded_prefs.wifi_ssid[sizeof(loaded_prefs.wifi_ssid) - 1] = 0;
    loaded_prefs.wifi_pwd[sizeof(loaded_prefs.wifi_pwd) - 1] = 0;
#endif

    // Any bytes left over form only part of a historically appended field.
    // Preserve the file and defaults rather than treating that tail as EOF.
    success = success && file.available() == 0;
    file.close();
    if (!success) return false;
    _prefs = loaded_prefs;
    node_lat = loaded_lat;
    node_lon = loaded_lon;
    return true;
  }
  return false;
}

bool DataStore::savePrefs(const CompanionNodePrefs& _prefs, double node_lat, double node_lon) {
  if (_prefs_load_incomplete) return false;
#if defined(ESP32_PLATFORM)
  if (!promoteCompanionRecoveryFile(_fs, "/new_prefs", _prefs_recovery_source)) return false;
#endif
#if defined(NRF52_PLATFORM)
  if (_primary_storage_unavailable) return false;
#endif
#if defined(NRF52_PLATFORM) || defined(STM32_PLATFORM)
  mesh::AtomicFileWriter file(_fs, "/new_prefs");
#elif defined(ESP32_PLATFORM)
  mesh::ContactFileTransaction file(_fs, "/new_prefs", companionPathPresence);
#elif defined(RP2040_PLATFORM)
  mesh::ContactFileTransaction file(_fs, "/new_prefs");
#else
  File file = openWrite(_fs, "/new_prefs");
#endif
  if (file) {
    uint8_t pad[8];
    memset(pad, 0, sizeof(pad));

    bool success = file.write((uint8_t *)&_prefs.airtime_factor, sizeof(float)) == sizeof(float); // 0
    success = success && file.write((uint8_t *)_prefs.node_name, sizeof(_prefs.node_name)) == sizeof(_prefs.node_name); // 4
    success = success && file.write(pad, 4) == 4;                                            // 36
    success = success && file.write((uint8_t *)&node_lat, sizeof(node_lat)) == sizeof(node_lat); // 40
    success = success && file.write((uint8_t *)&node_lon, sizeof(node_lon)) == sizeof(node_lon); // 48
    success = success && file.write((uint8_t *)&_prefs.freq, sizeof(_prefs.freq)) == sizeof(_prefs.freq); // 56
    success = success && file.write((uint8_t *)&_prefs.sf, sizeof(_prefs.sf)) == sizeof(_prefs.sf); // 60
    success = success && file.write((uint8_t *)&_prefs.cr, sizeof(_prefs.cr)) == sizeof(_prefs.cr); // 61
    success = success && file.write((uint8_t *)&_prefs.client_repeat, sizeof(_prefs.client_repeat)) == sizeof(_prefs.client_repeat); // 62
    success = success && file.write((uint8_t *)&_prefs.manual_add_contacts, sizeof(_prefs.manual_add_contacts)) == sizeof(_prefs.manual_add_contacts); // 63
    success = success && file.write((uint8_t *)&_prefs.bw, sizeof(_prefs.bw)) == sizeof(_prefs.bw); // 64
    success = success && file.write((uint8_t *)&_prefs.tx_power_dbm, sizeof(_prefs.tx_power_dbm)) == sizeof(_prefs.tx_power_dbm); // 68
    success = success && file.write((uint8_t *)&_prefs.telemetry_mode_base, sizeof(_prefs.telemetry_mode_base)) == sizeof(_prefs.telemetry_mode_base); // 69
    success = success && file.write((uint8_t *)&_prefs.telemetry_mode_loc, sizeof(_prefs.telemetry_mode_loc)) == sizeof(_prefs.telemetry_mode_loc); // 70
    success = success && file.write((uint8_t *)&_prefs.telemetry_mode_env, sizeof(_prefs.telemetry_mode_env)) == sizeof(_prefs.telemetry_mode_env); // 71
    success = success && file.write((uint8_t *)&_prefs.rx_delay_base, sizeof(_prefs.rx_delay_base)) == sizeof(_prefs.rx_delay_base); // 72
    success = success && file.write((uint8_t *)&_prefs.advert_loc_policy, sizeof(_prefs.advert_loc_policy)) == sizeof(_prefs.advert_loc_policy); // 76
    success = success && file.write((uint8_t *)&_prefs.multi_acks, sizeof(_prefs.multi_acks)) == sizeof(_prefs.multi_acks); // 77
    success = success && file.write((uint8_t *)&_prefs.path_hash_mode, sizeof(_prefs.path_hash_mode)) == sizeof(_prefs.path_hash_mode); // 78
    success = success && file.write(pad, 1) == 1;                                            // 79
    success = success && file.write((uint8_t *)&_prefs.ble_pin, sizeof(_prefs.ble_pin)) == sizeof(_prefs.ble_pin); // 80
    success = success && file.write((uint8_t *)&_prefs.buzzer_quiet, sizeof(_prefs.buzzer_quiet)) == sizeof(_prefs.buzzer_quiet); // 84
    success = success && file.write((uint8_t *)&_prefs.gps_enabled, sizeof(_prefs.gps_enabled)) == sizeof(_prefs.gps_enabled); // 85
    success = success && file.write((uint8_t *)&_prefs.gps_interval, sizeof(_prefs.gps_interval)) == sizeof(_prefs.gps_interval); // 86
    success = success && file.write((uint8_t *)&_prefs.autoadd_config, sizeof(_prefs.autoadd_config)) == sizeof(_prefs.autoadd_config); // 87
    success = success && file.write((uint8_t *)&_prefs.autoadd_max_hops, sizeof(_prefs.autoadd_max_hops)) == sizeof(_prefs.autoadd_max_hops); // 88
    success = success && file.write((uint8_t *)&_prefs.rx_boosted_gain, sizeof(_prefs.rx_boosted_gain)) == sizeof(_prefs.rx_boosted_gain); // 89
    success = success && file.write((uint8_t *)_prefs.default_scope_name, sizeof(_prefs.default_scope_name)) == sizeof(_prefs.default_scope_name); // 90
    success = success && file.write((uint8_t *)_prefs.default_scope_key, sizeof(_prefs.default_scope_key)) == sizeof(_prefs.default_scope_key); // 121
    success = success && file.write((uint8_t *)&_prefs.radio_fem_rxgain, sizeof(_prefs.radio_fem_rxgain)) == sizeof(_prefs.radio_fem_rxgain); // 122
    success = success && file.write((uint8_t *)&_prefs.radio_fem_rxgain_override,
               sizeof(_prefs.radio_fem_rxgain_override)) == sizeof(_prefs.radio_fem_rxgain_override); // 123
    success = success && file.write((uint8_t *)&_prefs.vibe_quiet,
               sizeof(_prefs.vibe_quiet)) == sizeof(_prefs.vibe_quiet);                    // 124
    success = success && file.write((uint8_t *)&_prefs.radio_fem_txgain,
               sizeof(_prefs.radio_fem_txgain)) == sizeof(_prefs.radio_fem_txgain);        // 125
    success = success && file.write((uint8_t *)&_prefs.rx_powersaving_enabled,
               sizeof(_prefs.rx_powersaving_enabled)) == sizeof(_prefs.rx_powersaving_enabled); // 126
    success = success && file.write((uint8_t *)&_prefs.rx_ps_rx_us,
               sizeof(_prefs.rx_ps_rx_us)) == sizeof(_prefs.rx_ps_rx_us);                  // 127
    success = success && file.write((uint8_t *)&_prefs.rx_ps_sleep_us,
               sizeof(_prefs.rx_ps_sleep_us)) == sizeof(_prefs.rx_ps_sleep_us);            // 131
    success = success && file.write((uint8_t *)&_prefs.rx_ps_level,
               sizeof(_prefs.rx_ps_level)) == sizeof(_prefs.rx_ps_level);                  // 135
    success = success && file.write((uint8_t *)&_prefs.rx_ps_preamble,
               sizeof(_prefs.rx_ps_preamble)) == sizeof(_prefs.rx_ps_preamble);            // 136
    success = success && file.write((uint8_t *)&_prefs.powersaving_enabled,
               sizeof(_prefs.powersaving_enabled)) == sizeof(_prefs.powersaving_enabled); // 137
    success = success && file.write((uint8_t *)&_prefs.wifi_enabled,
               sizeof(_prefs.wifi_enabled)) == sizeof(_prefs.wifi_enabled);               // 138
    success = success && file.write((uint8_t *)&_prefs.powersaving_policy_version,
               sizeof(_prefs.powersaving_policy_version))
               == sizeof(_prefs.powersaving_policy_version);                              // 139
    success = success && file.write((uint8_t *)&_prefs.usb_logging_enabled,
               sizeof(_prefs.usb_logging_enabled))
               == sizeof(_prefs.usb_logging_enabled);                                    // 140
    success = success && file.write((uint8_t *)_prefs.bluetooth_name,
               sizeof(_prefs.bluetooth_name)) == sizeof(_prefs.bluetooth_name);          // 141
    success = success && file.write(
               (uint8_t *)&_prefs.display_rotation_degrees,
               sizeof(_prefs.display_rotation_degrees))
               == sizeof(_prefs.display_rotation_degrees);
    success = success && file.write((uint8_t *)&_prefs.cad_enabled,
               sizeof(_prefs.cad_enabled)) == sizeof(_prefs.cad_enabled);
    success = success && file.write((uint8_t *)&_prefs.cad_scan_timeout_ms,
               sizeof(_prefs.cad_scan_timeout_ms))
               == sizeof(_prefs.cad_scan_timeout_ms);
    success = success && file.write((uint8_t *)&_prefs.cad_retry_delay_ms,
               sizeof(_prefs.cad_retry_delay_ms))
               == sizeof(_prefs.cad_retry_delay_ms);
    success = success && file.write((uint8_t *)&_prefs.cad_max_duration_ms,
               sizeof(_prefs.cad_max_duration_ms))
               == sizeof(_prefs.cad_max_duration_ms);
    success = success && file.write((uint8_t *)&_prefs.bluetooth_mac_mode,
               sizeof(_prefs.bluetooth_mac_mode))
               == sizeof(_prefs.bluetooth_mac_mode);
    success = success && file.write((uint8_t *)_prefs.bluetooth_mac,
               sizeof(_prefs.bluetooth_mac)) == sizeof(_prefs.bluetooth_mac);
    success = success && file.write(
               (uint8_t *)&_prefs.bluetooth_stealth_peer_type,
               sizeof(_prefs.bluetooth_stealth_peer_type))
               == sizeof(_prefs.bluetooth_stealth_peer_type);
    success = success && file.write(
               (uint8_t *)_prefs.bluetooth_stealth_peer,
               sizeof(_prefs.bluetooth_stealth_peer))
               == sizeof(_prefs.bluetooth_stealth_peer);
    success = success && file.write(
               (uint8_t *)&_prefs.bluetooth_stealth_mode,
               sizeof(_prefs.bluetooth_stealth_mode))
               == sizeof(_prefs.bluetooth_stealth_mode);

    success = success && file.write((uint8_t *)&_prefs.tx_delay_factor,
        sizeof(_prefs.tx_delay_factor)) == sizeof(_prefs.tx_delay_factor);
    success = success && file.write((uint8_t *)&_prefs.direct_tx_delay_factor,
        sizeof(_prefs.direct_tx_delay_factor)) == sizeof(_prefs.direct_tx_delay_factor);
    success = success && file.write((uint8_t *)&_prefs.interference_threshold,
        sizeof(_prefs.interference_threshold)) == sizeof(_prefs.interference_threshold);
    success = success && file.write((uint8_t *)&_prefs.agc_reset_interval,
        sizeof(_prefs.agc_reset_interval)) == sizeof(_prefs.agc_reset_interval);
    success = success && file.write((uint8_t *)&_prefs.tz_offset,
        sizeof(_prefs.tz_offset)) == sizeof(_prefs.tz_offset);
#ifdef TBEAM_1W
    success = success && file.write((uint8_t *)_prefs.fan_mode,
        sizeof(_prefs.fan_mode)) == sizeof(_prefs.fan_mode);
    success = success && file.write((uint8_t *)&_prefs.fan_lo,
        sizeof(_prefs.fan_lo)) == sizeof(_prefs.fan_lo);
    success = success && file.write((uint8_t *)&_prefs.fan_hi,
        sizeof(_prefs.fan_hi)) == sizeof(_prefs.fan_hi);
#endif
#if defined(RP2040_PLATFORM) && defined(ENABLE_WIFI_INTERFACE)
    success = success && file.write((uint8_t *)_prefs.wifi_ssid,
        sizeof(_prefs.wifi_ssid)) == sizeof(_prefs.wifi_ssid);
    success = success && file.write((uint8_t *)_prefs.wifi_pwd,
        sizeof(_prefs.wifi_pwd)) == sizeof(_prefs.wifi_pwd);
#endif

#if defined(NRF52_PLATFORM) || defined(STM32_PLATFORM) || defined(ESP32_PLATFORM) || defined(RP2040_PLATFORM)
    success = file.commit(success);
    if (!success) MESH_DEBUG_PRINTLN("DataStore: atomic preferences write failed");
#else
    file.close();
#endif
    return success;
  }
  return false;
}

static bool serializeContactRecord(const ContactInfo& c,
                                   uint8_t out[mesh::storage::CONTACT_RECORD_SIZE]) {
  size_t offset = 0;
  memcpy(&out[offset], c.id.pub_key, 32); offset += 32;
  memcpy(&out[offset], c.name, 32); offset += 32;
  out[offset++] = c.type;
  out[offset++] = c.flags;
  out[offset++] = mesh::encodeRadioTxPolicy(c.tx_radio);
  memcpy(&out[offset], &c.sync_since, 4); offset += 4;
  out[offset++] = c.out_path_len;
  memcpy(&out[offset], &c.last_advert_timestamp, 4); offset += 4;
  if (!c.copyPathTo(&out[offset])) return false;
  offset += 64;
  memcpy(&out[offset], &c.lastmod, 4); offset += 4;
  memcpy(&out[offset], &c.gps_lat, 4); offset += 4;
  memcpy(&out[offset], &c.gps_lon, 4);
  return true;
}

static bool deserializeContactRecord(
    const uint8_t in[mesh::storage::CONTACT_RECORD_SIZE], ContactInfo& c,
    uint16_t path_source, bool* path_unavailable = nullptr) {
  if (path_unavailable) *path_unavailable = false;
  size_t offset = 0;
  uint8_t pub_key[32];
  memcpy(pub_key, &in[offset], 32); offset += 32;
  memcpy(c.name, &in[offset], 32); offset += 32;
  c.name[sizeof(c.name) - 1] = 0;
  c.type = in[offset++];
  c.flags = in[offset++];
  c.tx_radio = mesh::decodeRadioTxPolicy(in[offset++]);
  memcpy(&c.sync_since, &in[offset], 4); offset += 4;
  c.out_path_len = in[offset++];
  memcpy(&c.last_advert_timestamp, &in[offset], 4); offset += 4;
#if MESH_CONTACT_CACHE
  if (!c.path_ref.bind(path_source, &in[offset])) {
    if (path_unavailable) *path_unavailable = true;
    return false;
  }
#else
  (void)path_source;
  if (!c.setRawPath(&in[offset])) return false;
#endif
  offset += 64;
  memcpy(&c.lastmod, &in[offset], 4); offset += 4;
  memcpy(&c.gps_lat, &in[offset], 4); offset += 4;
  memcpy(&c.gps_lon, &in[offset], 4);
  c.id = mesh::Identity(pub_key);
  c.shared_secret_valid = false;
  return c.out_path_len == OUT_PATH_UNKNOWN
      || mesh::Packet::isValidPathLen(c.out_path_len);
}

#if defined(NRF52_PLATFORM)
static const char* CONTACT_MIGRATION_MARKER = "/contacts4.mig";
static const char* SECONDARY_MIGRATION_JOURNAL = "/.extrafs.mig";
static const uint8_t SECONDARY_MIGRATION_PENDING = 1;
static const uint8_t SECONDARY_MIGRATION_COMMITTED = 2;
#if defined(MESHCORE_EXTRAFS_HIL)
static const char* HIL_CONTACT_PAGE_FAILURE_MARKER = "/__hil.readfail";
static const uint8_t HIL_CONTACT_STAT_FAILURE_FLAG = 0x80;
#endif

static mesh::storage::ContactPathState statContactPath(
    FILESYSTEM* fs, const char* path, uint32_t* size) {
  if (fs == nullptr || path == nullptr) {
    return mesh::storage::ContactPathState::IO_ERROR;
  }

  struct lfs_info info;
  fs->_lockFS();
  const int result = lfs_stat(fs->_getFS(), path, &info);
  fs->_unlockFS();
  const mesh::storage::ContactPathState state =
      mesh::storage::classifyContactPathStat(result, LFS_ERR_NOENT);
  if (state == mesh::storage::ContactPathState::PRESENT && size != nullptr) {
    *size = info.size;
  }
  if (state == mesh::storage::ContactPathState::IO_ERROR) {
    MESH_DEBUG_PRINTLN(
        "DataStore: storage path stat failed for %s: %d", path, result);
  }
  return state;
}

static bool contactPathPresence(FILESYSTEM* fs, const char* path,
                                bool& present, uint32_t* size) {
  const mesh::storage::ContactPathState state = statContactPath(fs, path, size);
  present = state == mesh::storage::ContactPathState::PRESENT;
  return state != mesh::storage::ContactPathState::IO_ERROR;
}

static void makeContactPagePath(uint8_t page, char path[24]) {
  snprintf(path, 24, "/contacts4_%02u", (unsigned)page);
}

static void discardInvalidContactPage(FILESYSTEM* fs, const char* path,
                                      uint8_t page) {
  // The page has already failed size/format/CRC validation and cannot be a
  // recovery source. Remove it so the atomic replacement only needs one free
  // page; retaining full-size .bad copies can otherwise exhaust a 100 KiB
  // ExtraFS and make self-repair impossible.
  if (!fs->remove(path)) {
    MESH_DEBUG_PRINTLN("DataStore: could not remove invalid contact page %u", page);
  }
}

void DataStore::resetContactPageState(bool clear_incomplete) {
  _contact_slots.clear();
  _dirty_contact_pages.clearAll();
  _unread_contact_pages.clearAll();
  if (clear_incomplete) {
    _contact_load_incomplete = false;
#if MESH_CONTACT_CACHE
    _cache_load_incomplete = false;
#endif
  }
  memset(_contact_page_generations, 0, sizeof(_contact_page_generations));
  _legacy_contacts_pending_cleanup = false;
  _legacy_migration_ready = false;
  _legacy_contact_count = 0;
}

bool DataStore::prepareLegacyContactMigration() {
  FILESYSTEM* fs = _getContactsChannelsFS();
  bool marker_exists = false;
  if (!contactPathPresence(fs, CONTACT_MIGRATION_MARKER, marker_exists)) {
    return false;
  }
  if (marker_exists) {
    _legacy_migration_ready = true;
    return true;
  }

  // With no marker, page files can be leftovers from a newer firmware followed
  // by a downgrade that rewrote /contacts3. The complete legacy file remains
  // authoritative while these are removed, so a reset at any point is safe.
  bool clean = true;
  for (uint8_t page = 0; page < mesh::storage::CONTACT_PAGE_COUNT; page++) {
    char path[24];
    makeContactPagePath(page, path);
    bool path_exists = false;
    if (!contactPathPresence(fs, path, path_exists)) return false;
    if (path_exists && !fs->remove(path)) clean = false;

    char temp_path[28];
    snprintf(temp_path, sizeof(temp_path), "%s.tmp", path);
    bool temp_exists = false;
    if (!contactPathPresence(fs, temp_path, temp_exists)) return false;
    if (temp_exists && !fs->remove(temp_path)) clean = false;
  }
  if (!clean) {
    MESH_DEBUG_PRINTLN("DataStore: could not clear stale contact pages before migration");
    return false;
  }

  mesh::AtomicFileWriter marker(fs, CONTACT_MIGRATION_MARKER);
  _legacy_migration_ready = marker.commit(true);
  if (!_legacy_migration_ready) {
    MESH_DEBUG_PRINTLN("DataStore: could not create contact migration marker");
  }
  return _legacy_migration_ready;
}

bool DataStore::loadContactPages(DataStoreHost* host, uint16_t minimum_slot,
                                 uint32_t expected_page_mask) {
  bool any_page_file = false;
  FILESYSTEM* fs = _getContactsChannelsFS();
  auto quarantineUnreadPage = [this](uint8_t page) {
    // Fail closed if an otherwise present page cannot be read. Reserving its
    // slots prevents a new contact from overwriting records which a later boot
    // may recover successfully.
    for (uint8_t index = 0;
         index < mesh::storage::CONTACTS_PER_PAGE; index++) {
      _contact_slots.reserve(
          (uint16_t)page * mesh::storage::CONTACTS_PER_PAGE + index);
    }
    _unread_contact_pages.mark(page);
    _contact_load_incomplete = true;
  };

  for (uint8_t page = 0; page < mesh::storage::CONTACT_PAGE_COUNT; page++) {
    if ((expected_page_mask & (1UL << page)) == 0) continue;

    char path[24];
    makeContactPagePath(page, path);
    // The first source-selection pass proved this page existed. Do not ask
    // exists() a second time and silently reinterpret a transient failure as
    // an empty slot range; opening the captured page either succeeds or puts
    // that range into fail-closed quarantine.
    any_page_file = true;

#if defined(MESHCORE_EXTRAFS_HIL)
    // Direct-attached HIL can request one fail-closed boot without damaging
    // the page. Consume the marker before quarantine so the next reboot
    // exercises normal recovery from the untouched source.
    if (fs->exists(HIL_CONTACT_PAGE_FAILURE_MARKER)) {
      File marker = openRead(fs, HIL_CONTACT_PAGE_FAILURE_MARKER);
      const int fail_page = marker ? marker.read() : -1;
      if (marker) marker.close();
      if (fail_page == page && fs->remove(HIL_CONTACT_PAGE_FAILURE_MARKER)
          && !fs->exists(HIL_CONTACT_PAGE_FAILURE_MARKER)) {
        MESH_DEBUG_PRINTLN(
            "DataStore: HIL quarantining contact page %u", page);
        quarantineUnreadPage(page);
        continue;
      }
    }
#endif

    File file = openRead(fs, path);
    if (!file) {
      MESH_DEBUG_PRINTLN("DataStore: contact page %u could not be opened", page);
      quarantineUnreadPage(page);
      continue;
    }
    if (file.size() != mesh::storage::CONTACT_PAGE_FILE_SIZE) {
      MESH_DEBUG_PRINTLN("DataStore: ignoring invalid contact page %u", page);
      file.close();
      discardInvalidContactPage(fs, path, page);
      _dirty_contact_pages.mark(page);
      continue;
    }

    uint8_t raw_header[mesh::storage::CONTACT_PAGE_HEADER_SIZE];
    mesh::storage::ContactPageHeader header;
    const bool header_read =
        file.read(raw_header, sizeof(raw_header)) == sizeof(raw_header);
    const bool header_valid = header_read
        && mesh::storage::decodeContactPageHeader(raw_header, page, header);
    if (!header_read) {
      // A short read is not evidence that the atomically committed page is
      // corrupt. Leave it untouched so a later boot can retry it.
      MESH_DEBUG_PRINTLN(
          "DataStore: contact page %u header could not be read", page);
      file.close();
      quarantineUnreadPage(page);
      continue;
    }
    if (!header_valid) {
      MESH_DEBUG_PRINTLN("DataStore: contact page %u failed CRC/format validation", page);
      file.close();
      discardInvalidContactPage(fs, path, page);
      _dirty_contact_pages.mark(page);
      continue;
    }

    // Keep a verified page snapshot in heap memory while constructing the RAM
    // table. A second series of filesystem seeks could fail after only part of
    // the page had been loaded; later rewriting that partial RAM view would
    // permanently delete otherwise valid contacts.
    uint8_t* payload =
        (uint8_t*)malloc(mesh::storage::CONTACT_PAGE_PAYLOAD_SIZE);
    if (payload == nullptr) {
      MESH_DEBUG_PRINTLN(
          "DataStore: no memory to load contact page %u", page);
      file.close();
      quarantineUnreadPage(page);
      continue;
    }
    const bool payload_read =
        file.read(payload, mesh::storage::CONTACT_PAGE_PAYLOAD_SIZE)
        == mesh::storage::CONTACT_PAGE_PAYLOAD_SIZE;
    file.close();
    if (!payload_read) {
      MESH_DEBUG_PRINTLN(
          "DataStore: contact page %u payload could not be read", page);
      free(payload);
      quarantineUnreadPage(page);
      continue;
    }
    const uint32_t crc = mesh::storage::updateCRC32(
        0xFFFFFFFFUL, payload, mesh::storage::CONTACT_PAGE_PAYLOAD_SIZE);
    if (crc != header.payload_crc) {
      MESH_DEBUG_PRINTLN("DataStore: contact page %u failed CRC/format validation", page);
      free(payload);
      discardInvalidContactPage(fs, path, page);
      _dirty_contact_pages.mark(page);
      continue;
    }

    if ((header.occupied & ~mesh::storage::contactPageValidSlotMask()) != 0) {
      MESH_DEBUG_PRINTLN(
          "DataStore: repairing contact page %u high occupancy bits", page);
      _dirty_contact_pages.mark(page);
    }

    _contact_page_generations[page] = header.generation;
    for (uint8_t index = 0; index < mesh::storage::CONTACTS_PER_PAGE; index++) {
      const uint16_t slot = (uint16_t)page * mesh::storage::CONTACTS_PER_PAGE + index;
      const uint8_t* record =
          &payload[(uint16_t)index * mesh::storage::CONTACT_RECORD_SIZE];

      // The payload CRC covers every record, so occupancy can be reconstructed
      // without trusting the separate header bitmask. This recovers a contact
      // hidden by a flipped mask bit and ignores an empty slot exposed by one;
      // the dirty page is later rewritten with the repaired mask.
      const bool has_data = mesh::storage::contactRecordHasData(record);
      const bool marked_occupied =
          (header.occupied & (1UL << index)) != 0;
      if (has_data != marked_occupied) {
        MESH_DEBUG_PRINTLN(
            "DataStore: repairing contact page %u occupancy slot %u",
            page, index);
        _dirty_contact_pages.mark(page);
      }
      if (!has_data
          || !mesh::storage::loadSlotFromMigratedPage(slot, minimum_slot)) {
        continue;
      }

      ContactInfo contact;
      bool path_unavailable = false;
      const bool valid = deserializeContactRecord(record, contact,
          0x8000 | slot, &path_unavailable);
      if (path_unavailable) {
        // Cache exhaustion is not a corrupt record. Keep the complete page
        // on disk and refuse changes until it can be loaded after reboot.
        _contact_load_incomplete = true;
        _dirty_contact_pages.clearAll();
        free(payload);
        return true;
      }
      if (!valid || !_contact_slots.reserve(slot)) {
        MESH_DEBUG_PRINTLN("DataStore: contact page %u slot %u is invalid/duplicate", page, index);
        _dirty_contact_pages.mark(page);
        continue;
      }
      contact.storage_slot = slot;
      if (!host->onContactLoaded(contact)) {
        _contact_slots.release(slot);
        // A host capacity refusal is not a filesystem read error, but the RAM
        // table is still not a complete representation of the durable page.
        // Preserve the page and veto every mutation/rewrite until reboot.
        MESH_DEBUG_PRINTLN(
            "DataStore: contact host refused page %u slot %u; load incomplete",
            page, index);
        _contact_load_incomplete = true;
        _dirty_contact_pages.clearAll();
        free(payload);
        return true;
      }
    }
    free(payload);
  }
  if (hasIncompleteContactLoad()) {
    // Occupancy/header repairs discovered elsewhere in this pass must wait for
    // a clean reboot too. Rewriting any page from an incomplete contact table
    // can turn a transient read failure into durable cross-page data loss.
    _dirty_contact_pages.clearAll();
  }
  return any_page_file;
}

bool DataStore::writeContactPage(DataStoreHost* host, uint8_t page,
                                 bool (*filter)(const ContactInfo& c)) {
  if (page >= mesh::storage::CONTACT_PAGE_COUNT) return false;

  // The nRF52 Arduino loop task has only a 4 KiB stack.  Keep pointers to this
  // page's contacts and stream one 152-byte record at a time instead of
  // allocating the complete 3.8 KiB payload on that stack.
  ContactInfo* page_contacts[mesh::storage::CONTACTS_PER_PAGE];
  memset(page_contacts, 0, sizeof(page_contacts));
  uint32_t occupied = 0;

  for (uint32_t index = 0;; index++) {
    ContactInfo* contact = host->getContactForStore(index);
    if (contact == NULL) break;
    if ((filter && !filter(*contact))
        || contact->storage_slot == mesh::storage::CONTACT_SLOT_NONE
        || contact->storage_slot / mesh::storage::CONTACTS_PER_PAGE != page) {
      continue;
    }

    const uint8_t page_slot = contact->storage_slot % mesh::storage::CONTACTS_PER_PAGE;
    page_contacts[page_slot] = contact;
    occupied |= 1UL << page_slot;
  }

#if MESH_CONTACT_CACHE
  auto& paths = mesh::contactPathStorage();
  paths.beginCommit();
  for (auto* contact : page_contacts) if (contact) paths.mark(contact->path_ref.handle());
  const uint16_t first_slot = page * mesh::storage::CONTACTS_PER_PAGE;
  if (!paths.preserveSnapshots(0x8000 | first_slot, mesh::storage::CONTACTS_PER_PAGE)
      || !paths.preserveSnapshots(first_slot, mesh::storage::CONTACTS_PER_PAGE)) {
    paths.endCommit(false);
    return false;
  }
#endif
  uint32_t payload_crc = 0xFFFFFFFFUL;
  uint8_t record[mesh::storage::CONTACT_RECORD_SIZE];
  for (uint8_t slot = 0; slot < mesh::storage::CONTACTS_PER_PAGE; slot++) {
    memset(record, 0, sizeof(record));
    if (page_contacts[slot] != nullptr) {
      if (!serializeContactRecord(*page_contacts[slot], record)) {
#if MESH_CONTACT_CACHE
        mesh::contactPathStorage().endCommit(false);
#endif
        return false;
      }
    }
    payload_crc = mesh::storage::updateCRC32(payload_crc, record, sizeof(record));
  }

  mesh::storage::ContactPageHeader header;
  header.page_index = page;
  header.occupied = occupied;
  header.generation = _contact_page_generations[page] + 1;
  header.payload_crc = payload_crc;
  uint8_t raw_header[mesh::storage::CONTACT_PAGE_HEADER_SIZE];
  mesh::storage::encodeContactPageHeader(raw_header, header);

  char path[24];
  makeContactPagePath(page, path);
  mesh::AtomicFileWriter writer(_getContactsChannelsFS(), path);
  bool wrote = writer
      && writer.write(raw_header, sizeof(raw_header)) == sizeof(raw_header);
  for (uint8_t slot = 0; wrote && slot < mesh::storage::CONTACTS_PER_PAGE; slot++) {
    memset(record, 0, sizeof(record));
    if (page_contacts[slot] != nullptr) {
      if (!serializeContactRecord(*page_contacts[slot], record)) {
#if MESH_CONTACT_CACHE
        mesh::contactPathStorage().endCommit(false);
#endif
        return false;
      }
    }
    wrote = writer.write(record, sizeof(record)) == sizeof(record);
  }
  if (!writer.commit(wrote)) {
#if MESH_CONTACT_CACHE
    paths.endCommit(false);
#endif
    MESH_DEBUG_PRINTLN("DataStore: atomic contact page %u write failed", page);
    return false;
  }

#if MESH_CONTACT_CACHE
  for (auto* contact : page_contacts) if (contact)
    paths.publish(contact->path_ref.handle(), 0x8000 | contact->storage_slot);
  paths.endCommit(true);
#endif
  _contact_page_generations[page] = header.generation;
  return true;
}
#endif

void DataStore::loadContacts(DataStoreHost* host) {
#if !defined(NRF52_PLATFORM) && !MESH_CONTACT_CACHE
  if (_uncached_contact_load_incomplete) return;
#endif
#if MESH_CONTACT_CACHE
  _cache_host = host;
  mesh::contactPathStorage().attach(this);
#if MESH_CONTACT_SECRET_FLASH_CACHE
  mesh::contactSecretCache().attach(this);
#endif
  if (_cache_load_incomplete) return;
#if defined(ESP32_PLATFORM)
  _contact_path_reader.close();
#endif
#endif
#if !defined(NRF52_PLATFORM)
  bool contacts_exist = false;
  bool contacts_metadata_ready = companionPathPresence(
      _getContactsChannelsFS(), "/contacts3", contacts_exist);
#endif
#if defined(ESP32_PLATFORM) || defined(RP2040_PLATFORM)
  bool backup_exists = false;
  contacts_metadata_ready = contacts_metadata_ready && companionPathPresence(
      _getContactsChannelsFS(), "/contacts3.bak", backup_exists);
  const bool contacts_required = contacts_exist || backup_exists;
  contacts_metadata_ready = contacts_metadata_ready
      && mesh::ContactFileTransaction::recover(_getContactsChannelsFS(), "/contacts3")
      && companionPathPresence(_getContactsChannelsFS(), "/contacts3", contacts_exist)
      && (!contacts_required || contacts_exist);
#endif
#if !defined(NRF52_PLATFORM)
  if (!contacts_metadata_ready) {
    MESH_DEBUG_PRINTLN("DataStore: contact transaction recovery failed");
#if MESH_CONTACT_CACHE
    _cache_load_incomplete = true;
#else
    _uncached_contact_load_incomplete = true;
#endif
    return;
  }
#endif
#if defined(NRF52_PLATFORM)
  // loadContacts() is also used after an identity import.  Rebuild runtime
  // slot ownership from disk so stale pointers/slots from the previous in-RAM
  // contact table cannot collide with the reload.
  resetContactPageState();
  if (_contact_load_incomplete) {
    // A failed load is latched for this boot. loadContacts() is also called by
    // identity import, but only a reboot may retry storage and re-enable
    // migration/mutation; otherwise an unrelated command could clear the veto.
    MESH_DEBUG_PRINTLN(
        "DataStore: contact load remains quarantined until reboot");
    return;
  }
  FILESYSTEM* contacts_fs = _getContactsChannelsFS();
#if defined(MESHCORE_EXTRAFS_HIL)
  // Reuse the read-failure marker's high bit for a one-boot source-discovery
  // stat failure.  Consume and verify the marker before injecting the error so
  // the untouched page is recoverable on the very next reboot.  Low-bit values
  // remain armed for loadContactPages(), preserving the existing read hook.
  int hil_stat_failure_page = -1;
  bool hil_failure_marker_exists = false;
  if (!contactPathPresence(contacts_fs, HIL_CONTACT_PAGE_FAILURE_MARKER,
                           hil_failure_marker_exists)) {
    _contact_load_incomplete = true;
    return;
  }
  if (hil_failure_marker_exists) {
    File marker = openRead(contacts_fs, HIL_CONTACT_PAGE_FAILURE_MARKER);
    const int encoded_failure = marker && marker.size() == 1
        ? marker.read() : -1;
    if (marker) marker.close();
    if (encoded_failure >= 0
        && (encoded_failure & HIL_CONTACT_STAT_FAILURE_FLAG) != 0) {
      const uint8_t page = static_cast<uint8_t>(
          encoded_failure & ~HIL_CONTACT_STAT_FAILURE_FLAG);
      bool marker_remains = true;
      const bool removed = contacts_fs->remove(
          HIL_CONTACT_PAGE_FAILURE_MARKER);
      const bool verified = contactPathPresence(
          contacts_fs, HIL_CONTACT_PAGE_FAILURE_MARKER, marker_remains);
      if (page >= mesh::storage::CONTACT_PAGE_COUNT || !removed || !verified
          || marker_remains) {
        MESH_DEBUG_PRINTLN(
            "DataStore: HIL stat-failure marker could not be consumed safely");
        _contact_load_incomplete = true;
        return;
      }
      hil_stat_failure_page = page;
    }
  }
#endif
  uint32_t contact_page_presence = 0;
  for (uint8_t page = 0; page < mesh::storage::CONTACT_PAGE_COUNT; page++) {
    char path[24];
    makeContactPagePath(page, path);
    bool page_exists = false;
#if defined(MESHCORE_EXTRAFS_HIL)
    if (page == hil_stat_failure_page) {
      MESH_DEBUG_PRINTLN(
          "DataStore: HIL injecting contact page %u stat failure", page);
      _contact_load_incomplete = true;
      return;
    }
#endif
    if (!contactPathPresence(contacts_fs, path, page_exists)) {
      _contact_load_incomplete = true;
      return;
    }
    if (page_exists) {
      contact_page_presence |= 1UL << page;
    }
  }
  bool legacy_exists = false;
  bool migration_marker_exists = false;
  if (!contactPathPresence(contacts_fs, "/contacts3", legacy_exists)
      || !contactPathPresence(contacts_fs, CONTACT_MIGRATION_MARKER,
                              migration_marker_exists)) {
    _contact_load_incomplete = true;
    return;
  }
  const mesh::storage::ContactStoreSource source =
      mesh::storage::chooseContactStoreSource(
          legacy_exists, contact_page_presence != 0);
  if (source == mesh::storage::ContactStoreSource::PAGED) {
    loadContactPages(host, 0, contact_page_presence);
    // A reset after the final legacy removal may leave this harmless marker.
    // Retire it only after every page captured above loaded successfully; an
    // unread page must leave all recovery metadata untouched for reboot retry.
    if (!hasIncompleteContactLoad() && migration_marker_exists) {
      contacts_fs->remove(CONTACT_MIGRATION_MARKER);
    }
    return;
  }
  if (source == mesh::storage::ContactStoreSource::EMPTY) {
    if (migration_marker_exists) {
      contacts_fs->remove(CONTACT_MIGRATION_MARKER);
    }
    return;
  }

  // Migrate /contacts3 from the tail so the legacy prefix and completed pages
  // never need enough room to coexist in full.  A page is committed first,
  // then the corresponding legacy tail is truncated.  On a reset between
  // those operations the still-present legacy prefix wins overlapping slots.
  _legacy_contacts_pending_cleanup = true;
  _legacy_migration_ready = migration_marker_exists;
#endif

  File file = openRead(_getContactsChannelsFS(), "/contacts3");
#if !defined(NRF52_PLATFORM)
  if (!file && contacts_exist) {
#if MESH_CONTACT_CACHE
    _cache_load_incomplete = true;
#else
    _uncached_contact_load_incomplete = true;
#endif
    return;
  }
#endif
#if defined(NRF52_PLATFORM)
  if (!file) {
    // Never delete, truncate, or write alongside a legacy source that could
    // not be opened. A later reboot can retry the untouched authoritative
    // file, but this boot must not accept a mutation into a competing page.
    MESH_DEBUG_PRINTLN("DataStore: legacy contacts exist but could not be read");
    _contact_load_incomplete = true;
    return;
  }

  const size_t legacy_size = file.size();
  if (!mesh::storage::isValidLegacyContactFileSize(legacy_size)) {
    MESH_DEBUG_PRINTLN(
        "DataStore: legacy contacts size %lu is invalid; preserving source",
        (unsigned long)legacy_size);
    file.close();
    _contact_load_incomplete = true;
    return;
  }
#endif
  if (file) {
    bool full = false;
    uint16_t record_index = 0;
#if !defined(NRF52_PLATFORM)
    if (file.size() % mesh::storage::CONTACT_RECORD_SIZE != 0) {
#if MESH_CONTACT_CACHE
      _cache_load_incomplete = true;
#else
      _uncached_contact_load_incomplete = true;
#endif
      file.close();
      return;
    }
#endif
#if defined(NRF52_PLATFORM)
    _legacy_contact_count =
        mesh::storage::legacyContactCountForSize(legacy_size);
    bool legacy_read_failed = false;
    bool legacy_host_refused = false;
#endif
    while (!full
#if !defined(NRF52_PLATFORM)
           && record_index < file.size() / mesh::storage::CONTACT_RECORD_SIZE
#endif
#if defined(NRF52_PLATFORM)
           && record_index < _legacy_contact_count
#endif
    ) {
      uint8_t record[mesh::storage::CONTACT_RECORD_SIZE];
      if (file.read(record, sizeof(record)) != sizeof(record)) {
#if MESH_CONTACT_CACHE
        _cache_load_incomplete = true;
#elif !defined(NRF52_PLATFORM)
        _uncached_contact_load_incomplete = true;
#endif
#if defined(NRF52_PLATFORM)
        legacy_read_failed = true;
#endif
        break;
      }

      ContactInfo contact;
      bool path_unavailable = false;
      if (!deserializeContactRecord(record, contact, record_index, &path_unavailable)) {
        if (path_unavailable) {
#if MESH_CONTACT_CACHE
          _cache_load_incomplete = true;
#elif !defined(NRF52_PLATFORM)
          _uncached_contact_load_incomplete = true;
#endif
#if defined(NRF52_PLATFORM)
          legacy_read_failed = true;
#endif
          break;
        }
        // Preserve the contact while containing corrupt legacy routing data.
        contact.out_path_len = OUT_PATH_UNKNOWN;
      }
#if defined(NRF52_PLATFORM)
      const uint16_t slot = record_index;
      if (!_contact_slots.reserve(slot)) {
        legacy_read_failed = true;
        break;
      }
      contact.storage_slot = slot;
#endif
      ++record_index;
      if (!host->onContactLoaded(contact)) {
        full = true;
#if MESH_CONTACT_CACHE
        _cache_load_incomplete = true;
#elif !defined(NRF52_PLATFORM)
        _uncached_contact_load_incomplete = true;
#endif
#if defined(NRF52_PLATFORM)
        _contact_slots.release(slot);
        legacy_host_refused = true;
#endif
      }
    }
    file.close();

#if defined(NRF52_PLATFORM)
    if (legacy_read_failed || legacy_host_refused
        || record_index != _legacy_contact_count) {
      if (legacy_host_refused) {
        // Capacity refusal is semantically distinct from an I/O error, but the
        // durable source still contains contacts absent from RAM. Preserve it
        // and use the same mutation/migration veto until a clean load succeeds.
        MESH_DEBUG_PRINTLN(
            "DataStore: contact host capacity refused legacy record %u; load incomplete",
            (unsigned)record_index);
      } else {
        MESH_DEBUG_PRINTLN(
            "DataStore: short/incomplete legacy contact read at record %u of %u",
            (unsigned)record_index, (unsigned)_legacy_contact_count);
      }
      _contact_load_incomplete = true;
      return;
    }

    // Only modify stale page artifacts after the complete authoritative legacy
    // file has been loaded. A transient read failure above therefore leaves
    // every possible recovery source untouched.
    if (!mesh::storage::trustMigratedContactPages(
            true, _legacy_migration_ready)) {
      if (!prepareLegacyContactMigration()) {
        _contact_load_incomplete = true;
        return;
      }
      // prepareLegacyContactMigration() intentionally removed every stale page
      // observed in the first pass before committing the marker.
      contact_page_presence = 0;
    }
#endif
  }

#if defined(NRF52_PLATFORM)
  // Pages at and beyond the remaining legacy prefix have already committed.
  // Loading both sources this way resumes safely after every possible reset
  // point, including a reset after page rename but before legacy truncation.
  if (_legacy_migration_ready) {
    loadContactPages(host, _legacy_contact_count, contact_page_presence);
  }
#endif
}

bool DataStore::saveContacts(DataStoreHost* host, bool (*filter)(const ContactInfo& c)) {
  if (hasIncompleteContactLoad()) return false;
#if defined(NRF52_PLATFORM)
  bool success = true;
  for (uint32_t idx = 0;; idx++) {
    ContactInfo* contact = host->getContactForStore(idx);
    if (contact == NULL) break;
    if (filter && !filter(*contact)) continue;
    success = markContactDirty(*contact) && success;
  }
  return flushContactWrites(host, filter) && success;
#elif MESH_CONTACT_CACHE && defined(ESP32_PLATFORM)
  auto& paths = mesh::contactPathStorage();
  paths.beginCommit();
  for (uint32_t i = 0;; ++i) {
    auto* c = host->getContactForStore(i);
    if (!c) break;
    if (!filter || filter(*c)) paths.mark(c->path_ref.handle());
  }
  if (!paths.preserveSnapshots(0, 0x8000)) {
    paths.endCommit(false);
    return false;
  }
  // The cold-path reader reuses one SPIFFS File during this streaming write.
  // Close it before replacing the original name so later reads reopen the
  // committed file. No complete contact-table copy is needed in RAM.
  mesh::ContactFileTransaction writer(_getContactsChannelsFS(), "/contacts3");
  bool success = writer;
  uint8_t record[mesh::storage::CONTACT_RECORD_SIZE];
  for (uint32_t i = 0; success; ++i) {
    auto* c = host->getContactForStore(i);
    if (!c) break;
    if (filter && !filter(*c)) continue;
    success = serializeContactRecord(*c, record)
        && writer.write(record, sizeof(record)) == sizeof(record);
  }
  _contact_path_reader.close();
  success = writer.commit(success);
  if (success) {
    uint16_t index = 0;
    for (uint32_t i = 0;; ++i) {
      auto* c = host->getContactForStore(i);
      if (!c) break;
      if (filter && !filter(*c)) continue;
      paths.publish(c->path_ref.handle(), index++);
    }
  }
  paths.endCommit(success);
  return success;
#else
#if defined(STM32_PLATFORM)
  mesh::AtomicFileWriter file(_getContactsChannelsFS(), "/contacts3");
#else
  mesh::ContactFileTransaction file(_getContactsChannelsFS(), "/contacts3");
#endif
  bool success = (bool)file;
  if (file) {
    uint32_t idx = 0;
    ContactInfo c;
    uint8_t tx_radio = 0;

    while (host->getContactForSave(idx, c)) {
      if (filter && !filter(c)) {
        idx++;  // advance to next contact
        continue;
      }
      success = (file.write(c.id.pub_key, 32) == 32);
      success = success && (file.write((uint8_t *)&c.name, 32) == 32);
      success = success && (file.write(&c.type, 1) == 1);
      success = success && (file.write(&c.flags, 1) == 1);
      tx_radio = mesh::encodeRadioTxPolicy(c.tx_radio);
      success = success && (file.write(&tx_radio, 1) == 1);
      success = success && (file.write((uint8_t *)&c.sync_since, 4) == 4);
      success = success && (file.write((uint8_t *)&c.out_path_len, 1) == 1);
      success = success && (file.write((uint8_t *)&c.last_advert_timestamp, 4) == 4);
      uint8_t path[64];
      success = success && c.copyPathTo(path) && (file.write(path, 64) == 64);
      success = success && (file.write((uint8_t *)&c.lastmod, 4) == 4);
      success = success && (file.write((uint8_t *)&c.gps_lat, 4) == 4);
      success = success && (file.write((uint8_t *)&c.gps_lon, 4) == 4);

      if (!success) break; // write failed

      idx++;  // advance to next contact
    }
    success = file.commit(success);
  }
  return success;
#endif
}

bool DataStore::markContactDirty(const ContactInfo& contact) {
#if defined(NRF52_PLATFORM)
  if (hasIncompleteContactLoad()) return false;
  uint16_t slot = contact.storage_slot;
  if (!_contact_slots.isUsed(slot)) {
    slot = _contact_slots.allocate();
    if (slot == mesh::storage::CONTACT_SLOT_NONE) return false;
    contact.storage_slot = slot;
  }
  return _dirty_contact_pages.mark(slot / mesh::storage::CONTACTS_PER_PAGE);
#else
  (void)contact;
  return true;
#endif
}

bool DataStore::releaseContact(const ContactInfo& contact) {
#if defined(NRF52_PLATFORM)
  if (hasIncompleteContactLoad()) return false;
  const uint16_t slot = contact.storage_slot;
  if (!_contact_slots.release(slot)) return false;
  contact.storage_slot = mesh::storage::CONTACT_SLOT_NONE;
  return _dirty_contact_pages.mark(slot / mesh::storage::CONTACTS_PER_PAGE);
#else
  (void)contact;
  return true;
#endif
}

bool DataStore::restoreContactSlot(const ContactInfo& contact, uint16_t slot) {
#if defined(NRF52_PLATFORM)
  if (hasIncompleteContactLoad()) return false;
  // Roll back a release into the exact slot it vacated. Allocating the first
  // free slot here could move the record to another page; a reset between the
  // two resulting page writes would then leave duplicate records on disk.
  if (!_contact_slots.reserve(slot)) return false;
  if (!_dirty_contact_pages.mark(
          slot / mesh::storage::CONTACTS_PER_PAGE)) {
    _contact_slots.release(slot);
    return false;
  }
  contact.storage_slot = slot;
  return true;
#else
  (void)contact;
  (void)slot;
  return true;
#endif
}

#if defined(NRF52_PLATFORM)
bool DataStore::truncateLegacyContacts(uint16_t remaining_contacts) {
  FILESYSTEM* fs = _getContactsChannelsFS();
  if (remaining_contacts == 0) {
    bool legacy_exists = false;
    if (!contactPathPresence(fs, "/contacts3", legacy_exists)) return false;
    if (legacy_exists && !fs->remove("/contacts3")) return false;

    bool marker_exists = false;
    if (!contactPathPresence(fs, CONTACT_MIGRATION_MARKER, marker_exists)) {
      return false;
    }
    if (marker_exists && !fs->remove(CONTACT_MIGRATION_MARKER)) return false;

    _legacy_contact_count = 0;
    _legacy_contacts_pending_cleanup = false;
    _legacy_migration_ready = false;
    return true;
  }

  File file = fs->open("/contacts3", FILE_O_WRITE);
  if (!file) return false;

  const uint32_t expected_size =
      (uint32_t)remaining_contacts * mesh::storage::CONTACT_RECORD_SIZE;
  const bool truncated = file.truncate(expected_size);
  if (truncated) file.flush();
  file.close();
  if (!truncated) return false;

  // Verify the committed length before advancing the in-memory transaction.
  // If verification itself fails, retrying the same page/truncate is harmless.
  File verify = openRead(fs, "/contacts3");
  const bool valid = verify && verify.size() == expected_size;
  if (verify) verify.close();
  if (!valid) return false;

  _legacy_contact_count = remaining_contacts;
  return true;
}
#endif

bool DataStore::serviceContactWrites(DataStoreHost* host,
                                     bool (*filter)(const ContactInfo& c)) {
#if defined(NRF52_PLATFORM)
  if (hasIncompleteContactLoad()) {
    // Reboot is the recovery operation. Never migrate or rewrite from a RAM
    // table which is known not to represent every durable contact.
    return false;
  }
  if (_legacy_contacts_pending_cleanup) {
    FILESYSTEM* fs = _getContactsChannelsFS();
    bool legacy_exists = false;
    if (!contactPathPresence(fs, "/contacts3", legacy_exists)) return false;
    if (!legacy_exists) {
      bool marker_exists = false;
      if (!contactPathPresence(fs, CONTACT_MIGRATION_MARKER,
                               marker_exists)) {
        return false;
      }
      if (marker_exists && !fs->remove(CONTACT_MIGRATION_MARKER)) return false;
      _legacy_contacts_pending_cleanup = false;
      _legacy_migration_ready = false;
      _legacy_contact_count = 0;
    } else if (!_legacy_migration_ready
               && !prepareLegacyContactMigration()) {
      return false;
    } else if (_legacy_contact_count == 0) {
      return truncateLegacyContacts(0);
    } else {
      const uint8_t page =
          mesh::storage::legacyMigrationPage(_legacy_contact_count);
      if ((_unread_contact_pages.bits() & (1UL << page)) != 0) {
        // The legacy prefix cannot reconstruct post-prefix records from an
        // unread committed page. Keep both sources untouched for reboot retry.
        return false;
      }
      if (page >= mesh::storage::CONTACT_PAGE_COUNT
          || !writeContactPage(host, page, filter)) {
        return false;
      }

      const uint16_t remaining =
          mesh::storage::legacyCountAfterMigratingPage(page);
      if (!truncateLegacyContacts(remaining)) return false;
      _dirty_contact_pages.clear(page);
      return true;
    }
  }

  const int page = _dirty_contact_pages.first();
  if (page < 0) return true;
  if (!writeContactPage(host, (uint8_t)page, filter)) return false;
  _dirty_contact_pages.clear((uint8_t)page);
  return true;
#else
  return saveContacts(host, filter);
#endif
}

bool DataStore::flushContactWrites(DataStoreHost* host,
                                   bool (*filter)(const ContactInfo& c)) {
  if (hasIncompleteContactLoad()) return false;
#if defined(NRF52_PLATFORM)
  while (hasPendingContactWrites()) {
    if (!serviceContactWrites(host, filter)) return false;
  }
  return true;
#else
  return saveContacts(host, filter);
#endif
}

bool DataStore::hasPendingContactWrites() const {
#if defined(NRF52_PLATFORM)
  return !hasIncompleteContactLoad()
      && (!_dirty_contact_pages.empty() || _legacy_contacts_pending_cleanup);
#else
  return false;
#endif
}

bool DataStore::hasIncompleteContactLoad() const {
#if !defined(NRF52_PLATFORM)
  if (_channel_load_incomplete) return true;
#if !MESH_CONTACT_CACHE
  if (_uncached_contact_load_incomplete) return true;
#endif
#endif
#if MESH_CONTACT_CACHE
  if (_cache_load_incomplete) return true;
#endif
#if defined(NRF52_PLATFORM)
  return _contact_load_incomplete || !_unread_contact_pages.empty();
#else
  return false;
#endif
}

void DataStore::loadChannels(DataStoreHost* host) {
#if defined(ESP32_PLATFORM)
  _channel_load_incomplete = false;
  _channel_recovery_source = nullptr;
  FILESYSTEM* fs = _getContactsChannelsFS();
  for (const char* path : {"/channels2", "/channels2.bak"}) {
    for (unsigned attempt = 0; attempt < 2; ++attempt) {
      bool present = false;
      if (!companionPathPresence(fs, path, present)) continue;
      if (!present) break;
      File file = openRead(fs, path);
      if (!file) continue;
      static const uint32_t RECORD_SIZE = 4 + 32 + 32;
      const size_t size = file.size();
      if (size % RECORD_SIZE != 0 || size / RECORD_SIZE > MAX_GROUP_CHANNELS) {
        file.close();
        break;
      }
      const uint8_t count = size / RECORD_SIZE;
      ChannelDetails* loaded = count == 0 ? nullptr
          : static_cast<ChannelDetails*>(malloc(sizeof(ChannelDetails) * count));
      if (count != 0 && loaded == nullptr) {
        file.close();
        // Heap pressure is not evidence that the durable data is damaged.
        _channel_load_incomplete = true;
        return;
      }
      bool valid = true;
      for (uint8_t i = 0; valid && i < count; ++i) {
        uint8_t unused[4];
        valid = file.read(unused, sizeof(unused)) == sizeof(unused)
            && file.read(reinterpret_cast<uint8_t*>(loaded[i].name), 32) == 32
            && file.read(loaded[i].channel.secret, 32) == 32;
        if (valid) {
          loaded[i].name[31] = 0;
          loaded[i].channel.tx_radio = mesh::decodeRadioTxPolicy(unused[0]);
        }
      }
      file.close();
      if (!valid) { free(loaded); continue; }
      for (uint8_t i = 0; i < count; ++i) {
        if (!host->onChannelLoaded(i, loaded[i])) {
          free(loaded);
          _channel_load_incomplete = true;
          return;
        }
      }
      free(loaded);
      if (strcmp(path, "/channels2") != 0) {
        _channel_recovery_source = path;
        promoteCompanionRecoveryFile(fs, "/channels2", _channel_recovery_source);
      }
      return;
    }
  }
  MESH_DEBUG_PRINTLN("DataStore: no recoverable channels; defaults may replace the old image");
  return;
#else
#if defined(NRF52_PLATFORM)
  bool& incomplete = _contact_load_incomplete;
#else
  bool& incomplete = _channel_load_incomplete;
#endif
  if (incomplete) return;
  FILESYSTEM* contacts_fs = _getContactsChannelsFS();
#if defined(ESP32_PLATFORM) || defined(RP2040_PLATFORM)
  if (!mesh::ContactFileTransaction::recover(contacts_fs, "/channels2")) {
    MESH_DEBUG_PRINTLN("DataStore: channel transaction recovery failed; storage quarantined");
    incomplete = true;
    return;
  }
#endif
  uint32_t channels_size = 0;
#if defined(NRF52_PLATFORM)
  bool channels_exist = false;
  if (!contactPathPresence(contacts_fs, "/channels2", channels_exist,
                           &channels_size)) {
    incomplete = true;
    return;
  }
  if (!channels_exist) return;
#else
  if (!contacts_fs->exists("/channels2")) return;
#endif
  File file = openRead(contacts_fs, "/channels2");
  if (!file) {
    MESH_DEBUG_PRINTLN("DataStore: channels file open failed; storage quarantined");
    incomplete = true;
    return;
  }
#if defined(NRF52_PLATFORM)
  if (file.size() != channels_size) {
    file.close();
    MESH_DEBUG_PRINTLN("DataStore: channels file changed while opening; storage quarantined");
    incomplete = true;
    return;
  }
#else
  channels_size = file.size();
#endif
  static const uint32_t CHANNEL_RECORD_SIZE = 4 + 32 + 32;
  if ((channels_size % CHANNEL_RECORD_SIZE) != 0
      || channels_size / CHANNEL_RECORD_SIZE > MAX_GROUP_CHANNELS) {
    file.close();
    MESH_DEBUG_PRINTLN("DataStore: invalid channels file size; storage quarantined");
    incomplete = true;
    return;
  }
  const uint8_t channel_count =
      static_cast<uint8_t>(channels_size / CHANNEL_RECORD_SIZE);
  ChannelDetails* loaded = channel_count == 0 ? nullptr
      : static_cast<ChannelDetails*>(
          malloc(sizeof(ChannelDetails) * channel_count));
  if (channel_count != 0 && loaded == nullptr) {
    file.close();
    MESH_DEBUG_PRINTLN(
        "DataStore: no memory for channels snapshot; storage quarantined");
    incomplete = true;
    return;
  }
  bool success = true;
  for (uint8_t channel_idx = 0; channel_idx < channel_count; channel_idx++) {
    uint8_t unused[4];
    success = file.read(unused, sizeof(unused)) == sizeof(unused)
        && file.read(reinterpret_cast<uint8_t*>(loaded[channel_idx].name),
                     sizeof(loaded[channel_idx].name))
            == sizeof(loaded[channel_idx].name)
        && file.read(loaded[channel_idx].channel.secret,
                     sizeof(loaded[channel_idx].channel.secret))
            == sizeof(loaded[channel_idx].channel.secret);
    if (!success) break;
    loaded[channel_idx].name[sizeof(loaded[channel_idx].name) - 1] = 0;
    loaded[channel_idx].channel.tx_radio = mesh::decodeRadioTxPolicy(unused[0]);
  }
  file.close();
  if (!success) {
    free(loaded);
    MESH_DEBUG_PRINTLN(
        "DataStore: channels file read failed; storage quarantined");
    incomplete = true;
    return;
  }
  for (uint8_t channel_idx = 0; channel_idx < channel_count; channel_idx++) {
    if (!host->onChannelLoaded(channel_idx, loaded[channel_idx])) {
      free(loaded);
      MESH_DEBUG_PRINTLN(
          "DataStore: channel host refused durable record; storage quarantined");
      incomplete = true;
      return;
    }
  }
  free(loaded);
#endif
}

bool DataStore::saveChannels(DataStoreHost* host) {
#if defined(ESP32_PLATFORM)
  if (!promoteCompanionRecoveryFile(_getContactsChannelsFS(), "/channels2",
                                    _channel_recovery_source)) return false;
#endif
#if !defined(NRF52_PLATFORM)
  if (_channel_load_incomplete) return false;
#endif
#if defined(NRF52_PLATFORM)
  if (_contact_load_incomplete) return false;
#endif
#if defined(NRF52_PLATFORM) || defined(STM32_PLATFORM)
  mesh::AtomicFileWriter file(_getContactsChannelsFS(), "/channels2");
#elif defined(ESP32_PLATFORM)
  mesh::ContactFileTransaction file(_getContactsChannelsFS(), "/channels2", companionPathPresence);
#elif defined(RP2040_PLATFORM)
  mesh::ContactFileTransaction file(_getContactsChannelsFS(), "/channels2");
#else
  File file = openWrite(_getContactsChannelsFS(), "/channels2");
#endif
  bool success = (bool)file;
  if (file) {
    uint8_t channel_idx = 0;
    ChannelDetails ch;
    uint8_t unused[4];
    memset(unused, 0, 4);

    while (success && host->getChannelForSave(channel_idx, ch)) {
      unused[0] = mesh::encodeRadioTxPolicy(ch.channel.tx_radio);
      success = (file.write(unused, 4) == 4);
      success = success && (file.write((uint8_t *)ch.name, 32) == 32);
      success = success && (file.write((uint8_t *)ch.channel.secret, 32) == 32);

      if (!success) break; // write failed
      channel_idx++;
    }
#if defined(NRF52_PLATFORM) || defined(STM32_PLATFORM) || defined(ESP32_PLATFORM) || defined(RP2040_PLATFORM)
    success = file.commit(success);
    if (!success) MESH_DEBUG_PRINTLN("DataStore: atomic channels write failed");
#else
    file.close();
#endif
  }
  return success;
}

#if defined(NRF52_PLATFORM) || defined(STM32_PLATFORM)

#define MAX_ADVERT_PKT_LEN   (2 + 32 + PUB_KEY_SIZE + 4 + SIGNATURE_SIZE + MAX_ADVERT_DATA_SIZE)

struct BlobRec {
  uint32_t timestamp;
  uint8_t  key[7];
  uint8_t  len;
  uint8_t  data[MAX_ADVERT_PKT_LEN];
};

#if !defined(NRF52_PLATFORM)
static void normalizeBlobKey(const uint8_t key[], int key_len, uint8_t normalized[7]) {
  memset(normalized, 0, 7);
  if (key == NULL || key_len <= 0) return;
  if (key_len > 7) key_len = 7;
  memcpy(normalized, key, key_len);
}
#endif

void DataStore::checkAdvBlobFile() {
#if defined(NRF52_PLATFORM)
  // Advert packets are a disposable cache and are learned again over the air.
  // Retaining the old 18 KiB monolithic cache alongside atomic buckets can
  // exhaust the 100 KiB ExtraFS and prevent a contact-page commit, so retire
  // it once on upgrade. Contact records and identity data are not affected.
  if (_fs->exists("/adv_blobs") && !_fs->remove("/adv_blobs")) {
    MESH_DEBUG_PRINTLN("DataStore: could not retire internal legacy advert cache");
  }
  if (_fsExtra != nullptr && _fsExtra->exists("/adv_blobs")
      && !_fsExtra->remove("/adv_blobs")) {
    MESH_DEBUG_PRINTLN("DataStore: could not retire secondary legacy advert cache");
  }
  return;
#else
  if (!_getContactsChannelsFS()->exists("/adv_blobs")) {
    File file = openWrite(_getContactsChannelsFS(), "/adv_blobs");
    if (file) {
      BlobRec zeroes;
      memset(&zeroes, 0, sizeof(zeroes));
      for (int i = 0; i < MAX_BLOBRECS; i++) {     // pre-allocate to fixed size
        file.write((uint8_t *) &zeroes, sizeof(zeroes));
      }
      file.close();
    }
  }
#endif
}

bool DataStore::migrateToSecondaryFS() {
  if (_fsExtra == nullptr) return false;

  // Implemented below through verified copy transactions. On nRF52, all
  // primary contact/channel files are copied before an atomic journal commit;
  // no source is removed before that commit makes the complete secondary
  // snapshot authoritative across a reset.
#if defined(NRF52_PLATFORM)
  // /adv_blobs is a reconstructable cache retired by checkAdvBlobFile(); do
  // not spend time and temporary space atomically copying it first.
  static const char* to_secondary[] = {
      "/contacts3", "/contacts4.mig", "/channels2"};
#else
  static const char* to_secondary[] = {"/adv_blobs", "/contacts3", "/channels2"};
#endif
  static const char* to_primary[] = {"/_main.id", "/new_prefs"};

  auto filesEqual = [this](FILESYSTEM* left_fs, FILESYSTEM* right_fs,
                           const char* path) -> bool {
    File left = openRead(left_fs, path);
    File right = openRead(right_fs, path);
    const uint32_t expected_size = left ? left.size() : 0;
    if (!left || !right || expected_size != right.size()) {
      if (left) left.close();
      if (right) right.close();
      return false;
    }
    uint8_t left_buf[64], right_buf[64];
    bool equal = true;
    uint32_t compared = 0;
    while (equal) {
      int left_count = left.read(left_buf, sizeof(left_buf));
      int right_count = right.read(right_buf, sizeof(right_buf));
      if (left_count < 0 || right_count < 0) {
        equal = false;
      } else if (left_count != right_count) {
        equal = false;
      } else if (left_count <= 0) {
        break;
      } else if (memcmp(left_buf, right_buf, left_count) != 0) {
        equal = false;
      } else {
        compared += (uint32_t)left_count;
      }
    }
    left.close();
    right.close();
    return equal && compared == expected_size;
  };

  auto copy = [this, &filesEqual](FILESYSTEM* source_fs, FILESYSTEM* dest_fs,
                                  const char* path,
                                  bool exact_snapshot) -> bool {
#if defined(NRF52_PLATFORM)
    bool source_exists = false;
    if (!contactPathPresence(source_fs, path, source_exists)) return false;
    if (!source_exists) {
      if (!exact_snapshot) return true;

      // A reset during a previous Pending attempt can leave a destination-only
      // file. Primary is still authoritative until COMMITTED, so the retry
      // must remove that stale file rather than blessing it into the snapshot.
      bool destination_exists = false;
      if (!contactPathPresence(dest_fs, path, destination_exists)) return false;
      if (!destination_exists) return true;
      if (!dest_fs->remove(path)) return false;
      bool destination_remains = true;
      return contactPathPresence(dest_fs, path, destination_remains)
          && !destination_remains;
    }

    bool destination_exists = false;
    if (!contactPathPresence(dest_fs, path, destination_exists)) return false;
#else
    if (!source_fs->exists(path)) return true;
    const bool destination_exists = dest_fs->exists(path);
#endif
    if (destination_exists) {
      if (filesEqual(source_fs, dest_fs, path)) {
        return true;
      }
      if (!exact_snapshot) {
        MESH_DEBUG_PRINTLN("DataStore: migration conflict for %s; preserving both copies", path);
        return false;
      }
      // Pending migration destinations are non-authoritative. Fall through to
      // AtomicFileWriter so this stale copy is replaced as one transaction.
    }

    File source = openRead(source_fs, path);
    if (!source) return false;
    const uint32_t expected_size = source.size();
    bool success = true;
    uint8_t buf[64];

#if defined(NRF52_PLATFORM)
    mesh::AtomicFileWriter destination(dest_fs, path);
    success = (bool)destination;
    while (success) {
      int count = source.read(buf, sizeof(buf));
      if (count < 0) {
        success = false;
      } else if (count == 0) {
        break;
      } else {
        success = destination.write(buf, count) == (size_t)count;
      }
    }
    source.close();
    success = destination.commit(success && destination.bytesWritten() == expected_size);
#else
    char temp_path[64];
    snprintf(temp_path, sizeof(temp_path), "%s.tmp", path);
    File destination = openWrite(dest_fs, temp_path);
    success = (bool)destination;
    uint32_t written = 0;
    while (success) {
      int count = source.read(buf, sizeof(buf));
      if (count < 0) {
        success = false;
      } else if (count == 0) {
        break;
      } else {
        success = destination.write(buf, count) == (size_t)count;
        written += success ? count : 0;
      }
    }
    source.close();
    if (destination) destination.close();
    success = success && written == expected_size;
    if (success) {
      File verify = openRead(dest_fs, temp_path);
      success = verify && verify.size() == expected_size;
      if (verify) verify.close();
    }
    if (success) success = dest_fs->rename(temp_path, path);
    if (!success) dest_fs->remove(temp_path);
#endif

    if (!success || !filesEqual(source_fs, dest_fs, path)) {
      MESH_DEBUG_PRINTLN("DataStore: verified migration failed for %s", path);
      return false;
    }
    return true;
  };

#if !defined(NRF52_PLATFORM)
  auto move = [&copy](FILESYSTEM* source_fs, FILESYSTEM* dest_fs,
                      const char* path) -> bool {
    if (!source_fs->exists(path)) return true;
    return copy(source_fs, dest_fs, path, false) && source_fs->remove(path);
  };
#endif

  auto migratePrimarySources = [this, &copy, &filesEqual]() -> bool {
    bool success = true;
    for (size_t i = 0; i < sizeof(to_primary) / sizeof(to_primary[0]); i++) {
      const char* path = to_primary[i];
#if defined(NRF52_PLATFORM)
      const bool is_identity = strcmp(path, "/_main.id") == 0;
      const bool is_preferences = strcmp(path, "/new_prefs") == 0;
#endif
      bool primary_exists = false;
#if defined(NRF52_PLATFORM)
      if (!contactPathPresence(_fs, path, primary_exists)) {
        if (is_identity) _identity_creation_blocked = true;
        if (is_preferences) _prefs_load_incomplete = true;
        return false;
      }

      bool secondary_exists = false;
      if (!contactPathPresence(_fsExtra, path, secondary_exists)) {
        // Identity and preferences on primary are canonical. If that copy is
        // already present, a secondary metadata error only prevents optional
        // duplicate cleanup and is irrelevant to their availability.
        if (primary_exists) continue;
        if (is_identity) _identity_creation_blocked = true;
        if (is_preferences) _prefs_load_incomplete = true;
        return false;
      }
#else
      primary_exists = _fs->exists(path);
      const bool secondary_exists = _fsExtra->exists(path);
#endif
      if (!secondary_exists) continue;

      if (primary_exists) {
        if (!filesEqual(_fsExtra, _fs, path)) {
          // Identity/preferences are canonical on primary. A differing legacy
          // secondary copy is useful forensic data, but it must not prevent
          // contact/channel migration or make secondary storage disappear.
          MESH_DEBUG_PRINTLN("DataStore: preserving conflicting legacy secondary %s; primary remains authoritative", path);
          continue;
        }
      } else {
        // The required source can disappear between its discovery probe and
        // copy()'s own stat. Only a verified destination proves recovery.
        bool recovered_primary = copy(_fsExtra, _fs, path, false);
#if defined(NRF52_PLATFORM)
        bool recovered_primary_exists = false;
        recovered_primary = recovered_primary
            && contactPathPresence(_fs, path, recovered_primary_exists)
            && recovered_primary_exists;
#else
        recovered_primary = recovered_primary && _fs->exists(path);
#endif
        if (!recovered_primary) {
          // Without any primary copy MyMesh would generate a replacement
          // identity or persist default preferences, so this is the only
          // primary-source condition which must block the storage handoff.
#if defined(NRF52_PLATFORM)
          if (is_identity) _identity_creation_blocked = true;
          if (is_preferences) _prefs_load_incomplete = true;
#endif
          success = false;
          continue;
        }
      }

      // At this point primary has an exact verified copy. Failure to retire a
      // duplicate is harmless and can be retried on a later boot.
      if (!_fsExtra->remove(path)) {
        MESH_DEBUG_PRINTLN("DataStore: could not retire verified legacy secondary %s", path);
      }
    }
    return success;
  };

#if defined(NRF52_PLATFORM)
  enum class MigrationJournalState : uint8_t {
    None,
    Pending,
    Committed,
    Invalid,
    IoError,
  };
  static const uint8_t journal_magic[] = {'M', 'C', 'X', 'F', 1};

  auto readJournal = [this]() -> MigrationJournalState {
    bool journal_exists = false;
    if (!contactPathPresence(_fsExtra, SECONDARY_MIGRATION_JOURNAL,
                             journal_exists)) {
      return MigrationJournalState::IoError;
    }
    if (!journal_exists) {
      return MigrationJournalState::None;
    }
    File journal = openRead(_fsExtra, SECONDARY_MIGRATION_JOURNAL);
    uint8_t payload[sizeof(journal_magic) + 1];
    const bool valid_size = journal && journal.size() == sizeof(payload);
    const int count = valid_size ? journal.read(payload, sizeof(payload)) : 0;
    if (journal) journal.close();
    if (count != (int)sizeof(payload)
        || memcmp(payload, journal_magic, sizeof(journal_magic)) != 0) {
      return MigrationJournalState::Invalid;
    }
    if (payload[sizeof(journal_magic)] == SECONDARY_MIGRATION_PENDING) {
      return MigrationJournalState::Pending;
    }
    if (payload[sizeof(journal_magic)] == SECONDARY_MIGRATION_COMMITTED) {
      return MigrationJournalState::Committed;
    }
    return MigrationJournalState::Invalid;
  };

  auto writeJournal = [this](uint8_t state) -> bool {
    uint8_t payload[sizeof(journal_magic) + 1];
    memcpy(payload, journal_magic, sizeof(journal_magic));
    payload[sizeof(journal_magic)] = state;
    mesh::AtomicFileWriter journal(_fsExtra, SECONDARY_MIGRATION_JOURNAL);
    return journal
        && journal.write(payload, sizeof(payload)) == sizeof(payload)
        && journal.commit();
  };

  auto hasSecondarySources = [this](bool& present) -> bool {
    present = false;
    for (size_t i = 0; i < sizeof(to_secondary) / sizeof(to_secondary[0]); i++) {
      bool path_exists = false;
      if (!contactPathPresence(_fs, to_secondary[i], path_exists)) return false;
      if (path_exists) {
        present = true;
        return true;
      }
    }
    for (uint8_t page = 0; page < mesh::storage::CONTACT_PAGE_COUNT; page++) {
      char path[24];
      makeContactPagePath(page, path);
      bool path_exists = false;
      if (!contactPathPresence(_fs, path, path_exists)) return false;
      if (path_exists) {
        present = true;
        return true;
      }
    }
    for (uint8_t bucket = 0; bucket < 10; bucket++) {
      char path[20];
      snprintf(path, sizeof(path), "/adv4_%02u", (unsigned)bucket);
      bool path_exists = false;
      if (!contactPathPresence(_fs, path, path_exists)) return false;
      if (path_exists) {
        present = true;
        return true;
      }
    }
    return true;
  };

  auto copySecondarySources = [this, &copy]() -> bool {
    bool success = true;
    for (size_t i = 0; i < sizeof(to_secondary) / sizeof(to_secondary[0]); i++) {
      if (!copy(_fs, _fsExtra, to_secondary[i], true)) success = false;
    }
    for (uint8_t page = 0; page < mesh::storage::CONTACT_PAGE_COUNT; page++) {
      char path[24];
      makeContactPagePath(page, path);
      if (!copy(_fs, _fsExtra, path, true)) success = false;
    }
    for (uint8_t bucket = 0; bucket < 10; bucket++) {
      char path[20];
      snprintf(path, sizeof(path), "/adv4_%02u", (unsigned)bucket);
      if (!copy(_fs, _fsExtra, path, true)) success = false;
    }
    return success;
  };

  auto retireSecondarySources = [this]() -> bool {
    bool fixed_present[sizeof(to_secondary) / sizeof(to_secondary[0])] = {};
    uint32_t page_presence = 0;
    uint16_t bucket_presence = 0;

    // Capture the complete removal set before deleting anything. A metadata
    // I/O error must leave every committed primary source in place for the
    // next reboot, rather than being mistaken for an absent file midway
    // through cleanup.
    for (size_t i = 0; i < sizeof(to_secondary) / sizeof(to_secondary[0]); i++) {
      if (!contactPathPresence(_fs, to_secondary[i], fixed_present[i])) {
        return false;
      }
    }
    for (uint8_t page = 0; page < mesh::storage::CONTACT_PAGE_COUNT; page++) {
      char path[24];
      makeContactPagePath(page, path);
      bool path_exists = false;
      if (!contactPathPresence(_fs, path, path_exists)) return false;
      if (path_exists) page_presence |= 1UL << page;
    }
    for (uint8_t bucket = 0; bucket < 10; bucket++) {
      char path[20];
      snprintf(path, sizeof(path), "/adv4_%02u", (unsigned)bucket);
      bool path_exists = false;
      if (!contactPathPresence(_fs, path, path_exists)) return false;
      if (path_exists) bucket_presence |= (uint16_t)1U << bucket;
    }

    bool success = true;
    auto retire = [this, &success](const char* path, bool path_exists) {
      if (!path_exists) return;
      // The committed journal is the authority switch: every destination was
      // already verified before it was written. Do not compare again here,
      // because the secondary may legitimately advance while cleanup retries.
      if (!_fs->remove(path)) success = false;
    };
    for (size_t i = 0; i < sizeof(to_secondary) / sizeof(to_secondary[0]); i++) {
      retire(to_secondary[i], fixed_present[i]);
    }
    for (uint8_t page = 0; page < mesh::storage::CONTACT_PAGE_COUNT; page++) {
      char path[24];
      makeContactPagePath(page, path);
      retire(path, (page_presence & (1UL << page)) != 0);
    }
    for (uint8_t bucket = 0; bucket < 10; bucket++) {
      char path[20];
      snprintf(path, sizeof(path), "/adv4_%02u", (unsigned)bucket);
      retire(path, (bucket_presence & ((uint16_t)1U << bucket)) != 0);
    }
    return success;
  };

  MigrationJournalState journal_state = readJournal();
  // Identity and preferences are always read from the primary filesystem.
  // Recover legacy/test-layout copies before any journal result can select or
  // quarantine the contact/channel store. The journal has already been read,
  // however, so a failure here can never silently select the wrong authority.
  if (!migratePrimarySources()) {
    _contact_load_incomplete = true;
    MESH_DEBUG_PRINTLN(
        "DataStore: primary identity/preferences migration failed; storage quarantined until reboot");
    return false;
  }

  if (journal_state == MigrationJournalState::IoError) {
    // The journal is the authority switch. If its presence cannot be
    // classified, neither filesystem can safely be selected for contact or
    // channel mutations this boot: Pending means primary is authoritative,
    // while Committed means secondary may already be the only complete copy.
    _contact_load_incomplete = true;
    MESH_DEBUG_PRINTLN(
        "DataStore: ExtraFS migration journal stat failed; storage quarantined until reboot");
    return false;
  }
  if (journal_state == MigrationJournalState::Invalid) {
    // Atomic journal contents should always be one of the two valid states.
    // Corruption cannot reveal whether source retirement had begun, so it has
    // the same authority-unknown policy as a journal I/O error.
    _contact_load_incomplete = true;
    MESH_DEBUG_PRINTLN(
        "DataStore: invalid ExtraFS migration journal; storage quarantined until explicit erase");
    return false;
  }

  bool secondary_sources_present = false;
  if (journal_state == MigrationJournalState::None
      && !hasSecondarySources(secondary_sources_present)) {
    // A clean post-migration boot also has no journal and no primary sources,
    // so a source-stat error makes authority unknowable. Preserve both stores
    // and block contact/channel use until a reboot can classify every path.
    _contact_load_incomplete = true;
    MESH_DEBUG_PRINTLN(
        "DataStore: could not inspect primary migration sources; storage quarantined until reboot");
    return false;
  }
  if (journal_state != MigrationJournalState::Committed
      && (journal_state != MigrationJournalState::None
          || secondary_sources_present)) {
    if (journal_state == MigrationJournalState::None
        && !writeJournal(SECONDARY_MIGRATION_PENDING)) {
      MESH_DEBUG_PRINTLN("DataStore: could not start ExtraFS migration transaction");
      _fsExtra = nullptr;
      return false;
    }
    if (!copySecondarySources()
        || !writeJournal(SECONDARY_MIGRATION_COMMITTED)) {
      // Pending means no source has been retired, so primary remains a complete
      // fallback even if the destination contains harmless partial copies.
      MESH_DEBUG_PRINTLN("DataStore: ExtraFS migration copy incomplete; using primary storage");
      _fsExtra = nullptr;
      return false;
    }
    journal_state = MigrationJournalState::Committed;
  }

  if (journal_state == MigrationJournalState::Committed) {
    if (!retireSecondarySources()) {
      // The committed secondary contains the full verified snapshot. Keep it
      // active and keep the journal so cleanup can resume without copying a
      // stale primary source over newer secondary data.
      MESH_DEBUG_PRINTLN("DataStore: ExtraFS migration committed; source cleanup remains pending");
      return false;
    }
    // readJournal() found it, or writeJournal() committed it in this call, so
    // a second presence probe cannot add safety. Remove the known journal
    // directly after all committed sources have been retired.
    if (!_fsExtra->remove(SECONDARY_MIGRATION_JOURNAL)) {
      MESH_DEBUG_PRINTLN("DataStore: ExtraFS migration journal cleanup failed");
      return false;
    }
  }
#else
  bool success = true;
  for (size_t i = 0; i < sizeof(to_secondary) / sizeof(to_secondary[0]); i++) {
    if (!move(_fs, _fsExtra, to_secondary[i])) success = false;
  }
  if (!success) return false;
  return migratePrimarySources();
#endif

  return true;
}

#if defined(NRF52_PLATFORM)
// Keep every bucket below one 4 KiB LittleFS block. Five 20-record buckets use
// about the same flash as the old 100-record file; smaller buckets would each
// consume a full block and leave no room for contact-page transactions.
static const uint8_t BLOB_BUCKET_COUNT = MAX_BLOBRECS > 20 ? 5 : 1;
static const uint8_t BLOB_BUCKET_SLOTS =
    (MAX_BLOBRECS + BLOB_BUCKET_COUNT - 1) / BLOB_BUCKET_COUNT;
static const uint8_t BLOB_BUCKET_HEADER_SIZE = 16;
static const uint8_t BLOB_BUCKET_MAGIC[4] = {'M', 'C', 'B', '4'};
static_assert(BLOB_BUCKET_HEADER_SIZE + sizeof(BlobRec) * BLOB_BUCKET_SLOTS < 4096,
              "advert bucket must fit in one LittleFS block");

static void normalizeBlobKey(const uint8_t key[], int key_len, uint8_t normalized[7]) {
  memset(normalized, 0, 7);
  if (key == NULL || key_len <= 0) return;
  if (key_len > 7) key_len = 7;
  memcpy(normalized, key, key_len);
}

static uint8_t blobBucketFor(const uint8_t key[7]) {
  uint32_t hash = 2166136261UL;
  for (uint8_t i = 0; i < 7; i++) {
    hash ^= key[i];
    hash *= 16777619UL;
  }
  return hash % BLOB_BUCKET_COUNT;
}

static void makeBlobBucketPath(uint8_t bucket, char path[20]) {
  snprintf(path, 20, "/adv4_%02u", (unsigned)bucket);
}

static bool loadBlobBucket(FILESYSTEM* fs, uint8_t bucket,
                           BlobRec records[BLOB_BUCKET_SLOTS]) {
  memset(records, 0, sizeof(BlobRec) * BLOB_BUCKET_SLOTS);
  char path[20];
  makeBlobBucketPath(bucket, path);
  if (!fs->exists(path)) return true;

  File file = fs->open(path, FILE_O_READ);
  if (!file) return false;
  const size_t payload_size = sizeof(BlobRec) * BLOB_BUCKET_SLOTS;
  if (file.size() != BLOB_BUCKET_HEADER_SIZE + payload_size) {
    file.close();
    return false;
  }

  uint8_t header[BLOB_BUCKET_HEADER_SIZE];
  bool valid = file.read(header, sizeof(header)) == sizeof(header)
      && memcmp(header, BLOB_BUCKET_MAGIC, sizeof(BLOB_BUCKET_MAGIC)) == 0
      && header[4] == 1 && header[5] == bucket
      && header[6] == BLOB_BUCKET_SLOTS
      && mesh::storage::readLE16(&header[8]) == sizeof(BlobRec)
      && file.read((uint8_t*)records, payload_size) == (int)payload_size;
  file.close();
  if (!valid) return false;

  const uint32_t expected_crc = mesh::storage::readLE32(&header[12]);
  const uint32_t actual_crc = mesh::storage::updateCRC32(
      0xFFFFFFFFUL, (const uint8_t*)records, payload_size);
  if (actual_crc != expected_crc) return false;
  for (uint8_t i = 0; i < BLOB_BUCKET_SLOTS; i++) {
    if (records[i].len > MAX_ADVERT_PKT_LEN) return false;
  }
  return true;
}

static bool saveBlobBucket(FILESYSTEM* fs, uint8_t bucket,
                           const BlobRec records[BLOB_BUCKET_SLOTS]) {
  const size_t payload_size = sizeof(BlobRec) * BLOB_BUCKET_SLOTS;
  uint8_t header[BLOB_BUCKET_HEADER_SIZE];
  memset(header, 0, sizeof(header));
  memcpy(header, BLOB_BUCKET_MAGIC, sizeof(BLOB_BUCKET_MAGIC));
  header[4] = 1;
  header[5] = bucket;
  header[6] = BLOB_BUCKET_SLOTS;
  mesh::storage::writeLE16(&header[8], sizeof(BlobRec));
  mesh::storage::writeLE32(&header[12], mesh::storage::updateCRC32(
      0xFFFFFFFFUL, (const uint8_t*)records, payload_size));

  char path[20];
  makeBlobBucketPath(bucket, path);
  mesh::AtomicFileWriter writer(fs, path);
  const bool wrote = writer
      && writer.write(header, sizeof(header)) == sizeof(header)
      && writer.write((const uint8_t*)records, payload_size) == payload_size;
  return writer.commit(wrote);
}

static bool findBlobInBucket(FILESYSTEM* fs, const uint8_t key[7],
                             uint8_t dest_buf[], uint8_t& length) {
  // Twenty records are roughly 3.6 KiB, too large for the nRF Arduino loop's
  // 4 KiB stack once callers are included. Use a short-lived heap buffer.
  BlobRec* records = (BlobRec*)malloc(sizeof(BlobRec) * BLOB_BUCKET_SLOTS);
  if (records == nullptr) return false;
  const uint8_t bucket = blobBucketFor(key);
  if (!loadBlobBucket(fs, bucket, records)) {
    free(records);
    return false;
  }
  for (uint8_t i = 0; i < BLOB_BUCKET_SLOTS; i++) {
    if (memcmp(records[i].key, key, sizeof(records[i].key)) == 0
        && (records[i].timestamp != 0 || records[i].len != 0)) {
      length = records[i].len; // zero is an intentional tombstone
      if (length > 0) memcpy(dest_buf, records[i].data, length);
      free(records);
      return true;
    }
  }
  free(records);
  return false;
}

// Once a v4 bucket contains a key, the old monolithic cache entry is no
// longer needed.  Clear just that disposable legacy record so an eventually
// evicted bucket/tombstone can never expose stale advert data again.  This is
// intentionally a bounded in-place write: atomically rewriting the complete
// legacy cache is the multi-second operation the bucket format avoids.
static bool clearLegacyBlobRecord(FILESYSTEM* fs, const uint8_t key[7]) {
  if (!fs->exists("/adv_blobs")) return true;

  File file = fs->open("/adv_blobs", FILE_O_WRITE);
  if (!file) return false;

  BlobRec record;
  uint32_t position = 0;
  bool success = true;
  bool found = false;
  file.seek(0);
  while (file.read((uint8_t*)&record, sizeof(record)) == sizeof(record)) {
    if (record.len <= MAX_ADVERT_PKT_LEN
        && memcmp(record.key, key, sizeof(record.key)) == 0
        && (record.timestamp != 0 || record.len != 0)) {
      found = true;
      memset(&record, 0, sizeof(record));
      success = file.seek(position)
          && file.write((uint8_t*)&record, sizeof(record)) == sizeof(record);
      if (success) file.flush();
      break;
    }
    position += sizeof(record);
  }
  file.close();
  return !found || success;
}
#endif

uint8_t DataStore::getBlobByKey(const uint8_t key[], int key_len, uint8_t dest_buf[]) {
#if defined(NRF52_PLATFORM)
  uint8_t normalized[7], length = 0;
  normalizeBlobKey(key, key_len, normalized);
  if (findBlobInBucket(_getContactsChannelsFS(), normalized, dest_buf, length)) {
    return length;
  }
  // checkAdvBlobFile() retires the old monolithic nRF cache at boot. If that
  // best-effort removal failed, do not fall back to it: a durable bucket
  // tombstone must remain authoritative even when its bucket is temporarily
  // unreadable, and adverts are a reconstructable cache.
  return 0;
#endif

#if !defined(NRF52_PLATFORM)
  File file = openRead(_getContactsChannelsFS(), "/adv_blobs");
  uint8_t len = 0;  // 0 = not found
  if (file) {
    BlobRec tmp;
    while (file.read((uint8_t *) &tmp, sizeof(tmp)) == sizeof(tmp)) {
      uint8_t normalized[7];
      normalizeBlobKey(key, key_len, normalized);
      if (tmp.len <= MAX_ADVERT_PKT_LEN
          && memcmp(normalized, tmp.key, sizeof(tmp.key)) == 0) {  // only match by 7 byte prefix
        len = tmp.len;
        memcpy(dest_buf, tmp.data, len);
        break;
      }
    }
    file.close();
  }
  return len;
#endif
}

bool DataStore::putBlobByKey(const uint8_t key[], int key_len, const uint8_t src_buf[], uint8_t len) {
  if (len < PUB_KEY_SIZE+4+SIGNATURE_SIZE || len > MAX_ADVERT_PKT_LEN) return false;
#if defined(NRF52_PLATFORM)
  uint8_t normalized[7];
  normalizeBlobKey(key, key_len, normalized);
  const uint8_t bucket = blobBucketFor(normalized);
  BlobRec* records = (BlobRec*)malloc(sizeof(BlobRec) * BLOB_BUCKET_SLOTS);
  if (records == nullptr) return false;
  if (!loadBlobBucket(_getContactsChannelsFS(), bucket, records)) {
    MESH_DEBUG_PRINTLN("DataStore: advert bucket %u corrupt; replacing on next write", bucket);
    memset(records, 0, sizeof(BlobRec) * BLOB_BUCKET_SLOTS);
  }

  uint8_t selected = 0;
  uint32_t oldest = 0xFFFFFFFFUL;
  for (uint8_t i = 0; i < BLOB_BUCKET_SLOTS; i++) {
    if (memcmp(records[i].key, normalized, sizeof(records[i].key)) == 0
        && (records[i].timestamp != 0 || records[i].len != 0)) {
      selected = i;
      break;
    }
    if (records[i].timestamp < oldest) {
      oldest = records[i].timestamp;
      selected = i;
    }
  }
  BlobRec& record = records[selected];
  memset(&record, 0, sizeof(record));
  memcpy(record.key, normalized, sizeof(record.key));
  memcpy(record.data, src_buf, len);
  record.len = len;
  record.timestamp = _clock->getCurrentTime();
  if (record.timestamp == 0) record.timestamp = 1;
  const bool saved = saveBlobBucket(_getContactsChannelsFS(), bucket, records);
  free(records);
  if (!saved) return false;
  if (!clearLegacyBlobRecord(_getContactsChannelsFS(), normalized)) {
    // The nRF reader never falls back to this reconstructable legacy cache.
    MESH_DEBUG_PRINTLN(
        "DataStore: advert saved; legacy cache cleanup deferred");
  }
  return true;
#else
  checkAdvBlobFile();
  File file = _getContactsChannelsFS()->open("/adv_blobs", FILE_O_WRITE);
  if (file) {
    uint32_t pos = 0, found_pos = 0;
    uint32_t min_timestamp = 0xFFFFFFFF;

    // search for matching key OR evict by oldest timestamp
    BlobRec tmp;
    file.seek(0);
    while (file.read((uint8_t *) &tmp, sizeof(tmp)) == sizeof(tmp)) {
      if (memcmp(key, tmp.key, sizeof(tmp.key)) == 0) {  // only match by 7 byte prefix
        found_pos = pos;
        break;
      }
      if (tmp.timestamp < min_timestamp) {
        min_timestamp = tmp.timestamp;
        found_pos = pos;
      }

      pos += sizeof(tmp);
    }

    memcpy(tmp.key, key, sizeof(tmp.key));  // just record 7 byte prefix of key
    memcpy(tmp.data, src_buf, len);
    tmp.len = len;
    tmp.timestamp = _clock->getCurrentTime();

    file.seek(found_pos);
    file.write((uint8_t *) &tmp, sizeof(tmp));

    file.close();
    return true;
  }
  return false; // error
#endif
}
bool DataStore::deleteBlobByKey(const uint8_t key[], int key_len) {
#if defined(NRF52_PLATFORM)
  uint8_t normalized[7];
  normalizeBlobKey(key, key_len, normalized);
  const uint8_t bucket = blobBucketFor(normalized);
  BlobRec* records = (BlobRec*)malloc(sizeof(BlobRec) * BLOB_BUCKET_SLOTS);
  if (records == nullptr) return false;
  const bool bucket_valid = loadBlobBucket(
      _getContactsChannelsFS(), bucket, records);
  if (!bucket_valid) {
    memset(records, 0, sizeof(BlobRec) * BLOB_BUCKET_SLOTS);
  }

  uint8_t selected = 0;
  uint32_t oldest = 0xFFFFFFFFUL;
  bool found = false;
  for (uint8_t i = 0; i < BLOB_BUCKET_SLOTS; i++) {
    if (memcmp(records[i].key, normalized, sizeof(records[i].key)) == 0
        && (records[i].timestamp != 0 || records[i].len != 0)) {
      selected = i;
      found = true;
      break;
    }
    if (records[i].timestamp < oldest) {
      oldest = records[i].timestamp;
      selected = i;
    }
  }
  const bool legacy_exists =
      _getContactsChannelsFS()->exists("/adv_blobs");
  if (!legacy_exists && bucket_valid
      && (!found || records[selected].len == 0)) {
    // Only a successfully verified bucket can prove the key is absent or
    // already tombstoned. A failed bucket read may be transient and must not
    // turn a stale cached advert into an acknowledged deletion.
    free(records);
    return true;
  }
  BlobRec& tombstone = records[selected];
  memset(&tombstone, 0, sizeof(tombstone));
  memcpy(tombstone.key, normalized, sizeof(tombstone.key));
  tombstone.timestamp = _clock->getCurrentTime();
  if (tombstone.timestamp == 0) tombstone.timestamp = 1;
  const bool saved = saveBlobBucket(_getContactsChannelsFS(), bucket, records);
  free(records);
  if (!saved) return false;
  if (!clearLegacyBlobRecord(_getContactsChannelsFS(), normalized)) {
    // The durable zero-length bucket record already masks the legacy entry,
    // and checkAdvBlobFile() retires the reconstructable legacy cache on the
    // next boot. The cache mutation cannot be rolled back after this commit,
    // so report deletion success and keep the live contact table consistent
    // with the authoritative tombstone.
    MESH_DEBUG_PRINTLN(
        "DataStore: deleted advert tombstoned; legacy cleanup deferred");
  }
  return true;
#else
  return true; // this is just a stub on NRF52/STM32 platforms
#endif
}
#else
inline void makeBlobPath(const uint8_t key[], int key_len, char* path, size_t path_size) {
  char fname[18];
  if (key_len > 8) key_len = 8; // just use first 8 bytes (prefix)
  mesh::Utils::toHex(fname, key, key_len);
  sprintf(path, "/bl/%s", fname);
}

uint8_t DataStore::getBlobByKey(const uint8_t key[], int key_len, uint8_t dest_buf[]) {
  char path[64];
  makeBlobPath(key, key_len, path, sizeof(path));

  if (_fs->exists(path)) {
    File f = openRead(_fs, path);
    if (f) {
      int len = f.read(dest_buf, 255); // currently MAX 255 byte blob len supported!!
      f.close();
      return len;
    }
  }
  return 0; // not found
}

bool DataStore::putBlobByKey(const uint8_t key[], int key_len, const uint8_t src_buf[], uint8_t len) {
  char path[64];
  makeBlobPath(key, key_len, path, sizeof(path));

  File f = openWrite(_fs, path);
  if (f) {
    int n = f.write(src_buf, len);
    f.close();
    if (n == len) return true; // success!

    _fs->remove(path); // blob was only partially written!
  }
  return false; // error
}

bool DataStore::deleteBlobByKey(const uint8_t key[], int key_len) {
  char path[64];
  makeBlobPath(key, key_len, path, sizeof(path));

  _fs->remove(path);
  
  return true; // return true even if file did not exist
}
#endif

#if MESH_CONTACT_CACHE
namespace {
bool cachedContactFilter(const ContactInfo& c) { return c.type != ADV_TYPE_NONE; }
#if MESH_CONTACT_SECRET_FLASH_CACHE
#if defined(NRF52_PLATFORM)
// ExtraFS allocates 4 KiB data blocks. Pack derived keys into almost a full
// block; tiny pages would consume the available flash after only a few peers.
constexpr size_t SECRET_PAGE_SLOTS = 56;
#else
constexpr size_t SECRET_PAGE_SLOTS = 8;
#endif
constexpr size_t SECRET_HEADER_SIZE = 36;
constexpr size_t SECRET_RECORD_SIZE = 68;
void secretPagePath(uint16_t slot, char path[24]) {
  snprintf(path, 24, "/csecret%03x", slot / SECRET_PAGE_SLOTS);
}
void secretHeader(uint8_t header[SECRET_HEADER_SIZE], const uint8_t identity[32]) {
  memcpy(header, "MCS1", 4);
  memcpy(header + 4, identity, 32);
}
bool validSecretRecord(const uint8_t record[SECRET_RECORD_SIZE]) {
  return mesh::storage::readLE32(record + 64) ==
      mesh::storage::updateCRC32(0xffffffff, record, 64);
}
#endif
}

bool DataStore::readStoredPath(uint16_t source, uint8_t path[64]) {
  if (source == mesh::ContactPathStorage::NONE || hasIncompleteContactLoad()) return false;
  char filename[24] = "/contacts3";
  size_t offset = static_cast<size_t>(source) * mesh::storage::CONTACT_RECORD_SIZE;
#if defined(ESP32_PLATFORM)
  if (!(source & mesh::ContactPathStorage::PAGED)) {
    // SPIFFS open scans metadata. Doing it for every contact made bulk saves
    // and app synchronization take tens of seconds. Share one read handle;
    // loadContacts/saveContacts close it before recovery or replacement.
    if (!_contact_path_reader)
      _contact_path_reader = openRead(_getContactsChannelsFS(), filename);
    File& file = _contact_path_reader;
    const bool ok = file && file.size() >= offset + mesh::storage::CONTACT_RECORD_SIZE
        && file.seek(offset + 76) && file.read(path, 64) == 64;
    if (!ok) file.close();
    return ok;
  }
#endif
  if (source & mesh::ContactPathStorage::PAGED) {
#if defined(NRF52_PLATFORM)
    const uint16_t slot = source & ~mesh::ContactPathStorage::PAGED;
    if (slot >= mesh::storage::CONTACT_PAGE_COUNT * mesh::storage::CONTACTS_PER_PAGE) return false;
    makeContactPagePath(slot / mesh::storage::CONTACTS_PER_PAGE, filename);
    offset = mesh::storage::CONTACT_PAGE_HEADER_SIZE +
        (slot % mesh::storage::CONTACTS_PER_PAGE) * mesh::storage::CONTACT_RECORD_SIZE;
#else
    return false;
#endif
  }
  File file = openRead(_getContactsChannelsFS(), filename);
  const bool ok = file && file.size() >= offset + mesh::storage::CONTACT_RECORD_SIZE
      && file.seek(offset + 76) && file.read(path, 64) == 64;
  if (file) file.close();
  // The pool checks the CRC captured when this immutable path value was bound.
  return ok;
}

bool DataStore::flushCachedPaths() {
  if (!_cache_host || hasIncompleteContactLoad()
      || !flushContactWrites(_cache_host, cachedContactFilter)) return false;
  _cache_host->onContactCacheFlushed();
  return true;
}

#if MESH_CONTACT_SECRET_FLASH_CACHE
uint16_t DataStore::secretSlot(const uint8_t peer[32]) const {
  if (!_cache_host || hasIncompleteContactLoad()) return mesh::storage::CONTACT_SLOT_NONE;
  uint16_t persistent_index = 0;
  for (uint32_t i = 0;; ++i) {
    const auto* c = _cache_host->getContactForStore(i);
    if (!c) break;
    if (c->type == ADV_TYPE_NONE) continue;
    if (memcmp(c->id.pub_key, peer, 32) == 0) {
#if defined(NRF52_PLATFORM)
      return c->storage_slot;
#else
      return persistent_index;
#endif
    }
    ++persistent_index;
  }
  return mesh::storage::CONTACT_SLOT_NONE;
}

bool DataStore::readSavedSecret(const uint8_t peer[32], const uint8_t identity[32],
                                uint8_t secret[32]) {
  const uint16_t slot = secretSlot(peer);
  if (slot == mesh::storage::CONTACT_SLOT_NONE) return false;
  char path[24];
  secretPagePath(slot, path);
#if defined(ESP32_PLATFORM)
  if (!mesh::ContactFileTransaction::recover(_getContactsChannelsFS(), path)) return false;
#endif
  File file = openRead(_getContactsChannelsFS(), path);
  uint8_t expected[SECRET_HEADER_SIZE], header[SECRET_HEADER_SIZE];
  secretHeader(expected, identity);
  uint8_t record[SECRET_RECORD_SIZE];
  const bool ok = file && file.size() == SECRET_HEADER_SIZE + SECRET_PAGE_SLOTS * SECRET_RECORD_SIZE
      && file.read(header, sizeof(header)) == sizeof(header)
      && memcmp(header, expected, sizeof(header)) == 0
      && file.seek(SECRET_HEADER_SIZE + (slot % SECRET_PAGE_SLOTS) * SECRET_RECORD_SIZE)
      && file.read(record, sizeof(record)) == sizeof(record)
      && memcmp(record, peer, 32) == 0 && validSecretRecord(record);
  if (file) file.close();
  if (ok) memcpy(secret, record + 32, 32);
  return ok;
}

bool DataStore::saveSecret(const uint8_t peer[32], const uint8_t identity[32],
                           const uint8_t secret[32]) {
  const uint16_t slot = secretSlot(peer);
  if (slot == mesh::storage::CONTACT_SLOT_NONE ||
      (_secret_retry_at && static_cast<int32_t>(millis() - _secret_retry_at) < 0)) return false;

  // Derived keys are expendable. Keep room for contact replacement, preferences
  // and filesystem metadata instead of letting this cache prevent a real save.
  size_t free_bytes = 0;
  size_t reserve_bytes = 0;
#if defined(NRF52_PLATFORM)
  const auto* config = _getContactsChannelsFS()->_getFS()->cfg;
  const int used = _getLfsUsedBlockCount(_getContactsChannelsFS());
  if (used < 0 || static_cast<uint32_t>(used) > config->block_count) return false;
  free_bytes = (config->block_count - used) * config->block_size;
  reserve_bytes = 4 * config->block_size;
#else
  const size_t total = SPIFFS.totalBytes(), used = SPIFFS.usedBytes();
  if (used > total) return false;
  free_bytes = total - used;
  reserve_bytes = MAX_CONTACTS * mesh::storage::CONTACT_RECORD_SIZE + 16384;
#endif
  if (free_bytes < reserve_bytes + 2 * (SECRET_HEADER_SIZE + SECRET_PAGE_SLOTS * SECRET_RECORD_SIZE)) {
    _secret_retry_at = millis() + 30000;
    return false;
  }

  char path[24];
  secretPagePath(slot, path);
  uint8_t header[SECRET_HEADER_SIZE], previous_header[SECRET_HEADER_SIZE];
  secretHeader(header, identity);
#if defined(ESP32_PLATFORM)
  if (!mesh::ContactFileTransaction::recover(_getContactsChannelsFS(), path)) return false;
#endif
  File old = openRead(_getContactsChannelsFS(), path);
  bool reuse = old && old.size() == SECRET_HEADER_SIZE + SECRET_PAGE_SLOTS * SECRET_RECORD_SIZE
      && old.read(previous_header, sizeof(previous_header)) == sizeof(previous_header)
      && memcmp(previous_header, header, sizeof(header)) == 0;
#if defined(NRF52_PLATFORM)
  mesh::AtomicFileWriter writer(_getContactsChannelsFS(), path);
#else
  mesh::ContactFileTransaction writer(_getContactsChannelsFS(), path);
#endif
  bool ok = writer && writer.write(header, sizeof(header)) == sizeof(header);
  uint8_t record[SECRET_RECORD_SIZE];
  for (size_t i = 0; ok && i < SECRET_PAGE_SLOTS; ++i) {
    memset(record, 0, sizeof(record));
    if (reuse && (old.read(record, sizeof(record)) != sizeof(record) || !validSecretRecord(record)))
      memset(record, 0, sizeof(record));
    if (i == slot % SECRET_PAGE_SLOTS) {
      memcpy(record, peer, 32);
      memcpy(record + 32, secret, 32);
      mesh::storage::writeLE32(record + 64,
          mesh::storage::updateCRC32(0xffffffff, record, 64));
    }
    ok = writer.write(record, sizeof(record)) == sizeof(record);
  }
  if (old) old.close();
  ok = writer.commit(ok);
  if (!ok) _secret_retry_at = millis() + 30000;
  return ok;
}
#endif
#endif
