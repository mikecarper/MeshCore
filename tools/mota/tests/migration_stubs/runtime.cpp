#include "Arduino.h"
#include "BootloaderMigrationResource.h"
#include "SHA256.h"
// The test app-base override bypasses the target linker's declarations.
namespace mesh { namespace ota {
extern "C" uint32_t __flash_arduino_end[];
extern "C" const uint8_t g_meshcore_internal_extrafs __attribute__((weak));
} }
#include "helpers/ota/OtaSelf.h"
#include "nrf.h"
#include "nrf_mbr.h"
#include "nrf_sdm.h"
#include "nrf_soc.h"

#include <cassert>
#include <cstdint>
#include <cstdlib>
#include <cstring>
#include <stdexcept>
#if defined(_WIN32)
#include <windows.h>
#else
#include <sys/mman.h>
#endif

namespace migration_test {
uint32_t now = 0;
unsigned endf_checks = 0;
unsigned softdevice_checks = 0;
unsigned disables = 0;
unsigned resets = 0;
unsigned copies = 0;
unsigned retained_reads = 0;
unsigned retained_clears = 0;
unsigned retained_sets = 0;
bool candidate_installed = false;
bool stock_installed = false;
bool stock_hash_damaged = false;
bool interrupt_hash = false;
bool interrupt_classification = false;
unsigned classification_checks = 0;
bool softdevice_enabled = false;
bool startup_metadata = false;
bool startup_valid = true;
bool store_available = true;
bool store_write_ok = true;
bool durable_lock_present = false;
uint8_t application_hash[32] = {1};
uint8_t durable_lock_hash[32] = {};
unsigned store_reads = 0;
unsigned store_writes = 0;
unsigned store_lock_calls = 0;
uint32_t softdevice_status = NRF_SUCCESS;
uint32_t retained_get_status = NRF_SUCCESS;
uint32_t retained_clear_status = NRF_SUCCESS;
uint32_t retained_set_status = NRF_SUCCESS;

void mbr_command() {
  ++copies;
  throw std::runtime_error("unexpected MBR command in state-machine test");
}
} // namespace migration_test

#include "helpers/ota/BootloaderMigrationPolicy.h"
namespace mesh { namespace ota { namespace migration {
InstalledVariant installed_variant_for_runtime(const uint8_t *image, size_t size,
                                                uint32_t version) {
  ++migration_test::classification_checks;
  if (migration_test::interrupt_classification)
    throw std::runtime_error("interrupted bootloader classification");
  return installed_variant(image, size, version);
}
} } }

// Include the actual driver. Only its target-specific naked SVC adapter is
// replaced: function-like 'volatile(...)' captures the extended assembly, but
// leaves ordinary 'volatile uint8_t *' memory reads unchanged. The adapter is
// an explicit failing trap, never a successful simulated flash operation.
// A classification boundary permits a reset injection while still using the
// real classifier for every completed check.
#define __attribute__(...)
#define __asm
#define volatile(...) migration_test::mbr_command()
#define installed_variant installed_variant_for_runtime
#include "helpers/ota/BootloaderMigration.cpp"
#undef installed_variant
#undef volatile
#undef __asm
#undef __attribute__

const uint8_t migration_candidate[40960] = {};
const uint8_t migration_candidate_sha256[32] = { 0x01, 0x23, 0x45, 0x67, 0x89, 0xAB, 0xCD, 0xEF };
const uint8_t migration_legacy_sha256[32] = {};
// The real registry's stock RAK 0.4.3 image identity; the SHA stub below
// controls whether that digest is observed, not the cryptographic primitive.
const uint8_t migration_stock_sha256[1][32] = {{
  0x6c,0xbf,0xa5,0x69,0xa0,0x5c,0xbd,0x23,0x2d,0xab,0xe4,0xa6,0x54,0xab,0xe9,0xc0,
  0x4b,0x7a,0x03,0xab,0x45,0x76,0x15,0x02,0x8d,0x28,0xf2,0xd1,0x32,0x9e,0x58,0xd2}};
const uint32_t migration_stock_count = 1;
const bool migration_has_legacy = false;
const uint32_t migration_app_base = 0x26000, migration_boot_version = 0x02040CFF,
               migration_board_id = 0x239A0029;
const uint16_t migration_softdevice_fwid = 0xB6;
const char migration_device_name[16] = "4631_DFU";
namespace mesh { namespace ota {
extern "C" {
uint32_t __flash_arduino_start[1] = {};
uint32_t __flash_arduino_end[1] = {};
}
} }

MigrationTestPower power_registers = {};
MigrationTestPower *NRF_POWER = &power_registers;
MigrationTestFicr *NRF_FICR = nullptr;
MigrationTestAcl *NRF_ACL = nullptr;
MigrationTestUicr *NRF_UICR = nullptr;
MigrationTestWdt *NRF_WDT = nullptr;

uint32_t millis() {
  return migration_test::now;
}

void NVIC_SystemReset() {
  ++migration_test::resets;
  throw std::runtime_error("unexpected reset in state-machine test");
}

uint32_t sd_softdevice_is_enabled(uint8_t *enabled) {
  ++migration_test::softdevice_checks;
  *enabled = migration_test::softdevice_enabled;
  return migration_test::softdevice_status;
}

uint32_t sd_softdevice_disable() {
  ++migration_test::disables;
  throw std::runtime_error("unexpected SoftDevice disable in state-machine test");
}

