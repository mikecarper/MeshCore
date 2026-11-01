#if defined(OTA_BOOTLOADER_MIGRATION)
#if !defined(NRF52_PLATFORM) || !defined(ENABLE_OTA)
#error Bootloader migration requires nRF52 OTA infrastructure firmware
#endif

#include "BootloaderMigration.h"

#include "BootloaderMigrationPolicy.h"
#include "BootloaderMigrationResource.h"
#include "OtaSelf.h"

#include <Arduino.h>
#include <SHA256.h>
#include <nrf.h>
#include <nrf_mbr.h>
#include <nrf_sdm.h>
#include <nrf_soc.h>
#include <stdio.h>
#include <string.h>

namespace mesh {
namespace ota {
static_assert(migration::ACL_READ_DENIED == ACL_ACL_PERM_READ_Msk, "ACL read permission bit");
static_assert(migration::ACL_WRITE_DENIED == ACL_ACL_PERM_WRITE_Msk, "ACL write permission bit");
namespace {
bool pending = false;
bool automatic_attempted = false;
bool automatic_blocked = false;
constexpr uint32_t AUTOMATIC_DELAY_MS = 70000u;
constexpr uint8_t ATTEMPT_MARKER = 0xD0;
uint32_t armed_at = 0;
uint8_t last_result = 0;
bool result_is_current = false;
bool already_installed = false;
FILESYSTEM *state_fs = nullptr;
uint8_t firmware_hash[32] = {};
MigrationAutoLock automatic_lock = MigrationAutoLock::Unavailable;
bool firmware_identified = false;
migration::InstalledVariant automatic_variant = migration::InstalledVariant::Unknown;
bool loader_excluded = false;

// The pinned framework's historical SVC wrapper has no memory clobber or IPA
// barrier. The MBR reads this pointed-to command after exception entry, which
// must remain visible to the compiler even under whole-program optimization.
#pragma GCC diagnostic push
#pragma GCC diagnostic ignored "-Wreturn-type"
__attribute__((naked, noipa)) uint32_t migration_mbr_command(sd_mbr_command_t *) {
  __asm volatile("svc %0\n"
                 "bx lr"
                 :
                 : "I"(SD_MBR_COMMAND)
                 : "r0", "memory");
}
#pragma GCC diagnostic pop

void hex8(const uint8_t *value, char output[17]) {
  static const char digits[] = "0123456789ABCDEF";
  for (unsigned i = 0; i < 8; ++i) {
    output[2 * i] = digits[value[i] >> 4];
    output[2 * i + 1] = digits[value[i] & 15];
  }
  output[16] = 0;
}

void hash_region(uint32_t address, uint32_t size, uint8_t output[32]) {
  SHA256 hash;
  hash.reset();
  uint8_t buffer[128];
  const volatile uint8_t *source = reinterpret_cast<const volatile uint8_t *>(address);
  while (size) {
    const uint32_t take = size < sizeof(buffer) ? size : sizeof(buffer);
    for (uint32_t i = 0; i < take; ++i)
      buffer[i] = source[i];
    hash.update(buffer, take);
    source += take;
    size -= take;
  }
  hash.finalize(output, 32);
}

bool installed_up_to_date() {
  OtaBootloaderIdentity installed;
  return ota_bootloader_identity_from_image(
             reinterpret_cast<const uint8_t *>(migration::BOOT_BASE), migration::BOOT_SIZE, installed) &&
         migration::installed_up_to_date(installed, migration_board_id, migration_device_name,
                                         migration_boot_version, mota_nrf52_app_base(),
                                         migration_softdevice_fwid);
}

bool qualified_legacy_hash(const uint8_t digest[32]) {
  return migration::legacy_digest_allowed(digest, migration_stock_sha256,
                                          migration_stock_count) ||
         (migration_has_legacy && memcmp(digest, migration_legacy_sha256, 32) == 0);
}

bool installed_allows_update() {
  const uint8_t *boot = reinterpret_cast<const uint8_t *>(migration::BOOT_BASE);
  OtaBootloaderIdentity installed;
  const bool valid = ota_bootloader_identity_from_image(boot, migration::BOOT_SIZE, installed);
  automatic_variant = migration::installed_variant(boot, migration::BOOT_SIZE,
                                                    valid ? installed.boot_version : 0);
  if (!valid) installed = OtaBootloaderIdentity();
  uint8_t digest[32] = {};
  if (!installed.present) hash_region(migration::BOOT_BASE, migration::BOOT_SIZE, digest);
  return migration::installed_source_allowed(installed, automatic_variant,
      !installed.present && qualified_legacy_hash(digest), migration_board_id,
      migration_device_name, mota_nrf52_app_base(), migration_softdevice_fwid);
}

bool lock_automatic_for_firmware() {
  if (!firmware_identified || !bootloader_migration_lock_auto(state_fs, firmware_hash)) {
    automatic_lock = MigrationAutoLock::Unavailable;
    return false;
  }
  automatic_lock = MigrationAutoLock::Locked;
  return true;
}

bool read_reboot_marker(uint32_t &result) {
  uint8_t enabled = 0;
  if (sd_softdevice_is_enabled(&enabled) != NRF_SUCCESS) return false;
  if (enabled) {
    if (sd_power_gpregret_get(0, &result) != NRF_SUCCESS) return false;
  } else {
    result = NRF_POWER->GPREGRET;
  }
  return true;
}

uint8_t retained_result() {
  if (result_is_current) return last_result;
  uint32_t result = 0;
  if (!read_reboot_marker(result)) return 0;
  return result >= 0xC0 && result <= 0xCF ? result & 15 : last_result;
}

bool write_reboot_marker(uint8_t marker) {
  uint8_t enabled = 0;
  if (sd_softdevice_is_enabled(&enabled) != NRF_SUCCESS) return false;
  if (enabled) {
    uint32_t current = 0;
    if (sd_power_gpregret_get(0, &current) != NRF_SUCCESS) return false;
    // Arming after the automatic image check must preserve its existing
    // guard, rather than creating an unguarded gap between clear and set.
    if (current == marker) return true;
    if (sd_power_gpregret_clr(0, 0xFF) != NRF_SUCCESS) return false;
    return marker == 0 || sd_power_gpregret_set(0, marker) == NRF_SUCCESS;
  }
  NRF_POWER->GPREGRET = marker;
  return NRF_POWER->GPREGRET == marker;
}

// Called only after the operator reply has drained and SoftDevice is stopped.
// GPREGRET (not GPREGRET2) survives this legacy loader's diagnostic writes.
void fail_and_reset(uint8_t result) {
  NRF_POWER->GPREGRET = 0xC0u | (result & 15u);
  NVIC_SystemReset();
  while (true) {
  }
}

void install() {
  SelfFwInfo app;
  if (!ota_self_firmware(app) || !app.valid) {
    pending = false;
    last_result = 1;
    return;
  }
  uint8_t enabled = 0;
  if (sd_softdevice_is_enabled(&enabled) != NRF_SUCCESS ||
      (enabled && sd_softdevice_disable() != NRF_SUCCESS)) {
    pending = false;
    last_result = 2;
    return;
  }

  // ACL is SoftDevice-owned while enabled. Inspect it only after disabling
  // SoftDevice; no ACL/MPU writes or attempts to defeat locked regions.
  migration::Snapshot snapshot = {};
  snapshot.part = NRF_FICR->INFO.PART;
  snapshot.flash_page_size = NRF_FICR->CODEPAGESIZE;
  snapshot.flash_pages = NRF_FICR->CODESIZE;
  for (unsigned i = 0; i < 8; ++i) {
    snapshot.acl[i] = { NRF_ACL->ACL[i].ADDR, NRF_ACL->ACL[i].SIZE, NRF_ACL->ACL[i].PERM };
    const auto &region = snapshot.acl[i];
    if ((region.permissions & migration::ACL_READ_DENIED) &&
        (migration::overlaps(region.address, region.size, 0, migration::APP_CEILING) ||
         migration::overlaps(region.address, region.size, migration::BOOT_BASE, migration::SETTINGS_BASE)))
      fail_and_reset(9);
  }
  snapshot.app_base = mota_nrf52_app_base();
  snapshot.app_ceiling = static_cast<uint32_t>(reinterpret_cast<uintptr_t>(__flash_arduino_end));
  snapshot.expected_softdevice_fwid = migration_softdevice_fwid;
  snapshot.softdevice_size = *reinterpret_cast<const volatile uint32_t *>(0x3008);
  snapshot.softdevice_fwid = *reinterpret_cast<const volatile uint16_t *>(0x300C);
  snapshot.mbr_boot_override = *reinterpret_cast<const volatile uint32_t *>(0xFF8);
  snapshot.mbr_params_override = *reinterpret_cast<const volatile uint32_t *>(0xFFC);
  snapshot.uicr_boot = NRF_UICR->NRFFW[0];
  snapshot.uicr_params = NRF_UICR->NRFFW[1];
  snapshot.source = static_cast<uint32_t>(reinterpret_cast<uintptr_t>(migration_candidate));
  snapshot.source_size = sizeof(migration_candidate);
  snapshot.app_body_size = app.body_len;
  snapshot.app_image_size = app.image_len;
  const auto gate = migration::preflight(snapshot);
  if (gate != migration::Gate::Ok) fail_and_reset(3 + static_cast<uint8_t>(gate));
  if (snapshot.app_base != migration_app_base) fail_and_reset(5);

  uint8_t digest[32];
  hash_region(snapshot.source, snapshot.source_size, digest);
  if (memcmp(digest, migration_candidate_sha256, sizeof(digest)) != 0) fail_and_reset(10);
  OtaBootloaderIdentity candidate;
  if (!ota_bootloader_vector_sane(migration_candidate) ||
      !ota_bootloader_candidate_identity_from_image(migration_candidate, migration::BOOT_SIZE, candidate) ||
      !migration::candidate_matches_resource(candidate, migration_board_id, migration_device_name,
                                            migration_boot_version, snapshot.app_base,
                                            snapshot.softdevice_fwid))
    fail_and_reset(10);
  hash_region(migration::BOOT_BASE, migration::BOOT_SIZE, digest);
  OtaBootloaderIdentity installed;
  const uint8_t *boot = reinterpret_cast<const uint8_t *>(migration::BOOT_BASE);
  const bool valid_identity = ota_bootloader_identity_from_image(boot, migration::BOOT_SIZE, installed);
  // A damaged or ambiguous modern manifest cannot masquerade as a legacy
  // loader. Only the explicitly pinned, byte-exact legacy image is eligible.
  if (!valid_identity) installed = OtaBootloaderIdentity();
  const bool legacy_matches = qualified_legacy_hash(digest);
  if (!migration::installed_source_allowed(installed,
          migration::installed_variant(boot, migration::BOOT_SIZE,
                                       valid_identity ? installed.boot_version : 0),
          legacy_matches, migration_board_id, migration_device_name,
          snapshot.app_base, snapshot.softdevice_fwid) ||
      !migration::installed_can_migrate(installed, candidate, true, legacy_matches,
                                       snapshot.app_base, snapshot.softdevice_fwid))
    fail_and_reset(12);

  // Do not start an uninterruptible MBR copy under an unexpectedly short WDT.
  if (NRF_WDT->RUNSTATUS && NRF_WDT->CRV < 5u * 32768u) fail_and_reset(13);
  if (NRF_WDT->RUNSTATUS) {
    for (unsigned i = 0; i < 8; ++i) {
      if (NRF_WDT->RREN & (1u << i)) NRF_WDT->RR[i] = 0x6E524635u;
    }
  }
  // The installed loader preserves our non-DFU marker. If COPY_BL or its
  // preflight is interrupted, the next application boot will stay available
  // instead of automatically trying the same operation again.
  NRF_POWER->GPREGRET = ATTEMPT_MARKER;
  NRF_POWER->GPREGRET2 = 0;
  sd_mbr_command_t command = {};
  command.command = SD_MBR_COMMAND_COPY_BL;
  command.params.copy_bl.bl_src = reinterpret_cast<uint32_t *>(snapshot.source);
  command.params.copy_bl.bl_len = migration::BOOT_SIZE / sizeof(uint32_t);
  // MBR owns the erase/copy and persistent recovery parameters. It never
  // returns on success. No application, filesystem or settings erase occurs.
  (void)migration_mbr_command(&command);
  fail_and_reset(14);
}
} // namespace

void bootloader_migration_begin(FILESYSTEM *fs) {
  state_fs = fs;
  firmware_identified = false;
  automatic_lock = MigrationAutoLock::Unavailable;
  SelfFwInfo app;
  if (fs && ota_self_firmware(app) && app.valid) {
    hash_region(mota_nrf52_app_base(), app.image_len, firmware_hash);
    firmware_identified = true;
    automatic_lock = bootloader_migration_auto_lock(fs, firmware_hash);
  }
  automatic_attempted = automatic_lock == MigrationAutoLock::Locked ||
                        automatic_lock == MigrationAutoLock::Unavailable;
  automatic_blocked = automatic_attempted;
}

bool bootloader_migration_pending() {
  return pending;
}

bool bootloader_migration_service_due() {
  return pending || (!automatic_attempted && millis() >= AUTOMATIC_DELAY_MS);
}

void bootloader_migration_status(char *reply, size_t capacity) {
  char hash[17];
  hex8(migration_candidate_sha256, hash);
  snprintf(reply, capacity, "BL embedded 2.4.12: 40960 B; hash=%s; %s; last=%u; auto=%s", hash,
           pending ? "armed" : already_installed ? "already installed" :
           installed_up_to_date() ? "up to date" : "idle", retained_result(),
           loader_excluded ? automatic_variant == migration::InstalledVariant::Recovery ?
               "recovery" : "unrecognized" :
           automatic_lock == MigrationAutoLock::Locked ? "locked for this firmware" :
           automatic_lock == MigrationAutoLock::Unavailable ? "storage unavailable" :
           automatic_blocked ? "blocked" : automatic_attempted ? "checked" : "70s after boot");
}

bool bootloader_migration_request(const char *confirmation, char *reply, size_t capacity) {
  char hash[17];
  hex8(migration_candidate_sha256, hash);
  if (confirmation && strcmp(confirmation, hash) != 0) {
    snprintf(reply, capacity, "ERR confirm exact image: ota bootloader embedded install %s", hash);
    return false;
  }
  // A manual attempt also satisfies the startup check, including a cancelled
  // or failed attempt. Only another explicit command may retry it this boot.
  automatic_attempted = true;
  automatic_blocked = false;
  if (pending) {
    snprintf(reply, capacity, "OK bootloader update already armed");
    return true;
  }
  if (installed_up_to_date()) {
    already_installed = false;
    last_result = 0;
    result_is_current = true;
    snprintf(reply, capacity, "OK bootloader already up to date");
    return true;
  }
  // Bootloader diagnostics already read this memory-mapped region while
  // SoftDevice runs. Detect a no-op before stopping it so BLE stays usable.
  uint8_t digest[32];
  hash_region(migration::BOOT_BASE, migration::BOOT_SIZE, digest);
  if (memcmp(digest, migration_candidate_sha256, sizeof(digest)) == 0) {
    already_installed = true;
    last_result = 0;
    result_is_current = true;
    snprintf(reply, capacity, "OK bootloader already installed");
    return true;
  }
  already_installed = false;
  if (!installed_allows_update()) {
    loader_excluded = true;
    last_result = 12;
    result_is_current = true;
    snprintf(reply, capacity, "ERR installed bootloader is not a reviewed exact compatible image");
    return false;
  }
  if (!lock_automatic_for_firmware()) {
    last_result = 18;
    result_is_current = true;
    snprintf(reply, capacity, "ERR cannot save bootloader automatic-update lock");
    return false;
  }
  if (!write_reboot_marker(ATTEMPT_MARKER)) {
    last_result = 17;
    result_is_current = true;
    snprintf(reply, capacity, "ERR bootloader reboot guard unavailable");
    return false;
  }
  pending = true;
  armed_at = millis();
  last_result = 0;
  result_is_current = true;
  snprintf(reply, capacity, "OK migration armed; checking exact old loader and protection after TX drains");
  return true;
}

void bootloader_migration_audit(char *reply, size_t capacity) {
  SelfFwInfo app;
  if (!ota_self_firmware(app) || !app.valid) {
    snprintf(reply, capacity, "ERR no valid application EndF");
    return;
  }
  uint8_t digest[32];
  char app_hash[17], settings_hash[17], fs_hash[17], sd_hash[17];
  hash_region(mota_nrf52_app_base(), app.image_len, digest);
  hex8(digest, app_hash);
  hash_region(migration::SETTINGS_BASE, 4096, digest);
  hex8(digest, settings_hash);
  const uint32_t fs_start = mota_nrf52_layout_stage_ceiling();
  hash_region(fs_start, migration::BOOT_BASE - fs_start, digest);
  hex8(digest, fs_hash);
  hash_region(0, mota_nrf52_app_base(), digest);
  hex8(digest, sd_hash);
  snprintf(reply, capacity, "app=%s settings=%s fs=%s sd=%s", app_hash, settings_hash, fs_hash, sd_hash);
}

void bootloader_migration_tick(bool outbound_idle, bool ota_idle) {
  if (!pending) {
    if (!bootloader_migration_service_due() || !outbound_idle || !ota_idle) return;
    // A failed MBR/preflight reset leaves this marker. Do not create a loop
    // that retries and resets every 70 seconds; the CLI can retry explicitly.
    uint32_t marker = 0;
    if (!read_reboot_marker(marker) ||
        (automatic_lock != MigrationAutoLock::PreviousImage &&
         (marker == ATTEMPT_MARKER || (marker > 0xC0 && marker <= 0xCF)))) {
      automatic_attempted = true;
      automatic_blocked = true;
      return;
    }
    automatic_attempted = true;
    // Mark before reading either image too, so a reset in the version/hash
    // check cannot turn into a recurring 70-second reboot loop.
    if (!write_reboot_marker(ATTEMPT_MARKER)) {
      last_result = 17;
      result_is_current = true;
      automatic_blocked = true;
      return;
    }
    // Save the current image before inspecting the bootloader. A new firmware
    // may ignore its predecessor's retained marker, so GPREGRET alone cannot
    // protect a reset during that first check.
    if (!lock_automatic_for_firmware()) {
      last_result = 18;
      result_is_current = true;
      automatic_blocked = true;
      return;
    }
    if (!installed_allows_update()) {
      loader_excluded = true;
      automatic_blocked = true;
      if (!write_reboot_marker(0)) {
        last_result = 17;
        result_is_current = true;
      }
      return;
    }
    char reply[160];
    bootloader_migration_request(nullptr, reply, sizeof(reply));
    if (!pending && last_result == 0 && !write_reboot_marker(0)) {
      last_result = 17;
      result_is_current = true;
    }
    return;
  }
  const uint32_t elapsed = millis() - armed_at;
  // An incoming advert may have started autofetch since the admin command.
  // Never disable SoftDevice while another OTA session owns the flash store.
  if (!ota_idle) {
    pending = false;
    last_result = 16;
  } else if (elapsed >= 30000u) {
    pending = false;
    last_result = 15;
  } else if (elapsed >= 1500u && outbound_idle) {
    install();
  }
}
} // namespace ota
} // namespace mesh
#endif
