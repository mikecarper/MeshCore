/* Executes pinned OTAFIX's complete GAT internal/hybrid apply entry point.
 * Hardware hooks model NOR bit transitions and separate retained SRAM. */
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdbool.h>
#include "ota_layout.h"
#include "ota_hybrid_handoff.h"

static uint8_t flash[MOTA_NRF52_APP_END];
static uint8_t arena[MOTA_HYBRID_ARENA_SIZE];
static mota_hybrid_handoff_t auth;
static uint32_t trigger, result, reset_reason = 4;
static uint16_t bank = 1, bank_crc;
static uint32_t bank_size, workspace_end, application_writes;
static int fail_write, boundary_violation;

void otah_read(uint32_t address, void *data, uint32_t size) {
  if ((uint64_t)address + size > sizeof(flash)) abort();
  memcpy(data, flash + address, size);
}
void otah_erase(uint32_t address) {
  if (address % 4096 || (uint64_t)address + 4096 > sizeof(flash)) abort();
  if (address < workspace_end) {
    if (bank != 0xff || address < MOTA_NRF52_APP_BASE) boundary_violation = 1;
    application_writes++;
  }
  memset(flash + address, 255, 4096);
}
void otah_write_words(uint32_t address, const uint32_t *data, uint32_t words) {
  if (address % 4 || (uint64_t)address + words * 4 > sizeof(flash)) abort();
  if (address < workspace_end) {
    if (bank != 0xff || address < MOTA_NRF52_APP_BASE ||
        (uint64_t)address + words * 4 > workspace_end) boundary_violation = 1;
    application_writes++;
    if (fail_write) return;
  }
  const uint8_t *bytes = (const uint8_t *)data;
  for (uint32_t i = 0; i < words * 4; i++) flash[address + i] &= bytes[i];
}
uint32_t otah_gpregret_get(void) { return trigger; }
void otah_gpregret_set(uint32_t value) { trigger = value; }
uint32_t otah_gpregret2_get(void) { return result; }
void otah_gpregret2_set(uint32_t value) { result = value; }
uint32_t otah_resetreas_get(void) { return reset_reason; }
void otah_hybrid_handoff_read(void *dst, uint32_t len) { memcpy(dst, &auth, len); }
void otah_hybrid_handoff_consume(void) { memset(&auth, 0, sizeof(auth)); }
int otah_hybrid_ram_read(uint32_t offset, void *dst, uint32_t size) {
  if ((uint64_t)offset + size > sizeof(arena)) return 0;
  memcpy(dst, arena + offset, size); return 1;
}
uint16_t otah_crc16(uint32_t address, uint32_t size) {
  uint16_t crc = 0xffff;
  for (uint32_t i = 0; i < size; i++) {
    crc = (uint16_t)(crc >> 8) | (uint16_t)(crc << 8);
    crc ^= flash[address + i];
    crc ^= (uint8_t)(crc & 255) >> 4;
    crc ^= (uint16_t)(crc << 12);
    crc ^= (uint16_t)((crc & 255) << 5);
  }
  return crc;
}
void otah_settings_commit(uint16_t value, uint16_t crc, uint32_t size) {
  bank = value; bank_crc = crc; bank_size = size;
}
int otah_crc_bound_app_size(uint32_t *size) {
  *size = bank_size; return bank == 1 && bank_crc != 0;
}
const uint8_t *otah_flash_pointer(uint32_t address, uint32_t size) {
  return (uint64_t)address + size <= sizeof(flash) ? flash + address : NULL;
}
int otah_mbr_copy_bl(uint32_t address, uint32_t words) { abort(); }

#include "ota_delta.c"

int otah_installed_boot_info(bootloader_image_info_t *info) {
  info->boot_version = 0x0204010c; info->softdevice_family = 140;
  info->softdevice_fwid = 0xb6; info->app_base = MOTA_NRF52_APP_BASE;
  info->layout_abi = BOOTLOADER_UPDATE_LAYOUT_ABI; return 1;
}
uint16_t otah_runtime_softdevice_fwid(void) { return 0xb6; }

static uint8_t *load(const char *path, uint32_t *size) {
  FILE *f = fopen(path, "rb"); if (!f) abort();
  fseek(f, 0, SEEK_END); long length = ftell(f); rewind(f);
  if (length <= 0 || length > 1048576) abort();
  uint8_t *data = malloc(length);
  if (!data || fread(data, 1, length, f) != (size_t)length) abort();
  fclose(f); *size = (uint32_t)length; return data;
}
int main(int argc, char **argv) {
  if (argc != 5) return 2;
  uint32_t base_size, package_size, expected_size;
  uint8_t *base = load(argv[1], &base_size), *package = load(argv[2], &package_size);
  uint8_t *expected = load(argv[3], &expected_size);
  uint32_t prefix = (package_size - sizeof(arena) + 4095) & ~4095u;
  workspace_end = MOTA_NRF52_STAGE_CEILING_EXPANDED - prefix;
  if (prefix >= package_size || package_size - prefix > sizeof(arena) ||
      MOTA_NRF52_APP_BASE + base_size > workspace_end) abort();
  memset(flash, 255, sizeof(flash));
  memcpy(flash + MOTA_NRF52_APP_BASE, base, base_size);
  memcpy(flash + workspace_end, package, prefix);
  memcpy(arena, package + prefix, package_size - prefix);
  /* Application authentication hashes the container with approval normalized. */
  uint32_t approval = (uint32_t)offsetof(mota_fixed_header_t, approval);
  memset(package + approval, 0, 4);
  uint8_t digest[32]; sha256_ctx_t sha; sha256_init(&sha);
  sha256_update(&sha, package, package_size); sha256_final(&sha, digest);
  memcpy(flash + workspace_end + approval, "APRV", 4);
  mota_hybrid_handoff_encode(&auth, package_size, workspace_end, prefix,
                             package_size - prefix, digest);
  bank_size = base_size; trigger = GPREGRET_OTA_APPLY; result = GPREGRET2_OTA_STAGE_HYBRID;
  if (!strcmp(argv[4], "corrupt-ram")) arena[package_size - prefix - 6] ^= 1;
  fail_write = !strcmp(argv[4], "write-failure");
  bool applied = ota_delta_check_and_apply();
  int matches = !memcmp(flash + MOTA_NRF52_APP_BASE, expected, expected_size);
  int intact = !memcmp(flash + MOTA_NRF52_APP_BASE, base, base_size);
  int crc_ok = bank == 1 && bank_size == expected_size &&
               bank_crc == otah_crc16(MOTA_NRF52_APP_BASE, expected_size);
  printf("{\"applied\":%d,\"status\":%u,\"bank\":%u,\"matches\":%d,"
         "\"baseIntact\":%d,\"crcOk\":%d,\"writes\":%u,\"boundsOk\":%d,"
         "\"flashPrefix\":%u,\"ramSuffix\":%u}\n",
         applied, result, bank, matches, intact, crc_ok, application_writes,
         !boundary_violation, prefix, package_size - prefix);
  free(base); free(package); free(expected); return 0;
}