uint32_t sd_power_gpregret_get(uint32_t index, uint32_t *result) {
  assert(index == 0);
  ++migration_test::retained_reads;
  *result = power_registers.GPREGRET;
  return migration_test::retained_get_status;
}

uint32_t sd_power_gpregret_clr(uint32_t index, uint32_t mask) {
  assert(index == 0 && mask == 0xFF);
  assert(!mesh::ota::bootloader_migration_pending());
  ++migration_test::retained_clears;
  if (migration_test::retained_clear_status == NRF_SUCCESS)
    power_registers.GPREGRET &= ~mask;
  return migration_test::retained_clear_status;
}

uint32_t sd_power_gpregret_set(uint32_t index, uint32_t mask) {
  assert(index == 0 && mask == mesh::ota::ATTEMPT_MARKER);
  assert(!mesh::ota::bootloader_migration_pending());
  ++migration_test::retained_sets;
  if (migration_test::retained_set_status == NRF_SUCCESS)
    power_registers.GPREGRET |= mask;
  return migration_test::retained_set_status;
}

void SHA256::reset() {
  if (migration_test::interrupt_hash)
    throw std::runtime_error("interrupted bootloader check");
}
void SHA256::update(const void *, size_t) {
}
void SHA256::finalize(void *out, size_t size) {
  assert(size == 32);
  // Scheduling tests exercise the full memory-read loop; cryptography itself
  // is the production SHA256 library's contract, not this hardware stub.
  std::memset(out, 0, size);
  if (migration_test::startup_metadata)
    std::memcpy(out, migration_test::application_hash, size);
  else if (migration_test::candidate_installed)
    std::memcpy(out, migration_candidate_sha256, size);
  else if (migration_test::stock_installed) {
    std::memcpy(out, migration_stock_sha256[0], size);
    if (migration_test::stock_hash_damaged) static_cast<uint8_t *>(out)[31] ^= 1;
  }
}

namespace mesh {
namespace ota {
bool ota_self_firmware(SelfFwInfo &out) {
  ++migration_test::endf_checks;
  if (migration_test::startup_metadata && migration_test::startup_valid) {
    out.valid = true;
    out.body_len = 256 - ENDF_LEN;
    out.image_len = 256;
    return true;
  }
  return false; // Stop at the first install preflight, before hardware access.
}

MigrationAutoLock bootloader_migration_auto_lock(FILESYSTEM *fs, const uint8_t hash[32]) {
  ++migration_test::store_reads;
  assert(std::memcmp(hash, migration_test::application_hash, 32) == 0);
  if (!fs || !migration_test::store_available) return MigrationAutoLock::Unavailable;
  if (!migration_test::durable_lock_present) return MigrationAutoLock::Eligible;
  return std::memcmp(hash, migration_test::durable_lock_hash, 32) == 0
      ? MigrationAutoLock::Locked : MigrationAutoLock::PreviousImage;
}

bool bootloader_migration_lock_auto(FILESYSTEM *fs, const uint8_t hash[32]) {
  ++migration_test::store_lock_calls;
  assert(!bootloader_migration_pending());
  assert(std::memcmp(hash, migration_test::application_hash, 32) == 0);
  if (!fs || !migration_test::store_available) return false;
  if (migration_test::durable_lock_present &&
      std::memcmp(migration_test::durable_lock_hash, hash, 32) == 0) return true;
  if (!migration_test::store_write_ok) return false;
  ++migration_test::store_writes;
  std::memcpy(migration_test::durable_lock_hash, hash, 32);
  migration_test::durable_lock_present = true;
  return true;
}
} // namespace ota
} // namespace mesh

