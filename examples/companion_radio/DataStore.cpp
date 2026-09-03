#include <Arduino.h>
#include <stdlib.h>
#include "DataStore.h"

#if defined(NRF52_PLATFORM)
#include <helpers/AtomicFileWriter.h>
#if defined(EXTRAFS) && !defined(QSPIFLASH)
#include <helpers/nrf52/InternalSecondaryFsRepair.h>
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
    // A traversal error can be transient, and formatting here would erase the
    // only identity before MyMesh can distinguish recovery from a fresh boot.
    // Preserve the filesystem and fail closed; a reboot may recover it, while
    // an explicit factory reset remains the destructive recovery operation.
    MESH_DEBUG_PRINTLN(
        "DataStore: primary LittleFS metadata is unavailable; preserving it and blocking startup writes");
    _primary_storage_unavailable = true;
    _identity_creation_blocked = true;
    _contact_load_incomplete = true;
    _prefs_load_incomplete = true;
    _fsExtra = nullptr;
    return;
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
    #include <CustomLFS.h>
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
      "/.extrafs.mig.tmp"};
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

  CustomLFS* extra = static_cast<CustomLFS*>(_configuredFsExtra);
  if (_primary_storage_unavailable
      || !mesh::storage::isExpectedInternalExtraFsGeometry(
          extra->getFlashAddr(), extra->getFlashSize(), extra->getBlockSize())
      || !mesh::storage::isInternalExtraFsReservedByApplication(
          (uint32_t)(uintptr_t)__flash_arduino_end)) {
    disableSecondaryFS(true);
    MESH_DEBUG_PRINTLN("DataStore: refusing automatic ExtraFS recovery outside reserved 100 KiB region");
    return false;
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
            MESH_DEBUG_PRINTLN("DataStore: internal ExtraFS remains unusable; rebuilding 100 KiB (secondary-only data may be lost)");
            return reinitializeInternalExtraFS();
          });
  if (result == mesh::storage::InternalSecondaryFsRecoveryResult::Failed) {
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

bool DataStore::reinitializeInternalExtraFS() {
  if (_configuredFsExtra == nullptr) return false;

  CustomLFS* extra = static_cast<CustomLFS*>(_configuredFsExtra);
  if (!mesh::storage::isExpectedInternalExtraFsGeometry(
          extra->getFlashAddr(), extra->getFlashSize(),
          extra->getBlockSize())
      || !mesh::storage::isInternalExtraFsReservedByApplication(
          (uint32_t)(uintptr_t)__flash_arduino_end)) {
    _fsExtra = nullptr;
    MESH_DEBUG_PRINTLN("DataStore: refusing internal ExtraFS repair with unexpected geometry");
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
        MESH_DEBUG_PRINTLN("DataStore: internal ExtraFS repair format failed");
        break;
      case mesh::storage::InternalSecondaryFsRepairResult::MountFailed:
        MESH_DEBUG_PRINTLN("DataStore: internal ExtraFS repair mount failed");
        break;
      case mesh::storage::InternalSecondaryFsRepairResult::ValidationFailed:
        MESH_DEBUG_PRINTLN("DataStore: internal ExtraFS repair validation failed");
        break;
      default:
        break;
    }
    return false;
  }

  _fsExtra = _configuredFsExtra;
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
#if defined(NRF52_PLATFORM) || defined(STM32_PLATFORM)
  return _fs->open(filename, FILE_O_READ);
#elif defined(RP2040_PLATFORM)
  return _fs->open(filename, "r");
#else
  return _fs->open(filename, "r", false);
#endif
}

File DataStore::openRead(FILESYSTEM* fs, const char* filename) {
#if defined(NRF52_PLATFORM) || defined(STM32_PLATFORM)
  return fs->open(filename, FILE_O_READ);
#elif defined(RP2040_PLATFORM)
  return fs->open(filename, "r");
#else
  return fs->open(filename, "r", false);
#endif
}

bool DataStore::removeFile(const char* filename) {
  return _fs->remove(filename);
}

