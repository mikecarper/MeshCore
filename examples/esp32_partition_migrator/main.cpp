#include <Arduino.h>
#include <WiFi.h>
#include <AsyncTCP.h>
#include <ESPAsyncWebServer.h>
#include <AsyncElegantOTA.h>
#include <Preferences.h>
#include <SPIFFS.h>
#include <esp_ota_ops.h>
#include <esp_attr.h>
#include <esp_flash.h>
#include <esp_partition.h>
#include <esp_heap_caps.h>
#if defined(MESHCORE_MIGRATION_RESUME_OTA) || defined(MOTA_MIGRATION_TARGET_ID)
#include <mbedtls/sha256.h>
#include <nvs.h>
#endif

#include <helpers/ESP32PartitionMigrationPolicy.h>
#include <helpers/esp32/WiFiRadioPolicy.h>

#if defined(MOTA_MIGRATION_TARGET_ID)
String partitionExpanderStatus();
#endif

namespace migration = mesh::esp32_partition_migration;

namespace {

constexpr char kApSsid[] = "MeshCore-Migrate";
constexpr char kApPassword[] = "meshcore-migrate";
#ifndef MESHCORE_MIGRATION_DELAY_MS
#define MESHCORE_MIGRATION_DELAY_MS 4500
#endif
#ifndef MESHCORE_MIGRATION_RESTART_DELAY_MS
#define MESHCORE_MIGRATION_RESTART_DELAY_MS 250
#endif
constexpr uint32_t kMigrationDelayMs = MESHCORE_MIGRATION_DELAY_MS;
constexpr uint32_t kMigrationRestartDelayMs = MESHCORE_MIGRATION_RESTART_DELAY_MS;
constexpr size_t kCopyBufferBytes = 4096;
constexpr size_t kIdentityFileBytes = 96;  // public key (32) followed by private key (64)
constexpr char kMigrationNvsNamespace[] = "mesh-pt-migrate";
constexpr char kMigrationIdentityKey[] = "identity";
// NVS key names are limited to 15 characters.
constexpr char kMigrationIdentityPendingKey[] = "id-pending";
constexpr char kMigrationConfigKey[] = "cfg-meta";
constexpr char kMigrationConfigPendingKey[] = "cfg-pending";
constexpr char kMigrationConfigRestoredKey[] = "cfg-restored";
#if defined(MOTA_MIGRATION_TARGET_ID)
constexpr char kExpanderHandoffKey[] = "full-handoff";
#endif
constexpr uint32_t kConfigStageMagic = 0x43464731U;
constexpr size_t kMaxConfigFileBytes = 16384;
// The common preferences hold node name, password and primary radio settings.
// Other entries preserve access policy, extra radio profiles and the credentials
// needed to remain manageable after the SPIFFS partition moves.
struct ConfigFile { const char* path; const char* nvs_key; };
constexpr ConfigFile kConfigFiles[] = {
    {"/com_prefs", "cfg00"}, {"/node_prefs", "cfg01"},
    {"/s_contacts", "cfg02"}, {"/s_login_replay", "cfg03"},
    {"/regions2", "cfg04"}, {"/radio_profiles", "cfg05"},
    {"/mqtt_prefs", "cfg06"}, {"/mqtt.json", "cfg07"},
    {"/ota_config", "cfg08"}, {"/flood_filter", "cfg09"},
    {"/flood_filter_bl", "cfg10"}, {"/flood_ch_scope", "cfg11"},
    {"/flood_ch_req", "cfg12"}, {"/flood_grp_mod", "cfg13"},
    {"/clock_sync", "cfg14"}, {"/display_prefs", "cfg15"},
    {"/telemetry_tx", "cfg16"}, {"/data_tx", "cfg17"},
    {"/com_prefs.bak", "cfg18"}, {"/radio_profiles.bak", "cfg19"},
    {"/s_contacts.bak", "cfg20"}, {"/mqtt_prefs.bak", "cfg21"},
    {"/regions2.bak", "cfg22"}, {"/prefs.json", "cfg23"},
    {"/management", "cfg24"}, {"/management.bak", "cfg25"},
    {"/s_login_replay.bak", "cfg26"}, {"/ota_config.bak", "cfg27"},
    {"/ota_speed", "cfg28"}, {"/flood_ch_block", "cfg29"},
    {"/bsec_state.bin", "cfg30"}, {"/display_prefs.bak", "cfg31"},
};
constexpr size_t kConfigFileCount = sizeof(kConfigFiles) / sizeof(kConfigFiles[0]);
static_assert(kConfigFileCount <= 32, "config presence mask is 32 bits");
struct ConfigStage {
  uint32_t magic;
  uint32_t present;
  uint32_t sizes[kConfigFileCount];
  uint32_t crcs[kConfigFileCount];
};
#if defined(MESHCORE_MIGRATION_RESUME_OTA)
constexpr char kMigrationResumeSlotKey[] = "resume-slot";
#endif
#if defined(MESHCORE_MIGRATION_RESUME_OTA) || defined(MOTA_MIGRATION_TARGET_ID)
constexpr size_t kEndfBytes = 56;
constexpr char kBridgeImageMarker[] = "MeshCore ESP32 partition migration bridge image";
// Earlier bridge packages predate the dedicated marker but contain this page title.
constexpr char kLegacyBridgeImageMarker[] = "MeshCore Wi-Fi partition migration";
#endif
static_assert(sizeof(kMigrationNvsNamespace) - 1 <= 15,
              "ESP32 NVS namespace names are limited to 15 characters");

AsyncWebServer server(80);
bool migration_started = false;
bool migration_complete = false;
bool expanded_layout_ready = false;
bool identity_ready = false;
bool reboot_requested = false;
uint32_t migration_at = 0;
uint32_t reboot_at = 0;
char status_text[160] = "Starting";

// The Arduino loop task can use external RAM on an ESP32-S3.  Flash
// erase/write disables the external-memory cache, so every buffer passed to a
// flash operation must be explicitly placed in internal DRAM.
DRAM_ATTR uint8_t copy_buffer[kCopyBufferBytes];
DRAM_ATTR uint8_t partition_table_bytes[
    migration::kExpandedPartitionTablePrefixBytes];
DRAM_ATTR uint8_t partition_table_verified[
    migration::kExpandedPartitionTablePrefixBytes];
// Keep the temporary flash hooks in DRAM.  The normal ESP-IDF hooks keep the
// two CPU cores and their flash caches safe while an erase/write is running;
// only the address-validation callback is narrowed for this one deliberately
// dangerous sector.  esp_flash_os_functions_t is documented for advanced
// callers which need to replace individual hooks.
DRAM_ATTR esp_flash_os_functions_t partition_table_flash_hooks;
using RegionProtectedFn = esp_err_t (*)(void*, size_t, size_t);
DRAM_ATTR RegionProtectedFn original_region_protected = nullptr;
DRAM_ATTR void* original_flash_hook_data = nullptr;
// esp_partition_write() reads the descriptor while the flash cache is off.
// Keep the synthetic copy destination out of a task stack that may be
// allocated in PSRAM on ESP32-S3 builds.
DRAM_ATTR esp_partition_t copy_destination;

struct PartitionRefs {
  const esp_partition_t* nvs = nullptr;
  const esp_partition_t* otadata = nullptr;
  const esp_partition_t* app0 = nullptr;
  const esp_partition_t* app1 = nullptr;
  const esp_partition_t* spiffs = nullptr;
};

uint32_t crc32(const uint8_t* data, size_t size, uint32_t crc = 0xFFFFFFFFU) {
  for (size_t i = 0; i < size; ++i) {
    crc ^= data[i];
    for (unsigned bit = 0; bit < 8; ++bit) {
      crc = (crc >> 1) ^ ((crc & 1U) ? 0xEDB88320U : 0U);
    }
  }
  return crc;
}

// This callback is invoked before ESP-IDF enters its cache-off flash critical
// section.  It permits only the partition-table sector, and delegates every
// other address to ESP-IDF's original protection policy.
esp_err_t IRAM_ATTR partitionTableRegionProtected(void*, size_t address, size_t size) {
  const size_t table_start = migration::kPartitionTableAddress;
  const size_t table_end = table_start + migration::kPartitionTableBytes;
  if (address >= table_start && size <= table_end - address) {
    return ESP_OK;
  }
  if (!original_region_protected) return ESP_ERR_INVALID_STATE;
  return original_region_protected(original_flash_hook_data, address, size);
}

const char* errName(esp_err_t err) {
  const char* name = esp_err_to_name(err);
  return name ? name : "unknown ESP error";
}

bool findPartitions(PartitionRefs& refs, migration::PartitionGeometry& geometry) {
  refs.nvs = esp_partition_find_first(ESP_PARTITION_TYPE_DATA,
      ESP_PARTITION_SUBTYPE_DATA_NVS, nullptr);
  refs.otadata = esp_partition_find_first(ESP_PARTITION_TYPE_DATA,
      ESP_PARTITION_SUBTYPE_DATA_OTA, nullptr);
  refs.app0 = esp_partition_find_first(ESP_PARTITION_TYPE_APP,
      ESP_PARTITION_SUBTYPE_APP_OTA_0, nullptr);
  refs.app1 = esp_partition_find_first(ESP_PARTITION_TYPE_APP,
      ESP_PARTITION_SUBTYPE_APP_OTA_1, nullptr);
  refs.spiffs = esp_partition_find_first(ESP_PARTITION_TYPE_DATA,
      ESP_PARTITION_SUBTYPE_DATA_SPIFFS, nullptr);
  if (!refs.nvs || !refs.otadata || !refs.app0 || !refs.app1 || !refs.spiffs) {
    return false;
  }
  geometry = {
      refs.nvs->address, refs.nvs->size,
      refs.otadata->address, refs.otadata->size,
      refs.app0->address, refs.app0->size,
      refs.app1->address, refs.app1->size,
      refs.spiffs->address, refs.spiffs->size,
  };
  return true;
}

// esp_partition_* validates only the geometry in the supplied descriptor.  A
// descriptor for the future data region is therefore enough to write it while
// the old partition table is still active.  This avoids touching old SPIFFS
// until the target table is published.
esp_partition_t rawPartition(uint32_t address, uint32_t size, const char* label) {
  esp_partition_t part = {};
  part.type = ESP_PARTITION_TYPE_DATA;
  part.subtype = ESP_PARTITION_SUBTYPE_DATA_UNDEFINED;
  part.address = address;
  part.size = size;
  strncpy(part.label, label, sizeof(part.label) - 1);
  part.label[sizeof(part.label) - 1] = 0;
  part.encrypted = false;
  return part;
}

bool rangesOverlap(uint32_t first_address, uint32_t first_size,
                   uint32_t second_address, uint32_t second_size) {
  const uint64_t first_end = static_cast<uint64_t>(first_address) + first_size;
  const uint64_t second_end = static_cast<uint64_t>(second_address) + second_size;
  return first_address < second_end && second_address < first_end;
}

bool eraseRaw(const esp_partition_t& destination) {
  for (uint32_t offset = 0; offset < destination.size;) {
    uint32_t span = destination.size - offset;
    // A large flash erase can run long enough to trip the task watchdog on an
    // S3. Erase one sector at a time and explicitly yield between operations.
    // This is slower but keeps the board alive while its future app slot is
    // being prepared.
    if (span > migration::kSectorBytes) span = migration::kSectorBytes;
    const esp_err_t result = esp_partition_erase_range(&destination, offset, span);
    if (result != ESP_OK) {
      snprintf(status_text, sizeof(status_text), "Erase failed at 0x%lx: %s",
               (unsigned long)(destination.address + offset), errName(result));
      return false;
    }
    offset += span;
    delay(1);
  }
  return true;
}

bool copyAndVerify(const esp_partition_t& source, uint32_t destination_address,
                   uint32_t bytes, const char* destination_label) {
  if (bytes == 0 || bytes > source.size || bytes % migration::kSectorBytes != 0) {
    strcpy(status_text, "Invalid migration copy geometry");
    return false;
  }
  copy_destination = rawPartition(destination_address, bytes, destination_label);
  if (!eraseRaw(copy_destination)) return false;

  uint32_t source_crc = 0xFFFFFFFFU;
  for (uint32_t offset = 0; offset < bytes; offset += sizeof(copy_buffer)) {
    esp_err_t result = esp_partition_read(&source, offset, copy_buffer,
                                          sizeof(copy_buffer));
    if (result != ESP_OK) {
      snprintf(status_text, sizeof(status_text), "Read failed at 0x%lx: %s",
               (unsigned long)(source.address + offset), errName(result));
      return false;
    }
    source_crc = crc32(copy_buffer, sizeof(copy_buffer), source_crc);
    result = esp_partition_write(&copy_destination, offset, copy_buffer,
                                 sizeof(copy_buffer));
    if (result != ESP_OK) {
      snprintf(status_text, sizeof(status_text), "Write failed at 0x%lx: %s",
               (unsigned long)(copy_destination.address + offset), errName(result));
      return false;
    }
    delay(1);
  }

  uint32_t destination_crc = 0xFFFFFFFFU;
  for (uint32_t offset = 0; offset < bytes; offset += sizeof(copy_buffer)) {
    const esp_err_t result = esp_partition_read(&copy_destination, offset, copy_buffer,
                                                sizeof(copy_buffer));
    if (result != ESP_OK) {
      snprintf(status_text, sizeof(status_text), "Verify read failed at 0x%lx: %s",
               (unsigned long)(copy_destination.address + offset), errName(result));
      return false;
    }
    destination_crc = crc32(copy_buffer, sizeof(copy_buffer), destination_crc);
    delay(1);
  }
  source_crc = ~source_crc;
  destination_crc = ~destination_crc;
  if (source_crc != destination_crc) {
    snprintf(status_text, sizeof(status_text),
             "CRC mismatch while copying %s (%08lx != %08lx)", destination_label,
             (unsigned long)source_crc, (unsigned long)destination_crc);
    return false;
  }
  return true;
}

// The larger destination SPIFFS partition is allowed to reformat itself on
// its first mount.  A raw SPIFFS image is not reliably expandable, so stage
// the one irreplaceable file in NVS first.  NVS stays at the same address in
// both layouts.  The source file format is exactly the historical
// IdentityStore layout: 32 public-key bytes followed by 64 private-key bytes.
bool stageLegacyIdentity() {
  // A previous attempt may have reached the future app1 copy before power
  // failed. That copy can overlap old SPIFFS on 8/16 MiB boards. Never read
  // a regenerated or damaged legacy file over the already staged old key.
  Preferences prior_stage;
  if (prior_stage.begin(kMigrationNvsNamespace, true)) {
    const bool pending = prior_stage.getBool(kMigrationIdentityPendingKey, false);
    const size_t saved = pending
        ? prior_stage.getBytes(kMigrationIdentityKey, copy_buffer,
                               kIdentityFileBytes) : 0;
    prior_stage.end();
    if (pending) {
      if (saved == kIdentityFileBytes) return true;
      strcpy(status_text, "Refused: previously staged private key is incomplete");
      return false;
    }
  }
  if (!SPIFFS.begin(false)) {
    strcpy(status_text, "Could not mount legacy SPIFFS to save identity");
    return false;
  }
  File identity = SPIFFS.open("/identity/_main.id", "r");
  const bool read_ok = identity && identity.size() >= kIdentityFileBytes
      && identity.read(copy_buffer, kIdentityFileBytes) == kIdentityFileBytes;
  if (identity) identity.close();
  SPIFFS.end();
  if (!read_ok) {
    strcpy(status_text, "Refused: legacy private-key file is unavailable");
    return false;
  }

  Preferences migration_nvs;
  if (!migration_nvs.begin(kMigrationNvsNamespace, false)) {
    strcpy(status_text, "Could not open NVS identity staging");
    return false;
  }
  const bool saved = migration_nvs.putBytes(kMigrationIdentityKey, copy_buffer,
                                             kIdentityFileBytes) == kIdentityFileBytes
      && migration_nvs.putBool(kMigrationIdentityPendingKey, true);
  migration_nvs.end();
  if (!saved) strcpy(status_text, "Could not save private key for migration");
  return saved;
}

bool restoreStagedIdentity() {
  Preferences migration_nvs;
  if (!migration_nvs.begin(kMigrationNvsNamespace, false)) {
    strcpy(status_text, "Could not open NVS identity recovery");
    return false;
  }
  const bool pending = migration_nvs.getBool(kMigrationIdentityPendingKey, false);
  const size_t stored_bytes = pending
      ? migration_nvs.getBytes(kMigrationIdentityKey, copy_buffer, sizeof(copy_buffer))
      : 0;
  migration_nvs.end();
  if (!pending) return true;
  if (stored_bytes != kIdentityFileBytes) {
    strcpy(status_text, "Refused: staged private key is incomplete");
    return false;
  }

  // `true` intentionally formats only if the raw legacy SPIFFS image cannot
  // mount at the expanded size. All settings except the identity are allowed
  // to be recreated; the staged identity is immediately written back below.
  if (!SPIFFS.begin(true)) {
    strcpy(status_text, "Could not initialize expanded SPIFFS");
    return false;
  }
  if (!SPIFFS.exists("/identity") && !SPIFFS.mkdir("/identity")) {
    SPIFFS.end();
    strcpy(status_text, "Could not create identity folder");
    return false;
  }
  File identity = SPIFFS.open("/identity/_main.id", "w");
  const bool wrote = identity && identity.write(copy_buffer, kIdentityFileBytes)
      == kIdentityFileBytes;
  if (identity) {
    identity.flush();
    identity.close();
  }
  File verify = SPIFFS.open("/identity/_main.id", "r");
  const bool verified = wrote && verify && verify.size() >= kIdentityFileBytes
      && verify.read(partition_table_verified, kIdentityFileBytes) == kIdentityFileBytes
      && memcmp(copy_buffer, partition_table_verified, kIdentityFileBytes) == 0;
  if (verify) verify.close();
  SPIFFS.end();
  if (!verified) {
    strcpy(status_text, "Private-key restore verification failed");
    return false;
  }

  if (!migration_nvs.begin(kMigrationNvsNamespace, false)) {
    strcpy(status_text, "Could not finalize NVS identity recovery");
    return false;
  }
  const bool cleared = migration_nvs.remove(kMigrationIdentityPendingKey);
  if (cleared) migration_nvs.remove(kMigrationIdentityKey);
  migration_nvs.end();
  if (!cleared) {
    strcpy(status_text, "Private key restored; NVS cleanup needs retry");
    return false;
  }
  return true;
}

#if !defined(MESHCORE_MIGRATION_RESUME_OTA)
bool verifyExpandedIdentityFile() {
  if (!SPIFFS.begin(false)) {
    strcpy(status_text, "Refused: expanded identity filesystem is unavailable");
    return false;
  }
  File identity = SPIFFS.open("/identity/_main.id", "r");
  const bool available = identity && identity.size() >= kIdentityFileBytes
      && identity.read(copy_buffer, kIdentityFileBytes) == kIdentityFileBytes;
  if (identity) identity.close();
  SPIFFS.end();
  if (!available) strcpy(status_text, "Refused: restored private key is unavailable");
  return available;
}
#endif

bool readConfigStage(Preferences& nvs, ConfigStage& stage) {
  if (nvs.getBytes(kMigrationConfigKey, &stage, sizeof(stage)) != sizeof(stage)
      || stage.magic != kConfigStageMagic) {
    strcpy(status_text, "Refused: staged configuration manifest is incomplete");
    return false;
  }
  for (size_t i = 0; i < kConfigFileCount; ++i) {
    const bool present = (stage.present & (1UL << i)) != 0;
    if ((!present && stage.sizes[i] != 0)
        || (present && (stage.sizes[i] == 0
                        || stage.sizes[i] > kMaxConfigFileBytes
                        || nvs.getBytesLength(kConfigFiles[i].nvs_key) != stage.sizes[i]))) {
      snprintf(status_text, sizeof(status_text),
               "Refused: staged %s is incomplete", kConfigFiles[i].path);
      return false;
    }
  }
  return true;
}

bool verifyStagedConfigBlob(Preferences& nvs, const ConfigStage& stage,
                            size_t i, uint8_t* buffer) {
  const size_t size = stage.sizes[i];
  return nvs.getBytes(kConfigFiles[i].nvs_key, buffer, size) == size
      && (~crc32(buffer, size)) == stage.crcs[i];
}

void clearConfigStageBlobs(Preferences& nvs) {
  nvs.remove(kMigrationConfigKey);
  for (const ConfigFile& file : kConfigFiles) nvs.remove(file.nvs_key);
}

bool validateStagedConfig() {
  Preferences nvs;
  if (!nvs.begin(kMigrationNvsNamespace, true)) return true;
  const bool pending = nvs.getBool(kMigrationConfigPendingKey, false);
  if (!pending) { nvs.end(); return true; }
  ConfigStage stage = {};
  bool valid = readConfigStage(nvs, stage);
  for (size_t i = 0; valid && i < kConfigFileCount; ++i) {
    if (!(stage.present & (1UL << i))) continue;
    uint8_t* buffer = static_cast<uint8_t*>(heap_caps_malloc(
        stage.sizes[i], MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT));
    valid = buffer && verifyStagedConfigBlob(nvs, stage, i, buffer);
    heap_caps_free(buffer);
    if (!valid) snprintf(status_text, sizeof(status_text),
                         "Refused: staged %s failed verification", kConfigFiles[i].path);
  }
  nvs.end();
  return valid;
}

bool stageLegacyConfig() {
  Preferences nvs;
  if (!nvs.begin(kMigrationNvsNamespace, false)) {
    strcpy(status_text, "Could not open NVS configuration staging");
    return false;
  }
  if (nvs.getBool(kMigrationConfigPendingKey, false)) {
    nvs.end();
    return validateStagedConfig();
  }
  // A power failure before the commit marker can leave partial blobs. They
  // are never authoritative and would otherwise consume the small NVS area.
  if (!nvs.putBool(kMigrationConfigRestoredKey, false)) {
    nvs.end();
    strcpy(status_text, "Could not reset configuration handoff record");
    return false;
  }
  clearConfigStageBlobs(nvs);
  if (!SPIFFS.begin(false)) {
    nvs.end();
    strcpy(status_text, "Could not mount legacy configuration filesystem");
    return false;
  }
  ConfigStage stage = {};
  stage.magic = kConfigStageMagic;
  bool saved = true;
  for (size_t i = 0; saved && i < kConfigFileCount; ++i) {
    if (!SPIFFS.exists(kConfigFiles[i].path)) continue;
    File source = SPIFFS.open(kConfigFiles[i].path, "r");
    const size_t size = source ? source.size() : 0;
    if (size == 0 || size > kMaxConfigFileBytes) {
      snprintf(status_text, sizeof(status_text),
               "Refused: %s cannot fit NVS staging", kConfigFiles[i].path);
      saved = false;
    } else {
      uint8_t* buffer = static_cast<uint8_t*>(heap_caps_malloc(
          size, MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT));
      saved = buffer && source.read(buffer, size) == size;
      if (saved) {
        stage.sizes[i] = size;
        stage.crcs[i] = ~crc32(buffer, size);
        saved = nvs.putBytes(kConfigFiles[i].nvs_key, buffer, size) == size
            && verifyStagedConfigBlob(nvs, stage, i, buffer);
      }
      heap_caps_free(buffer);
      if (!saved) snprintf(status_text, sizeof(status_text),
                           "Could not stage and verify %s", kConfigFiles[i].path);
    }
    if (source) source.close();
    if (saved) stage.present |= (1UL << i);
    delay(1);
  }
  SPIFFS.end();
  if (saved) {
    saved = nvs.putBytes(kMigrationConfigKey, &stage, sizeof(stage))
                 == sizeof(stage)
        && nvs.putBool(kMigrationConfigPendingKey, true);
    if (!saved) strcpy(status_text, "Could not finish NVS configuration staging");
  }
  if (!saved) clearConfigStageBlobs(nvs);
  nvs.end();
  return saved && validateStagedConfig();
}

bool restoreStagedConfig() {
  Preferences nvs;
  if (!nvs.begin(kMigrationNvsNamespace, false)) return true;
  if (!nvs.getBool(kMigrationConfigPendingKey, false)) {
    // Cleanup may have been interrupted after the restored files were
    // committed. This does not require another SPIFFS write.
    clearConfigStageBlobs(nvs);
    nvs.end();
    return true;
  }
  ConfigStage stage = {};
  if (!readConfigStage(nvs, stage) || !validateStagedConfig()) {
    nvs.end();
    return false;
  }
  if (!SPIFFS.begin(false)) {
    nvs.end();
    strcpy(status_text, "Could not mount expanded configuration filesystem");
    return false;
  }
  bool restored = true;
  for (size_t i = 0; restored && i < kConfigFileCount; ++i) {
    if (!(stage.present & (1UL << i))) continue;
    const size_t size = stage.sizes[i];
    uint8_t* buffer = static_cast<uint8_t*>(heap_caps_malloc(
        size, MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT));
    restored = buffer && verifyStagedConfigBlob(nvs, stage, i, buffer);
    if (restored) {
      File destination = SPIFFS.open(kConfigFiles[i].path, "w");
      restored = destination && destination.write(buffer, size) == size;
      if (destination) { destination.flush(); destination.close(); }
      File verify = SPIFFS.open(kConfigFiles[i].path, "r");
      restored = restored && verify && verify.size() == size
          && verify.read(buffer, size) == size
          && (~crc32(buffer, size)) == stage.crcs[i];
      if (verify) verify.close();
    }
    heap_caps_free(buffer);
    if (!restored) snprintf(status_text, sizeof(status_text),
                            "Could not restore and verify %s", kConfigFiles[i].path);
    delay(1);
  }
  SPIFFS.end();
  if (restored) {
    // Record the verified restore before clearing the pending marker. A lost
    // pending flag cannot otherwise prove that saved settings survived.
    restored = nvs.putBool(kMigrationConfigRestoredKey, true);
    if (restored) restored = nvs.remove(kMigrationConfigPendingKey);
    if (restored) {
      clearConfigStageBlobs(nvs);
    } else {
      strcpy(status_text, "Configuration restored; NVS commit needs retry");
    }
  }
  nvs.end();
  return restored;
}

#if defined(MESHCORE_MIGRATION_RESUME_OTA) || defined(MOTA_MIGRATION_TARGET_ID)
uint32_t readLe32(const uint8_t* bytes) {
  return static_cast<uint32_t>(bytes[0])
      | (static_cast<uint32_t>(bytes[1]) << 8)
      | (static_cast<uint32_t>(bytes[2]) << 16)
      | (static_cast<uint32_t>(bytes[3]) << 24);
}

struct OtaImageIdentity {
  uint32_t target_id = 0;
  uint32_t body_bytes = 0;
  uint8_t hardware_id[32] = {};
};

// The package appends an exact-target EndF to this bridge. Read and verify
// it from the running image, then require the opposite-slot firmware to have
// that same target and hardware identity. A generic chip-family bridge is
// therefore reusable without accepting a cross-board LoRa handoff.
bool readVerifiedOtaIdentity(const esp_partition_t& source,
                             OtaImageIdentity& identity) {
  uint8_t header = 0;
  if (esp_partition_read(&source, 0, &header, 1) != ESP_OK || header != 0xE9) {
    strcpy(status_text, "Refused: LoRa application image is invalid");
    return false;
  }
  uint8_t trailer[kEndfBytes];
  for (uint32_t base = 0; base < source.size;
       base += sizeof(copy_buffer)) {
    const uint32_t count = source.size - base < sizeof(copy_buffer)
        ? source.size - base : sizeof(copy_buffer);
    if (esp_partition_read(&source, base, copy_buffer, count) != ESP_OK) break;
    for (uint32_t index = 0; index < count; ++index) {
      if (copy_buffer[index] != 'E') continue;
      const uint32_t marker = base + index;
      if (marker + kEndfBytes > source.size
          || esp_partition_read(&source, marker, trailer, sizeof(trailer)) != ESP_OK
          || memcmp(trailer, "EndF", 4) != 0
          || readLe32(trailer + 4) != marker) {
        continue;
      }
      mbedtls_sha256_context hash;
      mbedtls_sha256_init(&hash);
      bool valid = mbedtls_sha256_starts_ret(&hash, 0) == 0;
      for (uint32_t offset = 0; valid && offset < marker;) {
        const uint32_t length = marker - offset < sizeof(copy_buffer)
            ? marker - offset : sizeof(copy_buffer);
        valid = esp_partition_read(&source, offset, copy_buffer, length) == ESP_OK
            && mbedtls_sha256_update_ret(&hash, copy_buffer, length) == 0;
        offset += length;
      }
      uint8_t digest[32];
      valid = valid && mbedtls_sha256_finish_ret(&hash, digest) == 0
          && memcmp(digest, trailer + 8, 8) == 0;
      mbedtls_sha256_free(&hash);
      if (valid) {
        identity.target_id = readLe32(trailer + 20);
        identity.body_bytes = marker;
        memcpy(identity.hardware_id, trailer + 24, sizeof(identity.hardware_id));
        if (identity.target_id != 0) return true;
        strcpy(status_text, "Refused: LoRa firmware EndF has no target ID");
        return false;
      }
      strcpy(status_text, "Refused: LoRa firmware EndF hash is invalid");
      return false;
    }
  }
  strcpy(status_text, "Refused: LoRa firmware has no valid EndF");
  return false;
}

enum class BridgeMarkerResult { Missing, Present, ReadFailure };

BridgeMarkerResult containsBridgeMarker(const esp_partition_t& image,
                                        uint32_t body_bytes) {
  constexpr size_t marker_bytes = sizeof(kBridgeImageMarker) - 1;
  constexpr size_t legacy_marker_bytes = sizeof(kLegacyBridgeImageMarker) - 1;
  size_t matched = 0;
  size_t matched_legacy = 0;
  for (uint32_t offset = 0; offset < body_bytes;) {
    const uint32_t count = body_bytes - offset < sizeof(copy_buffer)
        ? body_bytes - offset : sizeof(copy_buffer);
    if (esp_partition_read(&image, offset, copy_buffer, count) != ESP_OK) {
      strcpy(status_text, "Refused: could not inspect other LoRa image");
      return BridgeMarkerResult::ReadFailure;
    }
    for (uint32_t index = 0; index < count; ++index) {
      matched = copy_buffer[index] == kBridgeImageMarker[matched]
          ? matched + 1
          : (copy_buffer[index] == kBridgeImageMarker[0] ? 1 : 0);
      matched_legacy = copy_buffer[index] == kLegacyBridgeImageMarker[matched_legacy]
          ? matched_legacy + 1
          : (copy_buffer[index] == kLegacyBridgeImageMarker[0] ? 1 : 0);
      if (matched == marker_bytes || matched_legacy == legacy_marker_bytes)
        return BridgeMarkerResult::Present;
    }
    offset += count;
    delay(1);
  }
  return BridgeMarkerResult::Missing;
}

bool validOtherLoRaFirmware(const esp_partition_t& bridge,
                            const esp_partition_t& receiver) {
  OtaImageIdentity bridge_identity;
  OtaImageIdentity receiver_identity;
  if (!readVerifiedOtaIdentity(bridge, bridge_identity)
      || !readVerifiedOtaIdentity(receiver, receiver_identity)) return false;
  if (bridge_identity.target_id != receiver_identity.target_id
      || memcmp(bridge_identity.hardware_id, receiver_identity.hardware_id,
                sizeof(bridge_identity.hardware_id)) != 0) {
    strcpy(status_text, "Refused: other LoRa firmware target does not match bridge");
    return false;
  }
  const BridgeMarkerResult marker = containsBridgeMarker(
      receiver, receiver_identity.body_bytes);
  if (marker != BridgeMarkerResult::Missing) {
    if (marker == BridgeMarkerResult::Present)
      strcpy(status_text, "Refused: other LoRa slot contains a migration bridge");
    return false;
  }
  return true;
}

#if defined(MOTA_MIGRATION_TARGET_ID)
bool stageExpanderHandoff() {
  Preferences nvs;
  if (!nvs.begin(kMigrationNvsNamespace, false)) {
    strcpy(status_text, "Could not open Partition Expander handoff record");
    return false;
  }
  const bool saved = nvs.putUChar(kExpanderHandoffKey, 0xA5) == 1;
  nvs.end();
  if (!saved) strcpy(status_text, "Could not save Partition Expander handoff record");
  return saved;
}

bool hasExpanderHandoff() {
  Preferences nvs;
  if (!nvs.begin(kMigrationNvsNamespace, true)) return false;
  const bool pending = nvs.getUChar(kExpanderHandoffKey, 0) == 0xA5;
  nvs.end();
  return pending;
}

bool hasVerifiedConfigHandoff() {
  Preferences nvs;
  if (!nvs.begin(kMigrationNvsNamespace, true)) return false;
  const bool restored = nvs.getBool(kMigrationConfigRestoredKey, false);
  nvs.end();
  return restored;
}

bool returnToVerifiedFull() {
  const esp_partition_t* running = esp_ota_get_running_partition();
  const esp_partition_t* other = esp_ota_get_next_update_partition(nullptr);
  if (!running || !other || running->address == other->address) return false;
  OtaImageIdentity bridge;
  OtaImageIdentity full;
  if (!readVerifiedOtaIdentity(*running, bridge)
      || !readVerifiedOtaIdentity(*other, full)
      || full.target_id != (uint32_t)MOTA_MIGRATION_TARGET_ID
      || memcmp(full.hardware_id, bridge.hardware_id,
                sizeof(full.hardware_id)) != 0
      || containsBridgeMarker(*other, full.body_bytes)
             != BridgeMarkerResult::Missing) return false;
  const esp_err_t selected = esp_ota_set_boot_partition(other);
  if (selected != ESP_OK) {
    snprintf(status_text, sizeof(status_text),
             "Could not return to verified Full image: %s", errName(selected));
    return false;
  }
  strcpy(status_text, "Already expanded; returning to verified Full image");
  reboot_at = millis() + 1000;
  return true;
}
#endif

#if defined(MESHCORE_MIGRATION_RESUME_OTA)

bool stageResumeSlot(uint8_t slot) {
  Preferences migration_nvs;
  const bool opened = migration_nvs.begin(kMigrationNvsNamespace, false);
  const bool saved = opened && migration_nvs.putUChar(kMigrationResumeSlotKey, slot) == 1;
  if (opened) migration_nvs.end();
  if (!saved) strcpy(status_text, "Could not stage old LoRa application slot");
  return saved;
}

bool resumeLegacyOtaReceiver(const migration::PartitionGeometry& geometry) {
  uint8_t slot = 0xFF;
  nvs_handle_t nvs_handle;
  const esp_err_t opened = nvs_open(kMigrationNvsNamespace, NVS_READONLY, &nvs_handle);
  if (opened == ESP_OK) {
    const esp_err_t read = nvs_get_u8(nvs_handle, kMigrationResumeSlotKey, &slot);
    nvs_close(nvs_handle);
    if (read != ESP_OK && read != ESP_ERR_NVS_NOT_FOUND) {
      snprintf(status_text, sizeof(status_text), "Refused: LoRa handoff NVS read failed: %s",
               errName(read));
      return false;
    }
  } else if (opened != ESP_ERR_NVS_NOT_FOUND) {
    snprintf(status_text, sizeof(status_text), "Refused: LoRa handoff NVS open failed: %s",
             errName(opened));
    return false;
  }
  const esp_partition_t* running = esp_ota_get_running_partition();
  const migration::LoRaHandoffPlan handoff = migration::planLoRaHandoff(
      geometry, running ? running->address : 0, slot);
  if (!handoff.valid) {
    strcpy(status_text, "Refused: LoRa handoff record or running slot is unsafe");
    return false;
  }
  const esp_partition_t* other_app = esp_partition_find_first(
      ESP_PARTITION_TYPE_APP,
      handoff.other_slot == 0 ? ESP_PARTITION_SUBTYPE_APP_OTA_0
                              : ESP_PARTITION_SUBTYPE_APP_OTA_1,
      nullptr);
  if (!other_app || !running || other_app->address == running->address
      || !validOtherLoRaFirmware(*running, *other_app)) {
    if (!other_app || !running || other_app->address == running->address)
      strcpy(status_text, "Refused: other LoRa application slot is unavailable");
    return false;
  }
  const esp_err_t selected = esp_ota_set_boot_partition(other_app);
  if (selected != ESP_OK) {
    snprintf(status_text, sizeof(status_text), "Could not boot verified LoRa app: %s",
             errName(selected));
    return false;
  }
  if (handoff.has_record) {
    Preferences migration_nvs;
    if (migration_nvs.begin(kMigrationNvsNamespace, false)) {
      migration_nvs.remove(kMigrationResumeSlotKey);
      migration_nvs.end();
    }
  }
  strcpy(status_text, "Expanded layout ready; returning to verified LoRa firmware");
  reboot_at = millis() + 1000;
  return true;
}
#endif
#endif  // MESHCORE_MIGRATION_RESUME_OTA || MOTA_MIGRATION_TARGET_ID

bool publishExpandedPartitionTable(const migration::TargetPlan& plan) {
  // Preserve ESP-IDF's normal OS flash hooks.  In particular, their start/end
  // hooks suspend the other core and safely disable/re-enable caches.  Do not
  // call esp_flash_app_disable_protect(): that internal coredump-only helper
  // removes those hooks entirely and is unsafe from a running application.
  esp_flash_t* const chip = esp_flash_default_chip;
  const esp_flash_os_functions_t* const original_hooks = chip ? chip->os_func : nullptr;
  if (!chip || !original_hooks || !original_hooks->region_protected) {
    strcpy(status_text, "ESP-IDF flash protection hooks are unavailable");
    return false;
  }

  original_region_protected = original_hooks->region_protected;
  original_flash_hook_data = chip->os_func_data;
  partition_table_flash_hooks = *original_hooks;
  partition_table_flash_hooks.region_protected = partitionTableRegionProtected;
  chip->os_func = &partition_table_flash_hooks;

  bool ok = false;
  // esp_partition_write disables the flash cache.  The generated prefix is
  // otherwise stored in DROM, so copy it to explicitly internal DRAM first.
  memcpy(partition_table_bytes, plan.partition_table_prefix,
         plan.partition_table_prefix_bytes);
  // Do not use esp_partition_write for the table itself. Once the old sector
  // is erased, a partition-manager path must not be allowed to consult that
  // erased metadata. esp_flash_* operates on the main flash chip directly.
  esp_err_t result = esp_flash_erase_region(chip,
                                  migration::kPartitionTableAddress,
                                  migration::kPartitionTableBytes);
  if (result == ESP_OK) {
    result = esp_flash_write(chip, partition_table_bytes,
                             migration::kPartitionTableAddress,
                             plan.partition_table_prefix_bytes);
    if (result == ESP_OK) {
      result = esp_flash_read(chip, partition_table_verified,
                              migration::kPartitionTableAddress,
                              plan.partition_table_prefix_bytes);
      ok = result == ESP_OK && memcmp(partition_table_verified,
          plan.partition_table_prefix, plan.partition_table_prefix_bytes) == 0;
      if (!ok) strcpy(status_text, "Partition table verification failed");
    } else {
      snprintf(status_text, sizeof(status_text), "Partition table write failed: %s",
               errName(result));
    }
  } else {
    snprintf(status_text, sizeof(status_text), "Partition table erase failed: %s",
             errName(result));
  }
  chip->os_func = original_hooks;
  original_region_protected = nullptr;
  original_flash_hook_data = nullptr;
  return ok;
}

void runMigration() {
  migration_started = true;
  PartitionRefs refs;
  migration::PartitionGeometry geometry = {};
  const uint32_t flash_bytes = ESP.getFlashChipSize();
  const migration::TargetPlan* const plan = migration::targetForFlash(flash_bytes);
  if (!plan || !findPartitions(refs, geometry)
      || !migration::canMigrateGeneric(flash_bytes, geometry)) {
    snprintf(status_text, sizeof(status_text),
             "Refused: unsupported layout or flash size (%lu bytes)",
             (unsigned long)flash_bytes);
    Serial.println(status_text);
    return;
  }

  const esp_partition_t* running = esp_ota_get_running_partition();
  // Partition handles are opaque; their addresses, rather than their pointer
  // identities, determine the slot.  This keeps the A/B decision correct if
  // ESP-IDF returns a distinct descriptor for the currently-running image.
  if (!running || (running->address != refs.app0->address &&
                   running->address != refs.app1->address)) {
    strcpy(status_text, "Refused: migration image is not in a legacy OTA slot");
    Serial.println(status_text);
    return;
  }

#if defined(MESHCORE_MIGRATION_RESUME_OTA)
  const migration::LoRaResumePlan resume = migration::planLoRaResume(
      geometry, plan->layout, running->address);
  if (!resume.valid) {
    strcpy(status_text, "Refused: LoRa bridge/receiver slot layout is unsafe");
    Serial.println(status_text);
    return;
  }
  const esp_partition_t* old_receiver = resume.resume_slot == 0
      ? refs.app0 : refs.app1;
  if (!validOtherLoRaFirmware(*running, *old_receiver)) {
    Serial.println(status_text);
    return;
  }
#endif

  Serial.println("Migration: staging private key; do not interrupt power");
  if (!stageLegacyIdentity()) {
    Serial.println(status_text);
    return;
  }
  Serial.println("Migration: private key safely staged in NVS");
#if !defined(MESHCORE_MIGRATION_RESUME_OTA)
  if (!stageLegacyConfig()) {
    Serial.println(status_text);
    return;
  }
  Serial.println("Migration: ACL, radio profiles and node configuration safely staged in NVS");
#if defined(MOTA_MIGRATION_TARGET_ID)
  if (!stageExpanderHandoff()) {
    Serial.println(status_text);
    return;
  }
#endif
#endif

  // The Wi-Fi bridge always runs from target app0. The LoRa bridge instead
  // keeps the old application in the other expanded slot, restores identity,
  // then boots that old application to receive the final image by LoRa.
#if defined(MESHCORE_MIGRATION_RESUME_OTA)
  if (resume.copy_app1) {
    Serial.println("Migration: preserving legacy app1 in expanded app1");
    if (!copyAndVerify(*refs.app1, plan->layout.app1_address,
                       refs.app1->size, "future-app1")
        || !stageResumeSlot(resume.resume_slot)) {
      Serial.println(status_text);
      return;
    }
  } else {
    Serial.println("Migration: old receiver remains in app0; Full firmware will restore the key");
  }
#else
  // Never erase a running source range while copying it.
  if (running->address != plan->layout.app0_address) {
    if (running->size > plan->layout.app0_size) {
      strcpy(status_text, "Refused: migration slot is larger than future app0");
      Serial.println(status_text);
      return;
    }
    if (rangesOverlap(running->address, running->size,
                      plan->layout.app0_address, running->size)) {
      strcpy(status_text, "Refused: target app0 overlaps the running image");
      Serial.println(status_text);
      return;
    }
    Serial.println("Migration: placing Wi-Fi bridge image in app0");
    if (!copyAndVerify(*running, plan->layout.app0_address,
                       running->size, "future-app0")) {
      Serial.println(status_text);
      return;
    }
    Serial.println("Migration: bridge image copied to app0");
  }
#endif

  // The OTA-select data records a slot identity. On 8/16 MiB LoRa and Wi-Fi
  // paths it selects the bridge; the 4 MiB LoRa path selects old receiver A
  // because old bridge B cannot survive in either expanded slot.
#if defined(MESHCORE_MIGRATION_RESUME_OTA)
  const bool boot_in_app0 = resume.copy_app1
      ? resume.bridge_slot == 0 : resume.resume_slot == 0;
#else
  const bool boot_in_app0 = true;
#endif
  const esp_err_t select_result = esp_ota_set_boot_partition(
      boot_in_app0 ? refs.app0 : refs.app1);
  if (select_result != ESP_OK) {
    snprintf(status_text, sizeof(status_text), "Could not select migration boot slot: %s",
             errName(select_result));
    Serial.println(status_text);
    return;
  }
  Serial.println("Migration: boot slot selected for restart");

  Serial.println("Migration: publishing expanded partition table");
  // This bridge is built without native USB CDC, so a connected USB host cannot
  // post a flash-backed event while the partition-sector operation disables the
  // flash cache. Stop UART0 as well: the S3's serial event path is otherwise
  // still able to interrupt the raw flash operation. The bridge has no native
  // USB CDC; the final normal repeater build restores its standard USB behavior.
  Serial.flush();
  Serial.end();
  const bool table_published = publishExpandedPartitionTable(*plan);
  Serial.begin(115200);
  delay(50);
  if (!table_published) {
    Serial.println(status_text);
    return;
  }

  migration_complete = true;
  if (kMigrationRestartDelayMs == 0) {
    strcpy(status_text, "Partition table verified; waiting for test reboot");
  } else {
#if defined(MESHCORE_MIGRATION_RESUME_OTA)
    strcpy(status_text, resume.copy_app1
        ? "Migration complete; restarting LoRa bridge"
        : "Migration complete; resuming old LoRa receiver");
#else
    strcpy(status_text, "Migration complete; restarting Wi-Fi uploader");
#endif
    reboot_at = millis() + kMigrationRestartDelayMs;
  }
  Serial.println(status_text);
}

void sendHome(AsyncWebServerRequest* request) {
  const char* mode = status_text;
  String page;
  page.reserve(1000);
  page += "<!doctype html><meta name=viewport content='width=device-width,initial-scale=1'>";
#if defined(MESHCORE_MIGRATION_RESUME_OTA)
  page += "<!--";
  page += kBridgeImageMarker;
  page += "-->";
#endif
  page += "<h2>MeshCore Wi-Fi partition migration</h2><p>";
  page += mode;
  page += "</p>";
#if defined(MOTA_MIGRATION_TARGET_ID)
  if (expanded_layout_ready && identity_ready) {
    page += "<p>";
    page += partitionExpanderStatus();
    page += "</p>";
  }
#endif
  if (expanded_layout_ready && identity_ready) {
    page += "<p>The expanded partition layout is active. If the bridge could "
            "not return to a verified LoRa image, Wi-Fi recovery is available "
            "here: <a href='/update'>upload the correct Full application</a>. "
            "Check the status above before proceeding.</p>";
  } else if (expanded_layout_ready) {
    page += "<p>Private identity recovery did not complete. Firmware upload is "
            "disabled; restart the bridge and inspect the status before retrying.</p>";
  } else if (migration_complete) {
    page += "<p>Partition-table bytes were read back successfully. The test harness "
            "is waiting for an explicit reboot.</p><p><a href='/reboot'>Restart now</a></p>";
  } else if (!migration_started) {
    page += "<p>Copying private data and replacing the partition table starts shortly. "
            "Keep USB power connected; this page will disappear while the board restarts.</p>";
  } else {
    page += "<p>Keep power connected. Refresh after two minutes if the board did not restart.</p>";
  }
  request->send(200, "text/html", page);
}

void startServer() {
  // A legacy ESP-NOW repeater can leave the AP protocol mask in proprietary
  // LR mode.  SoftAP then reports success but ordinary phones and laptops
  // cannot discover it.  Reuse the normal WebConfig recipe: AP+STA mode,
  // an explicit interoperable protocol mask, and the project AP channel.
  WiFi.mode(WIFI_AP_STA);
  WiFi.setAutoReconnect(false);
  WiFi.disconnect(false, true);
  delay(100);
  WiFi.setSleep(false);
  const IPAddress address(192, 168, 4, 1);
  const IPAddress netmask(255, 255, 255, 0);
  if (!WiFi.softAPConfig(address, address, netmask)
      || !WiFi.softAP(kApSsid, kApPassword, mesh::wifi::accessPointChannel())
      || mesh::wifi::applyAccessPointProtocolMask() != ESP_OK
      || esp_wifi_set_protocol(WIFI_IF_STA, mesh::wifi::kProtocolMask) != ESP_OK
      || esp_wifi_set_max_tx_power(78) != ESP_OK) {
    strcpy(status_text, "Wi-Fi AP failed; restart the board and retry");
    return;
  }
  wifi_config_t ap_config = {};
  int8_t max_tx_power = 0;
  const esp_err_t config_result = esp_wifi_get_config(WIFI_IF_AP, &ap_config);
  const esp_err_t power_result = esp_wifi_get_max_tx_power(&max_tx_power);
  Serial.printf("Wi-Fi AP active: %s at %s (channel %u, hidden %u, power %.2f dBm, config %d, power %d)\n",
                WiFi.softAPSSID().c_str(), WiFi.softAPIP().toString().c_str(),
                (unsigned)ap_config.ap.channel, (unsigned)ap_config.ap.ssid_hidden,
                max_tx_power / 4.0, (int)config_result, (int)power_result);
  server.on("/", HTTP_GET, sendHome);
  server.on("/reboot", HTTP_GET, [](AsyncWebServerRequest* request) {
    if (!migration_complete) {
      request->send(409, "text/plain", "Migration has not completed");
      return;
    }
    request->send(200, "text/plain", "Restarting migration bridge");
    reboot_requested = true;
  });
  if (!expanded_layout_ready || identity_ready) AsyncElegantOTA.begin(&server);
  server.begin();
}

}  // namespace