namespace {
using namespace mesh::ota;
const char confirmation[] = "0123456789ABCDEF";
char reply[160];
FILESYSTEM test_filesystem;

void wr16(uint8_t *p, uint16_t value) {
  p[0] = value;
  p[1] = value >> 8;
}

void wr32(uint8_t *p, uint32_t value) {
  wr16(p, value);
  wr16(p + 2, value >> 16);
}

uint8_t *installed_manifest(uint32_t version) {
  uint8_t *boot = reinterpret_cast<uint8_t *>(migration::BOOT_BASE);
  std::memset(boot, 0xFF, migration::BOOT_SIZE);
  std::snprintf(reinterpret_cast<char *>(boot + 0x100), 64,
                "UF2 Bootloader BL_0x%08X\r\n", version);
  uint8_t *p = boot + OTA_BOOT_CANDIDATE_MANIFEST_OFFSET;
  wr32(p, OTA_BOOT_MANIFEST_MAGIC0);
  wr32(p + 4, OTA_BOOT_MANIFEST_MAGIC1);
  wr16(p + 8, 1);
  wr16(p + 10, 44);
  wr32(p + 12, migration::BOOT_BASE);
  wr32(p + 16, migration::BOOT_SIZE);
  wr32(p + 20, migration_board_id);
  std::memcpy(p + 24, migration_device_name, 16);
  wr32(p + 44, OTA_BOOT_CONTINUITY_MAGIC0);
  wr32(p + 48, OTA_BOOT_CONTINUITY_MAGIC1);
  wr16(p + 52, 2);
  wr16(p + 54, 32);
  wr32(p + 56, version);
  wr16(p + 60, 140);
  wr16(p + 62, migration_softdevice_fwid);
  wr32(p + 64, migration_app_base);
  wr16(p + 68, 1);
  wr16(p + 70, 0);
  wr32(p + 72, 0);
  return p;
}

void seal_manifest(uint8_t *p) {
  const uint8_t *boot = reinterpret_cast<const uint8_t *>(migration::BOOT_BASE);
  wr32(p + 40, ota_boot_image_crc32(boot, migration::BOOT_SIZE,
                                  OTA_BOOT_CANDIDATE_MANIFEST_OFFSET + 40));
}

void replace_boot_label(const char *label) {
  auto *boot = reinterpret_cast<uint8_t *>(migration::BOOT_BASE);
  std::memset(boot + 0x100, 0xFF, 256);
  std::strcpy(reinterpret_cast<char *>(boot + 0x100), label);
  seal_manifest(boot + OTA_BOOT_CANDIDATE_MANIFEST_OFFSET);
}

void arm() {
  assert(bootloader_migration_request(confirmation, reply, sizeof(reply)));
  assert(bootloader_migration_pending());
  assert(std::strstr(reply, "OK migration armed"));
  assert(power_registers.GPREGRET == mesh::ota::ATTEMPT_MARKER);
  assert(migration_test::durable_lock_present);
  assert(std::memcmp(migration_test::durable_lock_hash,
                     migration_test::application_hash, 32) == 0);
}

void no_install(unsigned softdevice_reads = 0) {
  assert(migration_test::endf_checks == 0);
  assert(migration_test::softdevice_checks == softdevice_reads);
  assert(migration_test::disables == 0);
  assert(migration_test::resets == 0);
  assert(migration_test::copies == 0);
}

void status_result(unsigned result) {
  bootloader_migration_status(reply, sizeof(reply));
  char expected[24];
  std::snprintf(expected, sizeof(expected), "last=%u", result);
  assert(std::strstr(reply, expected));
}

void begin_application(FILESYSTEM *fs = &test_filesystem) {
  migration_test::startup_metadata = true;
  bootloader_migration_begin(fs);
  migration_test::startup_metadata = false;
  migration_test::endf_checks = 0;
}

void restart_application(FILESYSTEM *fs = &test_filesystem) {
  // Recreate zero-initialized driver state while retaining GPREGRET, as a
  // hardware reset would. Test counters deliberately survive for assertions.
  mesh::ota::pending = false;
  mesh::ota::automatic_attempted = false;
  mesh::ota::automatic_blocked = false;
  mesh::ota::armed_at = 0;
  mesh::ota::last_result = 0;
  mesh::ota::result_is_current = false;
  mesh::ota::already_installed = false;
  mesh::ota::state_fs = nullptr;
  std::memset(mesh::ota::firmware_hash, 0, sizeof(mesh::ota::firmware_hash));
  mesh::ota::automatic_lock = MigrationAutoLock::Unavailable;
  mesh::ota::firmware_identified = false;
  mesh::ota::automatic_variant = migration::InstalledVariant::Unknown;
  mesh::ota::loader_excluded = false;
  migration_test::now = 0;
  begin_application(fs);
}
} // namespace

