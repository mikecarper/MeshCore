#include <Arduino.h>
#include <stdlib.h>
#include "DataStore.h"

#if defined(NRF52_PLATFORM)
#include <helpers/AtomicFileWriter.h>
#endif

// Linked presence of this symbol is the authoritative signal that this firmware actually mounts the
// internal 0xD4000 ExtraFS. OTA layout code references it weakly, so non-companion roles can reclaim the
// reserved range even though nrf52_base defines EXTRAFS globally.
#if defined(NRF52_PLATFORM) && defined(EXTRAFS) && !defined(QSPIFLASH)
extern "C" __attribute__((used)) const uint8_t g_meshcore_internal_extrafs = 1u;
#endif

#if defined(EXTRAFS) || defined(QSPIFLASH)
  #define MAX_BLOBRECS 100
#else
  #define MAX_BLOBRECS 20
#endif

DataStore::DataStore(FILESYSTEM& fs, mesh::RTCClock& clock) : _fs(&fs), _fsExtra(nullptr), _clock(&clock),
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
DataStore::DataStore(FILESYSTEM& fs, FILESYSTEM& fsExtra, mesh::RTCClock& clock) : _fs(&fs), _fsExtra(&fsExtra), _clock(&clock),
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
static bool recoverPrimaryFilesystem(FILESYSTEM* fs);
static void cleanupAtomicTempFiles(FILESYSTEM* fs);
#endif

