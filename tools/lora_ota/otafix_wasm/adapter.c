/* Host model for the pinned OTAFIX apply code; only the internal flash path
 * is modeled. QSPI, SD and hybrid handoffs deliberately fail closed. The
 * caller must instantiate a fresh module per boot, verify the exact release
 * and board profile, and compare the entire resulting image. */
#include <stdint.h>
#include <stddef.h>
#include <string.h>
#include <stdbool.h>
#include "ota_layout.h"
#include "ota_delta.h"
#if defined(MOTA_INTERNAL_BOOTLOADER_UPDATE)
#include "usb/uf2/bootloader_image.h"
int otah_crc_bound_app_size(uint32_t *size) { (void)size; return 0; }
int otah_installed_boot_info(bootloader_image_info_t *info) { (void)info; return 0; }
uint16_t otah_runtime_softdevice_fwid(void) { return 0; }
const uint8_t *otah_flash_pointer(uint32_t address,uint32_t len) { (void)address;(void)len;return NULL; }
int otah_mbr_copy_bl(uint32_t source,uint32_t count) { (void)source;(void)count;return -1; }
#endif
#if defined(MOTA_RAM_ARENA_SIZE) && MOTA_RAM_ARENA_SIZE > 0
uint32_t otah_resetreas_get(void) { return 0; }
void otah_hybrid_handoff_read(void *dst,uint32_t len) { memset(dst,0,len); }
void otah_hybrid_handoff_consume(void) {}
int otah_hybrid_ram_read(uint32_t off,void *dst,uint32_t len) { (void)off;(void)dst;(void)len;return -1; }
#endif
#if defined(MOTA_QSPI_FLASH)
bool ota_qspi_init(void) { return false; }
void ota_qspi_set_rak_source(uint8_t src) { (void)src; }
void ota_qspi_deinit(void) {}
uint32_t ota_qspi_capacity(void) { return 0; }
bool ota_qspi_read(uint32_t off,void *dst,uint32_t len) { (void)off;(void)dst;(void)len;return false; }
bool ota_qspi_write(uint32_t off,const void *src,uint32_t len) { (void)off;(void)src;(void)len;return false; }
#endif
#if defined(MOTA_SD_CARD)
void otah_sd_auth_read(void *dst,uint32_t len) { memset(dst,0,len); }
void otah_sd_auth_consume(void) {}
bool ota_sd_init(void) { return false; }
void ota_sd_deinit(void) {}
bool ota_sd_read_sector(uint32_t sector,uint8_t out[512]) { (void)sector;(void)out;return false; }
bool ota_sd_read_bytes(uint32_t first,uint32_t off,void *out,uint32_t len) { (void)first;(void)off;(void)out;(void)len;return false; }
#endif

static uint8_t flash[MOTA_NRF52_APP_END], base[0x100000], package[0x100000];
static uint32_t reg1, reg2, committed, commit_size, fault;
static int bounded(uint32_t addr, uint32_t n) { return addr <= sizeof(flash) && n <= sizeof(flash)-addr; }
void otah_read(uint32_t a, void *d, uint32_t n) { if (!bounded(a,n)) { fault=1; memset(d,0,n); return; } memcpy(d,flash+a,n); }
void otah_erase(uint32_t a) { if ((a & 4095) || !bounded(a,4096)) { fault=1; return; } memset(flash+a,255,4096); }
void otah_write_words(uint32_t a,const uint32_t *s,uint32_t n) { if (n>0x3fffffff || !bounded(a,n*4)) { fault=1; return; } for(uint32_t i=0;i<n*4;i++) flash[a+i]&=((const uint8_t*)s)[i]; }
uint32_t otah_gpregret_get(void) { return reg1; }
void otah_gpregret_set(uint32_t v) { reg1=v; }
uint32_t otah_gpregret2_get(void) { return reg2; }
void otah_gpregret2_set(uint32_t v) { reg2=v; }
uint16_t otah_crc16(uint32_t a,uint32_t n) { (void)a;(void)n;return 0x1234; }
void otah_settings_commit(uint16_t bank,uint16_t crc,uint32_t size) { (void)bank;(void)crc;committed++;commit_size=size; }
uintptr_t sim_base(void) { return (uintptr_t)base; }
uintptr_t sim_package(void) { return (uintptr_t)package; }
uintptr_t sim_result(void) { return (uintptr_t)(flash+MOTA_NRF52_APP_BASE); }
uint32_t sim_status(void) { return reg2; }
uint32_t sim_commit_size(void) { return commit_size; }
int sim_apply(uint32_t base_len,uint32_t package_len,uint32_t ceiling) {
  const uint32_t app=MOTA_NRF52_APP_BASE;
  if (!base_len || base_len>sizeof(base) || !package_len || package_len>sizeof(package) ||
      (ceiling!=MOTA_NRF52_STAGE_CEILING_LEGACY && ceiling!=MOTA_NRF52_STAGE_CEILING_EXPANDED) ||
      ceiling>sizeof(flash) || package_len>ceiling-app) return -1;
  uint32_t start=(ceiling-package_len)&~4095u;
  if (base_len>start-app || package_len<205 || !bounded(start,package_len)) return -2;
  memset(flash,255,sizeof(flash)); memcpy(flash+app,base,base_len); memcpy(flash+start,package,package_len);
  memcpy(flash+start+201,"APRV",4);
  reg1=GPREGRET_OTA_APPLY;reg2=ceiling==MOTA_NRF52_STAGE_CEILING_LEGACY?GPREGRET2_OTA_STAGE_LEGACY:GPREGRET2_OTA_STAGE_EXPANDED;
  fault=committed=commit_size=0;
  int ok=ota_delta_check_and_apply();
  if (fault) return -3;
  return ok && committed && reg2 == 0xB8 && commit_size ? 1 : 0;
}