int main(int argc, char **argv) {
  assert(argc == 2);
  using namespace migration_test;
#if defined(_WIN32)
  assert(VirtualAlloc(reinterpret_cast<void *>(0xF0000), 0x10000,
                      MEM_RESERVE | MEM_COMMIT, PAGE_READWRITE) != nullptr);
#else
  assert(mmap(reinterpret_cast<void *>(0xF0000), 0x10000, PROT_READ | PROT_WRITE,
              MAP_PRIVATE | MAP_ANONYMOUS | MAP_FIXED, -1, 0) != MAP_FAILED);
#endif
#if defined(_WIN32)
  assert(VirtualAlloc(reinterpret_cast<void *>(0x20000), 0x10000,
                      MEM_RESERVE | MEM_COMMIT, PAGE_READWRITE) != nullptr);
#else
  assert(mmap(reinterpret_cast<void *>(0x26000), 4096, PROT_READ | PROT_WRITE,
              MAP_PRIVATE | MAP_ANONYMOUS | MAP_FIXED, -1, 0) != MAP_FAILED);
#endif
  seal_manifest(installed_manifest(0x02040BFF));
  begin_application();
  const char *scenario = argv[1];
  if (std::strcmp(scenario, "automatic-recovery-prefix") == 0 ||
      std::strcmp(scenario, "automatic-recovery-suffix") == 0 ||
      std::strcmp(scenario, "automatic-unlabelled-loader") == 0 ||
      std::strcmp(scenario, "automatic-ambiguous-loader") == 0 ||
      std::strcmp(scenario, "automatic-foreign-loader") == 0 ||
      std::strcmp(scenario, "automatic-damaged-loader") == 0) {
    if (std::strcmp(scenario, "automatic-recovery-prefix") == 0)
      replace_boot_label("UF2 Bootloader R_0x02040BFF\r\n");
    else if (std::strcmp(scenario, "automatic-recovery-suffix") == 0)
      replace_boot_label("UF2 Bootloader v0.11.0-OTAFIX2.4.11-recovery-allow-all\r\n");
    else if (std::strcmp(scenario, "automatic-unlabelled-loader") == 0)
      replace_boot_label("");
    else if (std::strcmp(scenario, "automatic-ambiguous-loader") == 0)
      replace_boot_label("UF2 Bootloader BL_0x02040BFF\r\nUF2 Bootloader BL_0x02040BFF\r\n");
    else {
      auto *p = reinterpret_cast<uint8_t *>(migration::BOOT_BASE) + OTA_BOOT_CANDIDATE_MANIFEST_OFFSET;
      if (std::strcmp(scenario, "automatic-foreign-loader") == 0) {
        wr32(p + 20, migration_board_id ^ 1);
        seal_manifest(p);
      } else {
        p[40] ^= 1;
      }
    }
    now = 70000;
    bootloader_migration_tick(true, true);
    assert(!bootloader_migration_pending());
    assert(!bootloader_migration_service_due());
    assert(durable_lock_present && store_writes == 1);
    no_install(softdevice_checks);
    bootloader_migration_status(reply, sizeof(reply));
    const bool recovery = std::strstr(scenario, "recovery") != nullptr;
    assert(std::strstr(reply, recovery ? "auto=recovery" : "auto=unrecognized"));
    now = 140000;
    bootloader_migration_tick(true, true);
    no_install(softdevice_checks);
    assert(!bootloader_migration_request(nullptr, reply, sizeof(reply)));
    assert(!bootloader_migration_pending());
    assert(std::strstr(reply, "not a reviewed exact compatible image"));
    assert(durable_lock_present && store_writes == 1);
    now = 141500;
    bootloader_migration_tick(true, true);
    assert(endf_checks == 0); // Explicit commands cannot bypass source identity.
  } else if (std::strncmp(scenario, "stock-", 6) == 0) {
    auto *boot = reinterpret_cast<uint8_t *>(migration::BOOT_BASE);
    std::memset(boot, 0xFF, migration::BOOT_SIZE);
    std::strcpy(reinterpret_cast<char *>(boot + 0x100), "UF2 Bootloader 0.4.3\r\n");
    stock_installed = true;
    const bool automatic = std::strcmp(scenario, "stock-automatic") == 0;
    const bool manual = std::strcmp(scenario, "stock-manual") == 0;
    if (std::strcmp(scenario, "stock-damaged-hash") == 0) stock_hash_damaged = true;
    else if (std::strcmp(scenario, "stock-recovery") == 0)
      std::strcpy(reinterpret_cast<char *>(boot + 0x100), "UF2 Bootloader R_0x02040BFF\r\n");
    else if (std::strcmp(scenario, "stock-modern-mismatch") == 0) {
      auto *p = installed_manifest(0x02040BFF);
      p[24] = 'X'; seal_manifest(p);
    } else assert(automatic || manual);
    if (automatic) {
      now = 69999; bootloader_migration_tick(true, true);
      assert(!bootloader_migration_pending());
      now = 70000; bootloader_migration_tick(true, true);
    } else {
      const bool accepted = bootloader_migration_request(nullptr, reply, sizeof(reply));
      assert(accepted == manual);
    }
    assert(bootloader_migration_pending() == (automatic || manual));
    if (automatic || manual) {
      assert(durable_lock_present && store_writes == 1);
      assert(power_registers.GPREGRET == mesh::ota::ATTEMPT_MARKER);
      now += 1499; bootloader_migration_tick(true, true);
      assert(bootloader_migration_pending() && endf_checks == 0);
      ++now; bootloader_migration_tick(true, true);
      assert(!bootloader_migration_pending() && endf_checks == 1);
    } else {
      assert(!durable_lock_present && store_writes == 0);
      assert(std::strstr(reply, "not a reviewed exact compatible image"));
      now = 70000; bootloader_migration_tick(true, true);
      assert(!bootloader_migration_pending());
      assert(endf_checks == 0);
    }
  } else if (std::strcmp(scenario, "previous-image-interrupted-classification") == 0) {
    durable_lock_present = true;
    std::memcpy(durable_lock_hash, application_hash, 32);
    durable_lock_hash[31] ^= 1;
    power_registers.GPREGRET = mesh::ota::ATTEMPT_MARKER;
    restart_application();
    now = 70000;
    interrupt_classification = true;
    bool interrupted = false;
    try {
      bootloader_migration_tick(true, true);
    } catch (const std::runtime_error &error) {
      assert(std::strcmp(error.what(), "interrupted bootloader classification") == 0);
      interrupted = true;
    }
    assert(interrupted && classification_checks == 1);
    assert(!bootloader_migration_pending());
    assert(durable_lock_present && store_writes == 1);
    assert(std::memcmp(durable_lock_hash, application_hash, 32) == 0);
    assert(power_registers.GPREGRET == mesh::ota::ATTEMPT_MARKER);
    const unsigned reads_before_reset = softdevice_checks;
    interrupt_classification = false;
    restart_application();
    now = 70000;
    assert(!bootloader_migration_service_due());
    bootloader_migration_tick(true, true);
    assert(!bootloader_migration_pending());
    assert(classification_checks == 1 && store_writes == 1);
    no_install(reads_before_reset);
  } else if (std::strcmp(scenario, "durable-same-firmware-cold-boot") == 0 ||
             std::strcmp(scenario, "durable-same-firmware-old-loader") == 0) {
    now = 70000;
    bootloader_migration_tick(true, true);
    assert(bootloader_migration_pending());
    assert(durable_lock_present && store_writes == 1);
    const bool installed = std::strcmp(scenario, "durable-same-firmware-cold-boot") == 0;
    if (installed) seal_manifest(installed_manifest(migration_boot_version));
    power_registers.GPREGRET = 0; // A power cycle loses the retained register.
    restart_application();
    const unsigned reads_before = softdevice_checks;
    now = 70000;
    assert(!bootloader_migration_service_due());
    bootloader_migration_tick(true, true);
    assert(!bootloader_migration_pending());
    assert(store_writes == 1);
    no_install(reads_before);
    assert(bootloader_migration_request(nullptr, reply, sizeof(reply)));
    assert(bootloader_migration_pending() == !installed);
    assert(store_writes == 1); // An explicit retry reuses this image's lock.
  } else if (std::strcmp(scenario, "durable-different-firmware") == 0 ||
             std::strcmp(scenario, "durable-different-firmware-failure-marker") == 0) {
    now = 70000;
    bootloader_migration_tick(true, true);
    assert(bootloader_migration_pending());
    assert(durable_lock_present && store_writes == 1);
    assert(power_registers.GPREGRET == mesh::ota::ATTEMPT_MARKER);
    if (std::strcmp(scenario, "durable-different-firmware-failure-marker") == 0)
      power_registers.GPREGRET = 0xCC;
    application_hash[31] ^= 1; // The full 32-byte identity must distinguish images.
    restart_application();
    now = 70000;
    assert(bootloader_migration_service_due());
    bootloader_migration_tick(true, true);
    assert(bootloader_migration_pending()); // Old retained guards belong to the old image.
    assert(store_writes == 2);
    assert(std::memcmp(durable_lock_hash, application_hash, 32) == 0);
    assert(endf_checks == 0);
  } else if (std::strcmp(scenario, "automatic-store-unavailable") == 0 ||
             std::strcmp(scenario, "automatic-store-null") == 0 ||
             std::strcmp(scenario, "automatic-invalid-application") == 0 ||
             std::strcmp(scenario, "automatic-store-write-failure") == 0) {
    if (std::strcmp(scenario, "automatic-store-unavailable") == 0)
      store_available = false;
    else if (std::strcmp(scenario, "automatic-store-null") == 0) {
      // The role has no durable filesystem handle at this boot.
    }
    else if (std::strcmp(scenario, "automatic-invalid-application") == 0)
      startup_valid = false;
    else
      store_write_ok = false;
    restart_application(std::strcmp(scenario, "automatic-store-null") == 0
                            ? nullptr : &test_filesystem);
    now = 70000;
    bootloader_migration_tick(true, true);
    assert(!bootloader_migration_pending());
    assert(!bootloader_migration_service_due());
    assert(!durable_lock_present && store_writes == 0);
    no_install(softdevice_checks);
    assert(!bootloader_migration_request(nullptr, reply, sizeof(reply)));
    assert(!bootloader_migration_pending());
    no_install(softdevice_checks);
    seal_manifest(installed_manifest(migration_boot_version));
    assert(bootloader_migration_request(nullptr, reply, sizeof(reply)));
    assert(!bootloader_migration_pending()); // A version no-op needs no store write.
  } else if (std::strcmp(scenario, "automatic-delay") == 0 ||
      std::strcmp(scenario, "automatic-arm-enabled") == 0) {
    softdevice_enabled = std::strcmp(scenario, "automatic-arm-enabled") == 0;
    assert(!bootloader_migration_service_due());
    now = 69999;
    assert(!bootloader_migration_service_due());
    bootloader_migration_tick(true, true);
    assert(!bootloader_migration_pending());
    no_install();
    now = 70000;
    assert(bootloader_migration_service_due());
    bootloader_migration_tick(true, true);
    assert(bootloader_migration_pending());
    assert(bootloader_migration_service_due());
    no_install(3);
    assert(durable_lock_present && store_writes == 1);
    assert(power_registers.GPREGRET == mesh::ota::ATTEMPT_MARKER);
    assert(retained_reads == (softdevice_enabled ? 3u : 0u));
    assert(retained_clears == (softdevice_enabled ? 1u : 0u));
    assert(retained_sets == (softdevice_enabled ? 1u : 0u));
    now = 71499;
    bootloader_migration_tick(true, true);
    assert(bootloader_migration_pending());
    no_install(3);
    now = 71500;
    bootloader_migration_tick(true, true);
    assert(endf_checks == 1);
    assert(!bootloader_migration_pending());
    assert(!bootloader_migration_service_due());
    now = 140000;
    bootloader_migration_tick(true, true);
    assert(endf_checks == 1); // A failed preflight must not be retried automatically.
    assert(softdevice_checks == 3);
    status_result(1);
  } else if (std::strcmp(scenario, "automatic-busy") == 0) {
    now = 70000;
    bootloader_migration_tick(false, true);
    assert(!bootloader_migration_pending());
    assert(bootloader_migration_service_due());
    no_install();
    now = 72000;
    bootloader_migration_tick(true, false);
    assert(!bootloader_migration_pending());
    assert(bootloader_migration_service_due());
    no_install();
    now = 74000;
    bootloader_migration_tick(false, false);
    assert(!bootloader_migration_pending());
    assert(bootloader_migration_service_due());
    no_install();
    now = 76000;
    bootloader_migration_tick(true, true);
    assert(bootloader_migration_pending());
    no_install(3);
    now = 77499;
    bootloader_migration_tick(true, true);
    no_install(3);
    now = 77500;
    bootloader_migration_tick(true, true);
    assert(endf_checks == 1); // Grace starts when the deferred request is armed.
  } else if (std::strcmp(scenario, "automatic-same-version") == 0 ||
             std::strcmp(scenario, "automatic-same-version-enabled") == 0 ||
             std::strcmp(scenario, "automatic-newer-version") == 0 ||
             std::strcmp(scenario, "automatic-exact-image") == 0) {
    softdevice_enabled = std::strcmp(scenario, "automatic-same-version-enabled") == 0;
    if (std::strcmp(scenario, "automatic-exact-image") == 0) {
      candidate_installed = true;
    } else {
      seal_manifest(installed_manifest(
          (std::strcmp(scenario, "automatic-same-version") == 0 || softdevice_enabled)
              ? migration_boot_version : 0x02040D01));
    }
    now = 70000;
    bootloader_migration_tick(true, true);
    assert(!bootloader_migration_pending());
    assert(!bootloader_migration_service_due());
    no_install(3);
    assert(power_registers.GPREGRET == 0); // Clear the provisional check marker.
    assert(retained_reads == (softdevice_enabled ? 3u : 0u));
    assert(retained_clears == (softdevice_enabled ? 2u : 0u));
    assert(retained_sets == (softdevice_enabled ? 1u : 0u));
    now = 140000;
    bootloader_migration_tick(true, true);
    assert(!bootloader_migration_pending());
    assert(!bootloader_migration_service_due());
    no_install(3);
    bootloader_migration_status(reply, sizeof(reply));
    assert(std::strstr(reply, candidate_installed ? "already installed" : "up to date"));
  } else if (std::strcmp(scenario, "automatic-retained-failure") == 0) {
    power_registers.GPREGRET = 0xCC;
    now = 70000;
    bootloader_migration_tick(true, true);
    assert(!bootloader_migration_pending());
    assert(!bootloader_migration_service_due());
    no_install(1);
    now = 140000;
    bootloader_migration_tick(true, true);
    no_install(1); // Retained failures must not create a warm-reset retry loop.
    bootloader_migration_status(reply, sizeof(reply));
    assert(std::strstr(reply, "auto=blocked"));
    assert(bootloader_migration_request(nullptr, reply, sizeof(reply)));
    assert(bootloader_migration_pending());
    assert(std::strstr(reply, "OK migration armed"));
    now = 141500;
    bootloader_migration_tick(true, true);
    assert(endf_checks == 1); // An explicit operator request may still retry.
    assert(!bootloader_migration_pending());
  } else if (std::strcmp(scenario, "manual-cancel-consumes-automatic") == 0 ||
             std::strcmp(scenario, "manual-failure-consumes-automatic") == 0) {
    arm();
    now = 1500;
    const bool cancel = std::strcmp(scenario, "manual-cancel-consumes-automatic") == 0;
    bootloader_migration_tick(true, !cancel);
    assert(!bootloader_migration_pending());
    assert(endf_checks == (cancel ? 0u : 1u));
    now = 70000;
    assert(!bootloader_migration_service_due());
    bootloader_migration_tick(true, true);
    assert(!bootloader_migration_pending());
    assert(endf_checks == (cancel ? 0u : 1u));
    assert(softdevice_checks == 1);
  } else if (std::strcmp(scenario, "automatic-cancel") == 0) {
    now = 70000;
    bootloader_migration_tick(true, true);
    assert(bootloader_migration_pending());
    now = 70100;
    bootloader_migration_tick(true, false);
    assert(!bootloader_migration_pending());
    assert(!bootloader_migration_service_due());
    no_install(3);
    now = 140000;
    bootloader_migration_tick(true, true);
    assert(!bootloader_migration_pending());
    assert(!bootloader_migration_service_due());
    no_install(3);
    status_result(16);
  } else if (std::strcmp(scenario, "invalid-confirmation-preserves-automatic") == 0) {
    now = 1000;
    assert(!bootloader_migration_request("0123456789ABCDE0", reply, sizeof(reply)));
    assert(!bootloader_migration_pending());
    now = 70000;
    assert(bootloader_migration_service_due());
    bootloader_migration_tick(true, true);
    assert(bootloader_migration_pending());
    no_install(3);
    now = 71500;
    bootloader_migration_tick(true, true);
    assert(endf_checks == 1);
    assert(!bootloader_migration_pending());
    assert(!bootloader_migration_service_due());
  } else if (std::strcmp(scenario, "automatic-status-read-failure") == 0 ||
             std::strcmp(scenario, "automatic-marker-read-failure") == 0) {
    power_registers.GPREGRET = mesh::ota::ATTEMPT_MARKER;
    if (std::strcmp(scenario, "automatic-status-read-failure") == 0) {
      softdevice_status = 1;
    } else {
      softdevice_enabled = true;
      retained_get_status = 1;
    }
    now = 70000;
    bootloader_migration_tick(true, true);
    assert(!bootloader_migration_pending());
    assert(!bootloader_migration_service_due());
    no_install(1);
    assert(retained_reads == (softdevice_enabled ? 1u : 0u));
    assert(retained_clears == 0 && retained_sets == 0);
    assert(power_registers.GPREGRET == mesh::ota::ATTEMPT_MARKER);
    bootloader_migration_status(reply, sizeof(reply));
    assert(std::strstr(reply, "auto=blocked"));
  } else if (std::strcmp(scenario, "guard-softdevice-enabled") == 0) {
    softdevice_enabled = true;
    power_registers.GPREGRET = 0xAA;
    arm();
    assert(retained_clears == 1 && retained_sets == 1);
    no_install(1);
    assert(bootloader_migration_request(nullptr, reply, sizeof(reply)));
    assert(retained_clears == 1 && retained_sets == 1); // Duplicates do no work.
    now = 1500;
    bootloader_migration_tick(true, true);
    assert(endf_checks == 1);
    assert(power_registers.GPREGRET == mesh::ota::ATTEMPT_MARKER);
  } else if (std::strcmp(scenario, "guard-softdevice-status-failure") == 0 ||
             std::strcmp(scenario, "guard-marker-read-failure") == 0 ||
             std::strcmp(scenario, "guard-clear-failure") == 0 ||
             std::strcmp(scenario, "guard-set-failure") == 0) {
    power_registers.GPREGRET = 0x37;
    if (std::strcmp(scenario, "guard-softdevice-status-failure") == 0) {
      softdevice_status = 1;
    } else {
      softdevice_enabled = true;
      if (std::strcmp(scenario, "guard-marker-read-failure") == 0)
        retained_get_status = 1;
      else if (std::strcmp(scenario, "guard-clear-failure") == 0)
        retained_clear_status = 1;
      else
        retained_set_status = 1;
    }
    assert(!bootloader_migration_request(nullptr, reply, sizeof(reply)));
    assert(std::strstr(reply, "ERR bootloader reboot guard unavailable"));
    assert(!bootloader_migration_pending());
    no_install(1);
    assert(retained_clears == (softdevice_enabled && !retained_get_status ? 1u : 0u));
    assert(retained_sets == (retained_set_status ? 1u : 0u));
    assert(power_registers.GPREGRET == (retained_set_status ? 0u : 0x37u));
    now = 70000;
    assert(!bootloader_migration_service_due());
    bootloader_migration_tick(true, true);
    no_install(1);
    status_result(17);
  } else if (std::strcmp(scenario, "automatic-interrupted-check") == 0 ||
             std::strcmp(scenario, "automatic-interrupted-arm") == 0) {
    const bool during_check = std::strcmp(scenario, "automatic-interrupted-check") == 0;
    now = 70000;
    interrupt_hash = during_check;
    bool interrupted = false;
    try {
      bootloader_migration_tick(true, true);
    } catch (const std::runtime_error &error) {
      assert(during_check);
      assert(std::strcmp(error.what(), "interrupted bootloader check") == 0);
      interrupted = true;
    }
    assert(interrupted == during_check);
    assert(bootloader_migration_pending() == !during_check);
    assert(power_registers.GPREGRET == mesh::ota::ATTEMPT_MARKER);
    assert(durable_lock_present);
    assert(endf_checks == 0);
    const unsigned initial_reads = softdevice_checks;
    interrupt_hash = false;
    restart_application();
    now = 69999;
    assert(!bootloader_migration_service_due());
    now = 70000;
    assert(!bootloader_migration_service_due());
    bootloader_migration_tick(true, true);
    assert(!bootloader_migration_pending());
    assert(!bootloader_migration_service_due());
    assert(power_registers.GPREGRET == mesh::ota::ATTEMPT_MARKER);
    no_install(initial_reads);
    now = 140000;
    bootloader_migration_tick(true, true);
    no_install(initial_reads); // A second reset-time check must not repeat.
    bootloader_migration_status(reply, sizeof(reply));
    assert(std::strstr(reply, "auto=locked for this firmware"));
    arm(); // An operator can retry after correcting an interruption's cause.
    now = 141500;
    bootloader_migration_tick(true, true);
    assert(endf_checks == 1);
    assert(!bootloader_migration_pending());
  } else if (std::strcmp(scenario, "already-installed") == 0) {
    power_registers.GPREGRET = 0x37;
    candidate_installed = true;
    assert(bootloader_migration_request(confirmation, reply, sizeof(reply)));
    assert(!bootloader_migration_pending());
    assert(std::strstr(reply, "already installed"));
    now = 2000;
    bootloader_migration_tick(true, true);
    no_install();
    assert(power_registers.GPREGRET == 0x37);
    bootloader_migration_status(reply, sizeof(reply));
    assert(std::strstr(reply, "already installed"));
  } else if (std::strcmp(scenario, "same-version") == 0 ||
             std::strcmp(scenario, "newer-version") == 0) {
    power_registers.GPREGRET = 0x37;
    const uint32_t version = std::strcmp(scenario, "same-version") == 0
                                 ? migration_boot_version : 0x02040D01;
    seal_manifest(installed_manifest(version));
    // Live status has to detect this before any request, as it would after reboot.
    bootloader_migration_status(reply, sizeof(reply));
    assert(std::strstr(reply, "up to date"));
    assert(!bootloader_migration_pending());
    assert(!bootloader_migration_request("0123456789ABCDE0", reply, sizeof(reply)));
    assert(!bootloader_migration_pending());
    softdevice_checks = 0; // Status may read the retained diagnostic result.
    const char *tokens[] = {nullptr, confirmation};
    for (const char *token : tokens) {
      assert(bootloader_migration_request(token, reply, sizeof(reply)));
      assert(!bootloader_migration_pending());
      assert(std::strstr(reply, "up to date"));
      now += 2000;
      bootloader_migration_tick(true, true);
      no_install();
      assert(power_registers.GPREGRET == 0x37);
    }
  } else if (std::strcmp(scenario, "easy-update") == 0 ||
             std::strcmp(scenario, "older-preview") == 0) {
    if (std::strcmp(scenario, "older-preview") == 0)
      seal_manifest(installed_manifest(0x02040CFE));
    assert(bootloader_migration_request(nullptr, reply, sizeof(reply)));
    assert(bootloader_migration_pending());
    assert(std::strstr(reply, "OK migration armed"));
    now = 1499;
    bootloader_migration_tick(true, true);
    no_install(1);
    ++now;
    bootloader_migration_tick(true, true);
    assert(endf_checks == 1);
    assert(!bootloader_migration_pending());
  } else if (std::strncmp(scenario, "invalid-version-", 16) == 0) {
    uint8_t *p = installed_manifest(0x02040DFF);
    const char *mutation = scenario + 16;
    if (std::strcmp(mutation, "board") == 0) wr32(p + 20, migration_board_id ^ 1);
    else if (std::strcmp(mutation, "name") == 0) p[24] = 'X';
    else if (std::strcmp(mutation, "family") == 0) wr16(p + 60, 132);
    else if (std::strcmp(mutation, "fwid") == 0) wr16(p + 62, 0x123);
    else if (std::strcmp(mutation, "app") == 0) wr32(p + 64, 0x27000);
    else if (std::strcmp(mutation, "abi") == 0) wr16(p + 68, 2);
    else if (std::strcmp(mutation, "continuity") == 0) std::memset(p + 44, 0xFF, 32);
    else assert(std::strcmp(mutation, "crc") == 0);
    seal_manifest(p);
    if (std::strcmp(mutation, "crc") == 0) p[40] ^= 1;
    bootloader_migration_status(reply, sizeof(reply));
    assert(!std::strstr(reply, "up to date"));
    assert(!bootloader_migration_request(nullptr, reply, sizeof(reply)));
    assert(!bootloader_migration_pending());
    assert(std::strstr(reply, "not a reviewed exact compatible image"));
    assert(!std::strstr(reply, "up to date"));
    now = 1500;
    bootloader_migration_tick(true, true);
    // Source rejection happens while the application/SoftDevice stays running.
    assert(endf_checks == 0);
    assert(!bootloader_migration_pending());
  } else if (std::strcmp(scenario, "wrong-confirmation") == 0) {
    assert(!bootloader_migration_request("0123456789ABCDE0", reply, sizeof(reply)));
    assert(!bootloader_migration_request("0123456789abcdef", reply, sizeof(reply)));
    assert(!bootloader_migration_request("0123456789ABCDEF ", reply, sizeof(reply)));
    assert(!bootloader_migration_pending());
    now = 2000;
    bootloader_migration_tick(true, true);
    no_install();
  } else if (std::strcmp(scenario, "grace-and-drain") == 0) {
    arm();
    now = 1499;
    bootloader_migration_tick(true, true);
    no_install(1);
    now = 1500;
    bootloader_migration_tick(false, true);
    no_install(1);
    now = 29999;
    bootloader_migration_tick(false, true);
    no_install(1);
    assert(bootloader_migration_pending());
    bootloader_migration_tick(true, true);
    assert(endf_checks == 1);
    assert(!bootloader_migration_pending());
    assert(disables == 0 && resets == 0 && copies == 0);
    status_result(1);
  } else if (std::strcmp(scenario, "duplicate-arm") == 0) {
    arm();
    now = 1499;
    assert(!bootloader_migration_request("0123456789ABCDE0", reply, sizeof(reply)));
    assert(bootloader_migration_pending());
    assert(bootloader_migration_request(nullptr, reply, sizeof(reply)));
    assert(std::strstr(reply, "OK bootloader update already armed"));
    assert(bootloader_migration_request(confirmation, reply, sizeof(reply)));
    assert(std::strstr(reply, "already armed"));
    now = 1500;
    bootloader_migration_tick(true, true);
    assert(endf_checks == 1); // A duplicate must not extend the grace period.
  } else if (std::strcmp(scenario, "timeout") == 0) {
    arm();
    now = 30000;
    bootloader_migration_tick(true, true);
    assert(!bootloader_migration_pending());
    no_install(1);
    status_result(15);
    bootloader_migration_tick(true, true);
    assert(endf_checks == 0);
  } else if (std::strcmp(scenario, "ota-cancel-rearm") == 0) {
    arm();
    now = 100;
    bootloader_migration_tick(false, false);
    assert(!bootloader_migration_pending());
    no_install(1);
    status_result(16);
    now = 2000;
    bootloader_migration_tick(true, true);
    assert(endf_checks == 0); // Becoming idle must not revive cancelled work.
    arm();
    now = 3499;
    bootloader_migration_tick(true, true);
    assert(endf_checks == 0);
    now = 3500;
    bootloader_migration_tick(true, true);
    assert(endf_checks == 1);
  } else if (std::strcmp(scenario, "ota-cancel-at-install") == 0) {
    arm();
    now = 1500;
    bootloader_migration_tick(true, false);
    assert(!bootloader_migration_pending());
    no_install(1);
    status_result(16);
  } else if (std::strcmp(scenario, "stale-retained-result") == 0) {
    power_registers.GPREGRET = 0xCC;
    status_result(12);
    arm();
    status_result(0);
    now = 30000;
    bootloader_migration_tick(true, true);
    assert(!bootloader_migration_pending());
    status_result(15);
    arm();
    status_result(0);
    ++now;
    bootloader_migration_tick(true, false);
    assert(!bootloader_migration_pending());
    status_result(16);
    assert(power_registers.GPREGRET == mesh::ota::ATTEMPT_MARKER);
    assert(endf_checks == 0);
  } else if (std::strcmp(scenario, "clock-wrap") == 0) {
    now = UINT32_MAX - 999u;
    arm();
    now += 1499u;
    bootloader_migration_tick(true, true);
    no_install(1);
    ++now;
    bootloader_migration_tick(true, true);
    assert(endf_checks == 1);
  } else {
    throw std::runtime_error("unknown scenario");
  }
  assert(disables == 0 && resets == 0 && copies == 0);
}