bool DataStore::removeFile(FILESYSTEM* fs, const char* filename) {
  return fs->remove(filename);
}

bool DataStore::formatFileSystem() {
#if defined(NRF52_PLATFORM) || defined(STM32_PLATFORM)
  #if defined(NRF52_PLATFORM)
  resetContactPageState();
  #endif
  const bool primary_success = _fs->format();
#if defined(NRF52_PLATFORM) && defined(EXTRAFS) && !defined(QSPIFLASH)
  // Factory reset/rebuild is already an explicit destructive operation. Use
  // the configured pointer so it also clears and reactivates an ExtraFS which
  // normal boot deliberately quarantined after failed traversal validation.
  const bool secondary_success = _configuredFsExtra == nullptr
      || reinitializeInternalExtraFS();
#else
  const bool secondary_success = _fsExtra == nullptr || _fsExtra->format();
#endif
  const bool success = primary_success && secondary_success;
#if defined(NRF52_PLATFORM)
  // A successful explicit erase creates a new empty authoritative store. A
  // failed erase must keep the boot's load quarantine latched.
  if (success) {
    _contact_load_incomplete = false;
    _identity_creation_blocked = false;
    _prefs_load_incomplete = false;
  }
#endif
  return success;
#elif defined(RP2040_PLATFORM)
  return LittleFS.format();
#elif defined(ESP32)
  bool fs_success = ((fs::SPIFFSFS *)_fs)->format();
  esp_err_t nvs_err = nvs_flash_erase(); // no need to reinit, will be done by reboot
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

bool DataStore::loadMainIdentity(mesh::LocalIdentity &identity) {
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
  return identity_store.load("_main", identity);
#endif
}

bool DataStore::canCreateMainIdentity() const {
#if defined(NRF52_PLATFORM)
  return !_identity_creation_blocked;
#else
  return true;
#endif
}

bool DataStore::saveMainIdentity(const mesh::LocalIdentity &identity) {
#if defined(NRF52_PLATFORM)
  if (_identity_creation_blocked) return false;
#endif
  return identity_store.save("_main", identity);
}

bool DataStore::loadPrefs(CompanionNodePrefs& prefs, double& node_lat,
                          double& node_lon) {
#if defined(NRF52_PLATFORM)
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
  if (_fs->exists("/new_prefs")) {
    return loadPrefsInt("/new_prefs", prefs, node_lat, node_lon);
  }
  if (!_fs->exists("/node_prefs")) return true;
#endif

  if (!loadPrefsInt("/node_prefs", prefs, node_lat, node_lon)) {
#if defined(NRF52_PLATFORM)
    _prefs_load_incomplete = true;
#endif
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
#if defined(NRF52_PLATFORM)
  if (_prefs_load_incomplete) return false;
  if (_primary_storage_unavailable) return false;
  mesh::AtomicFileWriter file(_fs, "/new_prefs");
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

#if defined(NRF52_PLATFORM)
    success = file.commit(success);
    if (!success) MESH_DEBUG_PRINTLN("DataStore: atomic preferences write failed");
#else
    file.close();
#endif
    return success;
  }
  return false;
}

static void serializeContactRecord(const ContactInfo& c,
                                   uint8_t out[mesh::storage::CONTACT_RECORD_SIZE]) {
  size_t offset = 0;
  const uint8_t unused = 0;
  memcpy(&out[offset], c.id.pub_key, 32); offset += 32;
  memcpy(&out[offset], c.name, 32); offset += 32;
  out[offset++] = c.type;
  out[offset++] = c.flags;
  out[offset++] = unused;
  memcpy(&out[offset], &c.sync_since, 4); offset += 4;
  out[offset++] = c.out_path_len;
  memcpy(&out[offset], &c.last_advert_timestamp, 4); offset += 4;
  memcpy(&out[offset], c.out_path, 64); offset += 64;
  memcpy(&out[offset], &c.lastmod, 4); offset += 4;
  memcpy(&out[offset], &c.gps_lat, 4); offset += 4;
  memcpy(&out[offset], &c.gps_lon, 4);
}

static bool deserializeContactRecord(
    const uint8_t in[mesh::storage::CONTACT_RECORD_SIZE], ContactInfo& c) {
  size_t offset = 0;
  uint8_t pub_key[32];
  memcpy(pub_key, &in[offset], 32); offset += 32;
  memcpy(c.name, &in[offset], 32); offset += 32;
  c.name[sizeof(c.name) - 1] = 0;
  c.type = in[offset++];
  c.flags = in[offset++];
  offset++; // reserved
  memcpy(&c.sync_since, &in[offset], 4); offset += 4;
  c.out_path_len = in[offset++];
  memcpy(&c.last_advert_timestamp, &in[offset], 4); offset += 4;
  memcpy(c.out_path, &in[offset], 64); offset += 64;
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
  if (clear_incomplete) _contact_load_incomplete = false;
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
      if (!deserializeContactRecord(record, contact) || !_contact_slots.reserve(slot)) {
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

  uint32_t payload_crc = 0xFFFFFFFFUL;
  uint8_t record[mesh::storage::CONTACT_RECORD_SIZE];
  for (uint8_t slot = 0; slot < mesh::storage::CONTACTS_PER_PAGE; slot++) {
    memset(record, 0, sizeof(record));
    if (page_contacts[slot] != nullptr) {
      serializeContactRecord(*page_contacts[slot], record);
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
      serializeContactRecord(*page_contacts[slot], record);
    }
    wrote = writer.write(record, sizeof(record)) == sizeof(record);
  }
  if (!writer.commit(wrote)) {
    MESH_DEBUG_PRINTLN("DataStore: atomic contact page %u write failed", page);
    return false;
  }

  _contact_page_generations[page] = header.generation;
  return true;
}
#endif

void DataStore::loadContacts(DataStoreHost* host) {
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
#if defined(NRF52_PLATFORM)
    _legacy_contact_count =
        mesh::storage::legacyContactCountForSize(legacy_size);
    uint16_t record_index = 0;
    bool legacy_read_failed = false;
    bool legacy_host_refused = false;
#endif
    while (!full
#if defined(NRF52_PLATFORM)
           && record_index < _legacy_contact_count
#endif
    ) {
      uint8_t record[mesh::storage::CONTACT_RECORD_SIZE];
      if (file.read(record, sizeof(record)) != sizeof(record)) {
#if defined(NRF52_PLATFORM)
        legacy_read_failed = true;
#endif
        break;
      }

      ContactInfo contact;
      if (!deserializeContactRecord(record, contact)) {
        // Preserve the contact while containing corrupt legacy routing data.
        contact.out_path_len = OUT_PATH_UNKNOWN;
      }
#if defined(NRF52_PLATFORM)
      const uint16_t slot = record_index++;
      if (!_contact_slots.reserve(slot)) {
        legacy_read_failed = true;
        break;
      }
      contact.storage_slot = slot;
#endif
      if (!host->onContactLoaded(contact)) {
        full = true;
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
#if defined(NRF52_PLATFORM)
  bool success = true;
  for (uint32_t idx = 0;; idx++) {
    ContactInfo* contact = host->getContactForStore(idx);
    if (contact == NULL) break;
    if (filter && !filter(*contact)) continue;
    success = markContactDirty(*contact) && success;
  }
  return flushContactWrites(host, filter) && success;
#else
  File file = openWrite(_getContactsChannelsFS(), "/contacts3");
  bool success = (bool)file;
  if (file) {
    uint32_t idx = 0;
    ContactInfo c;
    uint8_t unused = 0;

    while (host->getContactForSave(idx, c)) {
      if (filter && !filter(c)) {
        idx++;  // advance to next contact
        continue;
      }
      success = (file.write(c.id.pub_key, 32) == 32);
      success = success && (file.write((uint8_t *)&c.name, 32) == 32);
      success = success && (file.write(&c.type, 1) == 1);
      success = success && (file.write(&c.flags, 1) == 1);
      success = success && (file.write(&unused, 1) == 1);
      success = success && (file.write((uint8_t *)&c.sync_since, 4) == 4);
      success = success && (file.write((uint8_t *)&c.out_path_len, 1) == 1);
      success = success && (file.write((uint8_t *)&c.last_advert_timestamp, 4) == 4);
      success = success && (file.write(c.out_path, 64) == 64);
      success = success && (file.write((uint8_t *)&c.lastmod, 4) == 4);
      success = success && (file.write((uint8_t *)&c.gps_lat, 4) == 4);
      success = success && (file.write((uint8_t *)&c.gps_lon, 4) == 4);

      if (!success) break; // write failed

      idx++;  // advance to next contact
    }
    file.close();
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
#if defined(NRF52_PLATFORM)
  return _contact_load_incomplete || !_unread_contact_pages.empty();
#else
  return false;
#endif
}

void DataStore::loadChannels(DataStoreHost* host) {
#if defined(NRF52_PLATFORM)
  if (_contact_load_incomplete) {
    MESH_DEBUG_PRINTLN(
        "DataStore: channel load quarantined after storage I/O failure");
    return;
  }

  FILESYSTEM* contacts_fs = _getContactsChannelsFS();
  bool channels_exist = false;
  uint32_t channels_size = 0;
  if (!contactPathPresence(contacts_fs, "/channels2", channels_exist,
                           &channels_size)) {
    _contact_load_incomplete = true;
    return;
  }
  if (!channels_exist) return;

  static const uint32_t CHANNEL_RECORD_SIZE = 4 + 32 + 32;
  if ((channels_size % CHANNEL_RECORD_SIZE) != 0
      || channels_size / CHANNEL_RECORD_SIZE > MAX_GROUP_CHANNELS) {
    MESH_DEBUG_PRINTLN(
        "DataStore: invalid channels file size; storage quarantined");
    _contact_load_incomplete = true;
    return;
  }
#endif
  File file = openRead(_getContactsChannelsFS(), "/channels2");
#if defined(NRF52_PLATFORM)
  if (!file || file.size() != channels_size) {
    if (file) file.close();
    MESH_DEBUG_PRINTLN(
        "DataStore: channels file could not be opened consistently; storage quarantined");
    _contact_load_incomplete = true;
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
    _contact_load_incomplete = true;
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
  }
  file.close();
  if (!success) {
    free(loaded);
    MESH_DEBUG_PRINTLN(
        "DataStore: channels file read failed; storage quarantined");
    _contact_load_incomplete = true;
    return;
  }
  for (uint8_t channel_idx = 0; channel_idx < channel_count; channel_idx++) {
    if (!host->onChannelLoaded(channel_idx, loaded[channel_idx])) {
      free(loaded);
      MESH_DEBUG_PRINTLN(
          "DataStore: channel host refused durable record; storage quarantined");
      _contact_load_incomplete = true;
      return;
    }
  }
  free(loaded);
#else
  if (file) {
    bool full = false;
    uint8_t channel_idx = 0;
    while (!full) {
      ChannelDetails ch;
      uint8_t unused[4];

      bool success = (file.read(unused, 4) == 4);
      success = success && (file.read((uint8_t *)ch.name, 32) == 32);
      success = success && (file.read((uint8_t *)ch.channel.secret, 32) == 32);

      if (!success) break; // EOF

      if (host->onChannelLoaded(channel_idx, ch)) {
        channel_idx++;
      } else {
        full = true;
      }
    }
    file.close();
  }
#endif
}

bool DataStore::saveChannels(DataStoreHost* host) {
#if defined(NRF52_PLATFORM)
  if (_contact_load_incomplete) return false;
#endif
#if defined(NRF52_PLATFORM)
  mesh::AtomicFileWriter file(_getContactsChannelsFS(), "/channels2");
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
      success = (file.write(unused, 4) == 4);
      success = success && (file.write((uint8_t *)ch.name, 32) == 32);
      success = success && (file.write((uint8_t *)ch.channel.secret, 32) == 32);

      if (!success) break; // write failed
      channel_idx++;
    }
#if defined(NRF52_PLATFORM)
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
