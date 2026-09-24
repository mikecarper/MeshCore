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
#if defined(MESHCORE_MIGRATION_RESUME_OTA)
#include <mbedtls/sha256.h>
#ifndef MESHCORE_MIGRATION_EXPECTED_TARGET_ID
#error "LoRa migration requires the exact legacy OTA target ID"
#endif
#endif

#include <helpers/ESP32PartitionMigrationPolicy.h>
#include <helpers/esp32/WiFiRadioPolicy.h>

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
#if defined(MESHCORE_MIGRATION_RESUME_OTA)
constexpr char kMigrationResumeSlotKey[] = "resume-slot";
constexpr size_t kEndfBytes = 56;
#endif
static_assert(sizeof(kMigrationNvsNamespace) - 1 <= 15,
              "ESP32 NVS namespace names are limited to 15 characters");

AsyncWebServer server(80);
bool migration_started = false;
bool migration_complete = false;
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
  migration_nvs.remove(kMigrationIdentityKey);
  const bool cleared = migration_nvs.remove(kMigrationIdentityPendingKey);
  migration_nvs.end();
  if (!cleared) {
    strcpy(status_text, "Private key restored; NVS cleanup needs retry");
    return false;
  }
  return true;
}

#if defined(MESHCORE_MIGRATION_RESUME_OTA)
uint32_t readLe32(const uint8_t* bytes) {
  return static_cast<uint32_t>(bytes[0])
      | (static_cast<uint32_t>(bytes[1]) << 8)
      | (static_cast<uint32_t>(bytes[2]) << 16)
      | (static_cast<uint32_t>(bytes[3]) << 24);
}

// The preserved app must really be the LoRa OTA receiver for this exact
// package. A valid EndF is emitted only for OTA-enabled MeshCore builds, and
// its body hash prevents a stale marker from authorizing a damaged image.
bool validLegacyOtaReceiver(const esp_partition_t& source) {
  uint8_t header = 0;
  if (esp_partition_read(&source, 0, &header, 1) != ESP_OK || header != 0xE9) {
    strcpy(status_text, "Refused: old LoRa application image is invalid");
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
          || readLe32(trailer + 4) != marker
          || readLe32(trailer + 20) != MESHCORE_MIGRATION_EXPECTED_TARGET_ID) {
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
      if (valid) return true;
      strcpy(status_text, "Refused: old LoRa firmware EndF hash is invalid");
      return false;
    }
  }
  strcpy(status_text, "Refused: old LoRa firmware has no matching valid EndF");
  return false;
}

bool stageResumeSlot(uint8_t slot) {
  Preferences migration_nvs;
  const bool opened = migration_nvs.begin(kMigrationNvsNamespace, false);
  const bool saved = opened && migration_nvs.putUChar(kMigrationResumeSlotKey, slot) == 1;
  if (opened) migration_nvs.end();
  if (!saved) strcpy(status_text, "Could not stage old LoRa application slot");
  return saved;
}

bool resumeLegacyOtaReceiver() {
  Preferences migration_nvs;
  if (!migration_nvs.begin(kMigrationNvsNamespace, false)) {
    strcpy(status_text, "Could not read old LoRa application slot");
    return false;
  }
  const uint8_t slot = migration_nvs.getUChar(kMigrationResumeSlotKey, 0xFF);
  migration_nvs.end();
  if (slot > 1) {
    strcpy(status_text, "Expanded layout ready; no LoRa resume record");
    return false;
  }
  const esp_partition_t* old_app = esp_partition_find_first(
      ESP_PARTITION_TYPE_APP,
      slot == 0 ? ESP_PARTITION_SUBTYPE_APP_OTA_0 : ESP_PARTITION_SUBTYPE_APP_OTA_1,
      nullptr);
  const esp_err_t selected = old_app
      ? esp_ota_set_boot_partition(old_app) : ESP_ERR_NOT_FOUND;
  if (selected != ESP_OK) {
    snprintf(status_text, sizeof(status_text), "Could not resume old LoRa app: %s",
             errName(selected));
    return false;
  }
  if (migration_nvs.begin(kMigrationNvsNamespace, false)) {
    migration_nvs.remove(kMigrationResumeSlotKey);
    migration_nvs.end();
  }
  strcpy(status_text, "Private key restored; resuming old LoRa firmware");
  reboot_at = millis() + 1000;
  return true;
}
#endif

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
    strcpy(status_text, "Refused: legacy slots cannot preserve LoRa receiver");
    Serial.println(status_text);
    return;
  }
  const esp_partition_t* old_receiver = resume.resume_slot == 0
      ? refs.app0 : refs.app1;
  if (!validLegacyOtaReceiver(*old_receiver)) {
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

  // The Wi-Fi bridge always runs from target app0. The LoRa bridge instead
  // keeps the old application in the other expanded slot, restores identity,
  // then boots that old application to receive the final image by LoRa.
#if defined(MESHCORE_MIGRATION_RESUME_OTA)
  Serial.println("Migration: preserving legacy app1 in expanded app1");
  if (!copyAndVerify(*refs.app1, plan->layout.app1_address,
                     refs.app1->size, "future-app1")
      || !stageResumeSlot(resume.resume_slot)) {
    Serial.println(status_text);
    return;
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

  // The OTA-select data records a slot identity. Select the bridge's currently
  // registered legacy slot; after the table change, that slot identity points
  // at the verified bridge copy under the expanded layout.
#if defined(MESHCORE_MIGRATION_RESUME_OTA)
  const bool bridge_in_app0 = resume.bridge_slot == 0;
#else
  const bool bridge_in_app0 = true;
#endif
  const esp_err_t select_result = esp_ota_set_boot_partition(
      bridge_in_app0 ? refs.app0 : refs.app1);
  if (select_result != ESP_OK) {
    snprintf(status_text, sizeof(status_text), "Could not select bridge slot: %s",
             errName(select_result));
    Serial.println(status_text);
    return;
  }
  Serial.println("Migration: bridge slot selected for restart");

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
    strcpy(status_text, "Migration complete; restarting LoRa bridge");
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
  page += "<h2>MeshCore Wi-Fi partition migration</h2><p>";
  page += mode;
  page += "</p>";
  if (strstr(status_text, "ready") != nullptr) {
    page += "<p>The expanded partition layout is active and the private device "
            "identity was staged and restored before this uploader was exposed.</p>"
            "<p><a href='/update'>Upload the full application image for this board.</a>.</p>";
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
  AsyncElegantOTA.begin(&server);
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
    if (restoreStagedIdentity()) {
      strcpy(status_text, "Expanded layout ready");
#if defined(MESHCORE_MIGRATION_RESUME_OTA)
      resumeLegacyOtaReceiver();
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