void setup() {
  Serial.begin(115200);
  delay(250);

  PartitionRefs refs;
  migration::PartitionGeometry geometry = {};
  const uint32_t flash_bytes = ESP.getFlashChipSize();
  if (findPartitions(refs, geometry)
      && migration::isTargetLayout(flash_bytes, geometry)) {
    expanded_layout_ready = true;
    if (validateStagedConfig() && restoreStagedIdentity()
#if !defined(MESHCORE_MIGRATION_RESUME_OTA)
        && restoreStagedConfig()
        && verifyExpandedIdentityFile()
#endif
        ) {
      identity_ready = true;
      strcpy(status_text, "Expanded layout ready");
#if defined(MESHCORE_MIGRATION_RESUME_OTA)
      resumeLegacyOtaReceiver(geometry);
#endif
    }
  } else if (findPartitions(refs, geometry)
             && migration::canMigrateGeneric(flash_bytes, geometry)) {
    strcpy(status_text, "Legacy layout verified; migration begins shortly");
    migration_at = millis() + kMigrationDelayMs;
  } else {
    strcpy(status_text, "Refused: unsupported legacy source layout");
  }

  startServer();
  Serial.printf("%s. Join %s (password: %s), then open http://192.168.4.1/\n",
                status_text, kApSsid, kApPassword);
}

void loop() {
  if (!migration_started && migration_at != 0
      && static_cast<int32_t>(millis() - migration_at) >= 0) {
    runMigration();
  }
  if ((reboot_at != 0 && static_cast<int32_t>(millis() - reboot_at) >= 0)
      || reboot_requested) {
    delay(100);
    ESP.restart();
  }
  delay(10);
}