void DataStore::begin() {
#if defined(RP2040_PLATFORM)
  identity_store.begin();
#endif

#if defined(NRF52_PLATFORM) || defined(STM32_PLATFORM)
#if defined(NRF52_PLATFORM)
  bool primary_ready = validateLfsFilesystem(_fs);
  if (!primary_ready) {
    MESH_DEBUG_PRINTLN("DataStore: primary LittleFS metadata is corrupt; rebuilding before first write");
    primary_ready = recoverPrimaryFilesystem(_fs);
  }
  if (_fsExtra != nullptr && !validateLfsFilesystem(_fsExtra)) {
    // Do not erase removable/external storage automatically.  Keep it intact
    // for recovery and boot from the known-good internal filesystem.
    MESH_DEBUG_PRINTLN("DataStore: secondary LittleFS metadata is corrupt; using internal storage");
    _fsExtra = nullptr;
  }
  if (primary_ready) cleanupAtomicTempFiles(_fs);
  if (_fsExtra != nullptr) cleanupAtomicTempFiles(_fsExtra);
  resetContactPageState();
#endif
  #if defined(EXTRAFS) || defined(QSPIFLASH)
  if (_fsExtra != nullptr) migrateToSecondaryFS();
  #endif
  checkAdvBlobFile();
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
static bool recoverPrimaryFilesystem(FILESYSTEM* fs) {
  // Once traversal has found a bad pointer or metadata cycle, even read-only
  // path lookup on this mounted filesystem can enter the same unbounded walk.
  // Do not try to copy identity/preferences out through the corrupted metadata:
  // rebuild immediately so the next mutation cannot hard-fault or hang forever.
  const bool formatted = fs->format();
  if (!formatted) {
    MESH_DEBUG_PRINTLN("DataStore: primary LittleFS rebuild failed");
  } else {
    MESH_DEBUG_PRINTLN("DataStore: primary LittleFS rebuilt; corrupt contents discarded");
  }
  return formatted;
}

static void cleanupAtomicTempFiles(FILESYSTEM* fs) {
  if (fs == nullptr) return;

  static const char* fixed_temp_paths[] = {
      "/_main.id.tmp", "/new_prefs.tmp", "/channels2.tmp",
      "/contacts3.tmp", "/contacts4.mig.tmp", "/adv_blobs.tmp"};
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
#endif

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
  if (_fsExtra == nullptr) {
    return _fs->format();
  } else {
    return _fs->format() && _fsExtra->format();
  }
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

bool DataStore::loadMainIdentity(mesh::LocalIdentity &identity) {
  return identity_store.load("_main", identity);
}

bool DataStore::saveMainIdentity(const mesh::LocalIdentity &identity) {
  return identity_store.save("_main", identity);
}

void DataStore::loadPrefs(CompanionNodePrefs& prefs, double& node_lat, double& node_lon) {
  if (_fs->exists("/new_prefs")) {
    loadPrefsInt("/new_prefs", prefs, node_lat, node_lon); // new filename
  } else if (_fs->exists("/node_prefs")) {
    loadPrefsInt("/node_prefs", prefs, node_lat, node_lon);
    if (savePrefs(prefs, node_lat, node_lon)) {
      _fs->remove("/node_prefs"); // remove old only after verified replacement
    }
  }
}

void DataStore::loadPrefsInt(const char *filename, CompanionNodePrefs& _prefs, double& node_lat, double& node_lon) {
  File file = openRead(_fs, filename);
  if (file) {
    uint8_t pad[8];

    file.read((uint8_t *)&_prefs.airtime_factor, sizeof(float));                           // 0
    file.read((uint8_t *)_prefs.node_name, sizeof(_prefs.node_name));                      // 4
    file.read(pad, 4);                                                                     // 36
    file.read((uint8_t *)&node_lat, sizeof(node_lat));                                     // 40
    file.read((uint8_t *)&node_lon, sizeof(node_lon));                                     // 48
    file.read((uint8_t *)&_prefs.freq, sizeof(_prefs.freq));                               // 56
    file.read((uint8_t *)&_prefs.sf, sizeof(_prefs.sf));                                   // 60
    file.read((uint8_t *)&_prefs.cr, sizeof(_prefs.cr));                                   // 61
    file.read((uint8_t *)&_prefs.client_repeat, sizeof(_prefs.client_repeat));             // 62
    file.read((uint8_t *)&_prefs.manual_add_contacts, sizeof(_prefs.manual_add_contacts)); // 63
    file.read((uint8_t *)&_prefs.bw, sizeof(_prefs.bw));                                   // 64
    file.read((uint8_t *)&_prefs.tx_power_dbm, sizeof(_prefs.tx_power_dbm));               // 68
    file.read((uint8_t *)&_prefs.telemetry_mode_base, sizeof(_prefs.telemetry_mode_base)); // 69
    file.read((uint8_t *)&_prefs.telemetry_mode_loc, sizeof(_prefs.telemetry_mode_loc));   // 70
    file.read((uint8_t *)&_prefs.telemetry_mode_env, sizeof(_prefs.telemetry_mode_env));   // 71
    file.read((uint8_t *)&_prefs.rx_delay_base, sizeof(_prefs.rx_delay_base));             // 72
    file.read((uint8_t *)&_prefs.advert_loc_policy, sizeof(_prefs.advert_loc_policy));     // 76
    file.read((uint8_t *)&_prefs.multi_acks, sizeof(_prefs.multi_acks));                   // 77
    file.read((uint8_t *)&_prefs.path_hash_mode, sizeof(_prefs.path_hash_mode));           // 78
    file.read(pad, 1);                                                                     // 79
    file.read((uint8_t *)&_prefs.ble_pin, sizeof(_prefs.ble_pin));                         // 80
    file.read((uint8_t *)&_prefs.buzzer_quiet, sizeof(_prefs.buzzer_quiet));               // 84
    file.read((uint8_t *)&_prefs.gps_enabled, sizeof(_prefs.gps_enabled));                 // 85
    file.read((uint8_t *)&_prefs.gps_interval, sizeof(_prefs.gps_interval));               // 86
    file.read((uint8_t *)&_prefs.autoadd_config, sizeof(_prefs.autoadd_config));           // 87
    file.read((uint8_t *)&_prefs.autoadd_max_hops, sizeof(_prefs.autoadd_max_hops));       // 88
    file.read((uint8_t *)&_prefs.rx_boosted_gain, sizeof(_prefs.rx_boosted_gain));         // 89
    file.read((uint8_t *)_prefs.default_scope_name, sizeof(_prefs.default_scope_name));    // 90
    file.read((uint8_t *)_prefs.default_scope_key, sizeof(_prefs.default_scope_key));     // 121
    file.read((uint8_t *)&_prefs.radio_fem_rxgain, sizeof(_prefs.radio_fem_rxgain));      // 122
    file.read((uint8_t *)&_prefs.radio_fem_rxgain_override,
              sizeof(_prefs.radio_fem_rxgain_override));                                  // 123
    if (file.available() >= (int)sizeof(_prefs.vibe_quiet)) {
      file.read((uint8_t *)&_prefs.vibe_quiet, sizeof(_prefs.vibe_quiet));                  // 124
    }
    if (file.available() >= (int)sizeof(_prefs.radio_fem_txgain)) {
      file.read((uint8_t *)&_prefs.radio_fem_txgain, sizeof(_prefs.radio_fem_txgain));      // 125
    }
    const size_t rxps_tail_size = sizeof(_prefs.rx_powersaving_enabled)
        + sizeof(_prefs.rx_ps_rx_us) + sizeof(_prefs.rx_ps_sleep_us)
        + sizeof(_prefs.rx_ps_level) + sizeof(_prefs.rx_ps_preamble);
    if (file.available() >= (int)rxps_tail_size) {
      file.read((uint8_t *)&_prefs.rx_powersaving_enabled,
                sizeof(_prefs.rx_powersaving_enabled));                                    // 126
      file.read((uint8_t *)&_prefs.rx_ps_rx_us, sizeof(_prefs.rx_ps_rx_us));                // 127
      file.read((uint8_t *)&_prefs.rx_ps_sleep_us, sizeof(_prefs.rx_ps_sleep_us));          // 131
      file.read((uint8_t *)&_prefs.rx_ps_level, sizeof(_prefs.rx_ps_level));                // 135
      file.read((uint8_t *)&_prefs.rx_ps_preamble, sizeof(_prefs.rx_ps_preamble));          // 136
      if (file.available() >= (int)sizeof(_prefs.powersaving_enabled)) {
        file.read((uint8_t *)&_prefs.powersaving_enabled,
                  sizeof(_prefs.powersaving_enabled));                                    // 137
        if (file.available() >= (int)sizeof(_prefs.wifi_enabled)) {
          file.read((uint8_t *)&_prefs.wifi_enabled,
                    sizeof(_prefs.wifi_enabled));                                         // 138
          if (file.available() >= (int)sizeof(_prefs.powersaving_policy_version)) {
            file.read((uint8_t *)&_prefs.powersaving_policy_version,
                      sizeof(_prefs.powersaving_policy_version));                         // 139
            if (file.available() >= (int)sizeof(_prefs.usb_logging_enabled)) {
              file.read((uint8_t *)&_prefs.usb_logging_enabled,
                        sizeof(_prefs.usb_logging_enabled));                              // 140
              if (file.available() >= (int)sizeof(_prefs.bluetooth_name)) {
                file.read((uint8_t *)_prefs.bluetooth_name,
                          sizeof(_prefs.bluetooth_name));                                // 141
                if (file.available()
                    >= (int)sizeof(_prefs.display_rotation_degrees)) {
                  file.read((uint8_t *)&_prefs.display_rotation_degrees,
                            sizeof(_prefs.display_rotation_degrees));
                  if (file.available() >= (int)sizeof(_prefs.cad_enabled)) {
                    file.read((uint8_t *)&_prefs.cad_enabled,
                              sizeof(_prefs.cad_enabled));
                    if (file.available()
                        >= (int)sizeof(_prefs.cad_scan_timeout_ms)) {
                      file.read((uint8_t *)&_prefs.cad_scan_timeout_ms,
                                sizeof(_prefs.cad_scan_timeout_ms));
                      if (file.available()
                          >= (int)sizeof(_prefs.cad_retry_delay_ms)) {
                        file.read((uint8_t *)&_prefs.cad_retry_delay_ms,
                                  sizeof(_prefs.cad_retry_delay_ms));
                        if (file.available()
                            >= (int)sizeof(_prefs.cad_max_duration_ms)) {
                          file.read((uint8_t *)&_prefs.cad_max_duration_ms,
                                    sizeof(_prefs.cad_max_duration_ms));
                        }
                      }
                    }
                  }
                }
              }
            }
          }
        }
      }
    }

    file.close();
  }
}

bool DataStore::savePrefs(const CompanionNodePrefs& _prefs, double node_lat, double node_lon) {
#if defined(NRF52_PLATFORM)
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

void DataStore::resetContactPageState() {
  _contact_slots.clear();
  _dirty_contact_pages.clearAll();
  memset(_contact_page_generations, 0, sizeof(_contact_page_generations));
  _legacy_contacts_pending_cleanup = false;
  _legacy_migration_ready = false;
  _legacy_contact_count = 0;
}

bool DataStore::prepareLegacyContactMigration() {
  FILESYSTEM* fs = _getContactsChannelsFS();
  if (fs->exists(CONTACT_MIGRATION_MARKER)) {
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
    if (fs->exists(path) && !fs->remove(path)) clean = false;

    char temp_path[28];
    snprintf(temp_path, sizeof(temp_path), "%s.tmp", path);
    if (fs->exists(temp_path) && !fs->remove(temp_path)) clean = false;
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

bool DataStore::loadContactPages(DataStoreHost* host, uint16_t minimum_slot) {
  bool any_page_file = false;
  FILESYSTEM* fs = _getContactsChannelsFS();

  for (uint8_t page = 0; page < mesh::storage::CONTACT_PAGE_COUNT; page++) {
    char path[24];
    makeContactPagePath(page, path);
    if (!fs->exists(path)) continue;
    any_page_file = true;

    File file = openRead(fs, path);
    if (!file || file.size() != mesh::storage::CONTACT_PAGE_FILE_SIZE) {
      MESH_DEBUG_PRINTLN("DataStore: ignoring invalid contact page %u", page);
      if (file) file.close();
      discardInvalidContactPage(fs, path, page);
      _dirty_contact_pages.mark(page);
      continue;
    }

    uint8_t raw_header[mesh::storage::CONTACT_PAGE_HEADER_SIZE];
    mesh::storage::ContactPageHeader header;
    bool valid = file.read(raw_header, sizeof(raw_header)) == sizeof(raw_header)
        && mesh::storage::decodeContactPageHeader(raw_header, page, header);

    uint32_t crc = 0xFFFFFFFFUL;
    uint16_t remaining = mesh::storage::CONTACT_PAGE_PAYLOAD_SIZE;
    uint8_t chunk[64];
    while (valid && remaining > 0) {
      const uint16_t count = remaining < sizeof(chunk) ? remaining : sizeof(chunk);
      if (file.read(chunk, count) != count) {
        valid = false;
        break;
      }
      crc = mesh::storage::updateCRC32(crc, chunk, count);
      remaining -= count;
    }

    if (!valid || crc != header.payload_crc) {
      MESH_DEBUG_PRINTLN("DataStore: contact page %u failed CRC/format validation", page);
      file.close();
      discardInvalidContactPage(fs, path, page);
      _dirty_contact_pages.mark(page);
      continue;
    }

    _contact_page_generations[page] = header.generation;
    for (uint8_t index = 0; index < mesh::storage::CONTACTS_PER_PAGE; index++) {
      if ((header.occupied & (1UL << index)) == 0) continue;

      const uint16_t slot = (uint16_t)page * mesh::storage::CONTACTS_PER_PAGE + index;
      if (!mesh::storage::loadSlotFromMigratedPage(slot, minimum_slot)) continue;
      uint8_t record[mesh::storage::CONTACT_RECORD_SIZE];
      if (!file.seek(mesh::storage::CONTACT_PAGE_HEADER_SIZE
                     + (uint32_t)index * mesh::storage::CONTACT_RECORD_SIZE)
          || file.read(record, sizeof(record)) != sizeof(record)) {
        MESH_DEBUG_PRINTLN("DataStore: contact page %u slot %u could not be read", page, index);
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
        file.close();
        return true;
      }
    }
    file.close();
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
  FILESYSTEM* contacts_fs = _getContactsChannelsFS();
  bool any_page_file = false;
  for (uint8_t page = 0; page < mesh::storage::CONTACT_PAGE_COUNT; page++) {
    char path[24];
    makeContactPagePath(page, path);
    if (contacts_fs->exists(path)) {
      any_page_file = true;
    }
  }
  const mesh::storage::ContactStoreSource source =
      mesh::storage::chooseContactStoreSource(
          contacts_fs->exists("/contacts3"), any_page_file);
  if (source == mesh::storage::ContactStoreSource::PAGED) {
    // A reset after the final legacy removal may leave this harmless marker.
    if (contacts_fs->exists(CONTACT_MIGRATION_MARKER)) {
      contacts_fs->remove(CONTACT_MIGRATION_MARKER);
    }
    loadContactPages(host, 0);
    return;
  }
  if (source == mesh::storage::ContactStoreSource::EMPTY) {
    if (contacts_fs->exists(CONTACT_MIGRATION_MARKER)) {
      contacts_fs->remove(CONTACT_MIGRATION_MARKER);
    }
    return;
  }

  // Migrate /contacts3 from the tail so the legacy prefix and completed pages
  // never need enough room to coexist in full.  A page is committed first,
  // then the corresponding legacy tail is truncated.  On a reset between
  // those operations the still-present legacy prefix wins overlapping slots.
  _legacy_contacts_pending_cleanup = true;
  _legacy_migration_ready = contacts_fs->exists(CONTACT_MIGRATION_MARKER);
#endif

  File file = openRead(_getContactsChannelsFS(), "/contacts3");
#if defined(NRF52_PLATFORM)
  if (!file) {
    // Never delete or truncate a legacy source that could not be opened.
    MESH_DEBUG_PRINTLN("DataStore: legacy contacts exist but could not be read");
    _legacy_contacts_pending_cleanup = false;
    return;
  }
  if (!mesh::storage::trustMigratedContactPages(
          true, _legacy_migration_ready)) {
    prepareLegacyContactMigration();
  }
#endif
  if (file) {
    bool full = false;
#if defined(NRF52_PLATFORM)
    _legacy_contact_count = mesh::storage::legacyContactCountForSize(file.size());
    uint16_t record_index = 0;
#endif
    while (!full
#if defined(NRF52_PLATFORM)
           && record_index < _legacy_contact_count
#endif
    ) {
      uint8_t record[mesh::storage::CONTACT_RECORD_SIZE];
      if (file.read(record, sizeof(record)) != sizeof(record)) break;

      ContactInfo contact;
      if (!deserializeContactRecord(record, contact)) {
        // Preserve the contact while containing corrupt legacy routing data.
        contact.out_path_len = OUT_PATH_UNKNOWN;
      }
#if defined(NRF52_PLATFORM)
      const uint16_t slot = record_index++;
      if (!_contact_slots.reserve(slot)) break;
      contact.storage_slot = slot;
#endif
      if (!host->onContactLoaded(contact)) {
        full = true;
#if defined(NRF52_PLATFORM)
        _contact_slots.release(slot);
#endif
      }
    }
    file.close();
  }

#if defined(NRF52_PLATFORM)
  // Pages at and beyond the remaining legacy prefix have already committed.
  // Loading both sources this way resumes safely after every possible reset
  // point, including a reset after page rename but before legacy truncation.
  if (_legacy_migration_ready) {
    loadContactPages(host, _legacy_contact_count);
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
  const uint16_t slot = contact.storage_slot;
  if (!_contact_slots.release(slot)) return false;
  contact.storage_slot = mesh::storage::CONTACT_SLOT_NONE;
  return _dirty_contact_pages.mark(slot / mesh::storage::CONTACTS_PER_PAGE);
#else
  (void)contact;
  return true;
#endif
}

#if defined(NRF52_PLATFORM)
bool DataStore::truncateLegacyContacts(uint16_t remaining_contacts) {
  FILESYSTEM* fs = _getContactsChannelsFS();
  if (remaining_contacts == 0) {
    const bool removed = !fs->exists("/contacts3") || fs->remove("/contacts3");
    if (removed) {
      if (fs->exists(CONTACT_MIGRATION_MARKER)) {
        fs->remove(CONTACT_MIGRATION_MARKER);
      }
      _legacy_contact_count = 0;
      _legacy_contacts_pending_cleanup = false;
      _legacy_migration_ready = false;
    }
    return removed;
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
  if (_legacy_contacts_pending_cleanup) {
    FILESYSTEM* fs = _getContactsChannelsFS();
    if (!fs->exists("/contacts3")) {
      if (fs->exists(CONTACT_MIGRATION_MARKER)) {
        fs->remove(CONTACT_MIGRATION_MARKER);
      }
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
  return !_dirty_contact_pages.empty() || _legacy_contacts_pending_cleanup;
#else
  return false;
#endif
}

void DataStore::loadChannels(DataStoreHost* host) {
    File file = openRead(_getContactsChannelsFS(), "/channels2");
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
}

void DataStore::saveChannels(DataStoreHost* host) {
#if defined(NRF52_PLATFORM)
  mesh::AtomicFileWriter file(_getContactsChannelsFS(), "/channels2");
#else
  File file = openWrite(_getContactsChannelsFS(), "/channels2");
#endif
  if (file) {
    uint8_t channel_idx = 0;
    ChannelDetails ch;
    uint8_t unused[4];
    memset(unused, 0, 4);

    bool success = true;
    while (success && host->getChannelForSave(channel_idx, ch)) {
      success = (file.write(unused, 4) == 4);
      success = success && (file.write((uint8_t *)ch.name, 32) == 32);
      success = success && (file.write((uint8_t *)ch.channel.secret, 32) == 32);

      if (!success) break; // write failed
      channel_idx++;
    }
#if defined(NRF52_PLATFORM)
    if (!file.commit(success)) MESH_DEBUG_PRINTLN("DataStore: atomic channels write failed");
#else
    file.close();
#endif
  }
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

void DataStore::migrateToSecondaryFS() {
  if (_fsExtra == nullptr) return;

  // Implemented below through verified copy transactions.  Source files are
  // removed only after the destination has been read back successfully.
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
    if (!left || !right || left.size() != right.size()) {
      if (left) left.close();
      if (right) right.close();
      return false;
    }
    uint8_t left_buf[64], right_buf[64];
    bool equal = true;
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
      }
    }
    left.close();
    right.close();
    return equal;
  };

  auto migrate = [this, &filesEqual](FILESYSTEM* source_fs, FILESYSTEM* dest_fs,
                                     const char* path) -> bool {
    if (!source_fs->exists(path)) return true;
    if (dest_fs->exists(path)) {
      if (filesEqual(source_fs, dest_fs, path)) {
        return source_fs->remove(path);
      }
      MESH_DEBUG_PRINTLN("DataStore: migration conflict for %s; preserving both copies", path);
      return false;
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
    return source_fs->remove(path);
  };

  for (size_t i = 0; i < sizeof(to_secondary) / sizeof(to_secondary[0]); i++) {
    migrate(_fs, _fsExtra, to_secondary[i]);
  }
  // Also move the bounded nRF v4 page/bucket files.  This matters after a boot
  // where external QSPI was unavailable and the store deliberately fell back
  // to internal flash.
#if defined(NRF52_PLATFORM)
  for (uint8_t page = 0; page < mesh::storage::CONTACT_PAGE_COUNT; page++) {
    char path[24];
    makeContactPagePath(page, path);
    migrate(_fs, _fsExtra, path);
  }
  for (uint8_t bucket = 0; bucket < 10; bucket++) {
    char path[20];
    snprintf(path, sizeof(path), "/adv4_%02u", (unsigned)bucket);
    migrate(_fs, _fsExtra, path);
  }
#endif
  for (size_t i = 0; i < sizeof(to_primary) / sizeof(to_primary[0]); i++) {
    migrate(_fsExtra, _fs, to_primary[i]);
  }
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
#endif

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
    MESH_DEBUG_PRINTLN("DataStore: could not retire legacy advert record");
    return false;
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
  if (!loadBlobBucket(_getContactsChannelsFS(), bucket, records)) {
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
  BlobRec& tombstone = records[selected];
  memset(&tombstone, 0, sizeof(tombstone));
  memcpy(tombstone.key, normalized, sizeof(tombstone.key));
  tombstone.timestamp = _clock->getCurrentTime();
  if (tombstone.timestamp == 0) tombstone.timestamp = 1;
  const bool saved = saveBlobBucket(_getContactsChannelsFS(), bucket, records);
  free(records);
  if (!saved) return false;
  if (!clearLegacyBlobRecord(_getContactsChannelsFS(), normalized)) {
    MESH_DEBUG_PRINTLN("DataStore: could not clear deleted legacy advert record");
    return false;
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
