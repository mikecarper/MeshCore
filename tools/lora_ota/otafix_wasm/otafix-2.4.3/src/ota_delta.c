// MeshCore `.mota` delta-apply for the nRF52 bootloader (single-slot, detools in-place).
// See ota_delta.h for the contract. Compiles for the device (nrfx/SDK) and for a host test harness
// (OTA_DELTA_HOST_TEST: the test TU provides the otah_* flash/settings/gpregret stubs + crc16).
#include "ota_delta.h"
#include "ota_layout.h"
#include "ota_bl_info.h"
#if defined(MOTA_SD_CARD)
  #include "ota_sd_auth.h"
  #include "ota_sd_spi.h"
#endif
#if defined(MOTA_SD_BOOTLOADER_UPDATE)
  #include "ota_sd_boot_token.h"
#endif
#if defined(MOTA_QSPI_FLASH)
  #include "ota_qspi.h"
#endif
#if defined(MOTA_SD_BOOTLOADER_UPDATE) || defined(MOTA_QSPI_BOOTLOADER_UPDATE) || \
  defined(MOTA_INTERNAL_BOOTLOADER_UPDATE)
  #include "usb/uf2/bootloader_image.h"
#endif
#include "detools/detools.h"
#include <string.h>
#include <stdint.h>

#if defined(OTA_DELTA_HOST_TEST) && !defined(MOTA_SOFTDEVICE_FAMILY)
  #define MOTA_SOFTDEVICE_FAMILY 140u
#endif

#if defined(MOTA_QSPI_BOOTLOADER_UPDATE)
  #define MOTA_QSPI_STORAGE_FLAGS \
    (MOTA_BL_STORAGE_QSPI | MOTA_BL_STORAGE_STAGE_CEILING | MOTA_BL_STORAGE_BOOT_UPDATE)
#else
  #define MOTA_QSPI_STORAGE_FLAGS (MOTA_BL_STORAGE_QSPI | MOTA_BL_STORAGE_STAGE_CEILING)
#endif

#if defined(MOTA_SD_BOOTLOADER_UPDATE)
  #define MOTA_SD_STORAGE_FLAGS (MOTA_BL_STORAGE_SD | MOTA_BL_STORAGE_BOOT_UPDATE)
#else
  #define MOTA_SD_STORAGE_FLAGS MOTA_BL_STORAGE_SD
#endif

#if defined(MOTA_SD_BOOTLOADER_UPDATE) || defined(MOTA_QSPI_BOOTLOADER_UPDATE) || \
  defined(MOTA_INTERNAL_BOOTLOADER_UPDATE)
  #define MOTA_BOOTLOADER_UPDATE_ENABLED 1
#endif

#if defined(MOTA_SD_BOOTLOADER_UPDATE)
  #define MOTA_BOOT_UPDATE_STORAGE_FLAGS MOTA_SD_STORAGE_FLAGS
#elif defined(MOTA_QSPI_BOOTLOADER_UPDATE)
  #define MOTA_BOOT_UPDATE_STORAGE_FLAGS MOTA_QSPI_STORAGE_FLAGS
#elif defined(MOTA_INTERNAL_BOOTLOADER_UPDATE)
  // No backend bit means the normal internal staging window. GPREGRET 0x6A vs
  // 0x6B selects an application vs bootloader package in that shared window.
  #define MOTA_BOOT_UPDATE_STORAGE_FLAGS (MOTA_BL_STORAGE_STAGE_CEILING | MOTA_BL_STORAGE_BOOT_UPDATE)
#endif

#if defined(MOTA_BOOTLOADER_UPDATE_ENABLED)
  #define MOTA_BOOT_UPDATE_CODEC_MASK ((1u << CODEC_FULL) | (1u << CODEC_INPLACE))
#endif

// Capability marker the running MeshCore app scans for (see ota_bl_info.h). `used` + the reference in
// ota_delta_check_and_apply() keep it through -ffunction/data-sections, --gc-sections and -flto.
__attribute__((used, aligned(4))) const mota_bl_info_t g_mota_bl_info = {
  {MOTA_BL_MAGIC0, MOTA_BL_MAGIC1, MOTA_BL_MAGIC2, MOTA_BL_MAGIC3, MOTA_BL_MAGIC4, MOTA_BL_MAGIC5, MOTA_BL_MAGIC6,
   MOTA_BL_MAGIC7},
  MOTA_BL_APPLY_ABI,
#if defined(MOTA_SD_CARD)
  (uint16_t)((1u << 0) | (1u << 2)), // SD: full images and in-place deltas
  {MOTA_SD_STORAGE_FLAGS, 0, 0, 0},  // retained-auth SD source
#elif defined(MOTA_QSPI_FLASH)
  (uint16_t)((1u << 0) | (1u << 2)), // QSPI: full images and in-place deltas
  {MOTA_QSPI_STORAGE_FLAGS, 0, 0, 0},
#elif defined(MOTA_INTERNAL_BOOTLOADER_UPDATE)
  (uint16_t)((1u << 0) | (1u << 2)), // internal staging: bootloader full image + app in-place delta
  {MOTA_BOOT_UPDATE_STORAGE_FLAGS, 0, 0, 0},
#else
  (uint16_t)(1u << 2),                      // internal flash: in-place deltas only
  {MOTA_BL_STORAGE_STAGE_CEILING, 0, 0, 0}, // GPREGRET2 selects the safe staging ceiling
#endif
};

// ---- `.mota` / EndF on-wire constants (mirror of src/helpers/ota/OtaFormat.h, C-friendly) ---------
static const uint8_t MAGIC[4]   = {'m', 'O', 'T', 'A'};
static const uint8_t TRAILER[5] = {'v', 'k', '4', '9', '6'};
static const uint8_t ENDF[4]    = {'E', 'n', 'd', 'F'};
static const uint8_t APRV[4]    = {'A', 'P', 'R', 'V'};
#define ENDF_LEN   56u  // fixed trailer: marker(4)+body_len(4)+body_hash8(8)+fw_ver(4)+target(4)+hw_id(32)
#define MOTA_MFL   197u // fixed manifest-minus-leaves length (head 89 + base_hash 8 + signer 32 + sig 64 + approval 4)
#define MFLAG_FULL 0x01u
#define MFLAG_SIGNED  0x02u
#define MFLAG_BOOTLOADER 0x04u
#define CODEC_FULL    0u
#define CODEC_INPLACE 2u
#define PAGE          MOTA_NRF52_FLASH_PAGE

// ---- platform flash / settings / gpregret abstraction --------------------------------------------
#ifdef OTA_DELTA_HOST_TEST
extern void     otah_read(uint32_t addr, void *dst, uint32_t n);
extern void     otah_erase(uint32_t page_addr);
extern void     otah_write_words(uint32_t addr, const uint32_t *src, uint32_t nwords);
extern uint32_t otah_gpregret_get(void);
extern void     otah_gpregret_set(uint32_t v);
extern uint32_t otah_gpregret2_get(void);
extern void     otah_gpregret2_set(uint32_t v);
extern uint16_t otah_crc16(uint32_t addr, uint32_t len);
extern void     otah_settings_commit(uint16_t bank0, uint16_t crc, uint32_t size);
#if defined(MOTA_SD_CARD)
extern void     otah_sd_auth_read(void *dst, uint32_t len);
extern void     otah_sd_auth_consume(void);
#endif
#if defined(MOTA_INTERNAL_BOOTLOADER_UPDATE) || defined(MOTA_SD_BOOTLOADER_UPDATE)
extern int      otah_crc_bound_app_size(uint32_t *size);
#endif
#if defined(MOTA_BOOTLOADER_UPDATE_ENABLED)
extern int      otah_installed_boot_info(bootloader_image_info_t *info);
extern uint16_t otah_runtime_softdevice_fwid(void);
#endif
  #define APP_BASE MOTA_NRF52_APP_BASE
static void fl_read(uint32_t a, void *d, uint32_t n) {
  otah_read(a, d, n);
}
static void fl_erase(uint32_t page) {
  otah_erase(page);
}
static void fl_write_words(uint32_t a, const uint32_t *s, uint32_t nw) {
  otah_write_words(a, s, nw);
}
static uint32_t gpregret_get(void) {
  return otah_gpregret_get();
}
static void gpregret_set(uint32_t v) {
  otah_gpregret_set(v);
}
static uint32_t gpregret2_get(void) {
  return otah_gpregret2_get();
}
static void gpregret2_set(uint32_t v) {
  otah_gpregret2_set(v);
}
static uint16_t crc16_region(uint32_t a, uint32_t len) {
  return otah_crc16(a, len);
}
#else
  #include "nrf.h"
  #include "nrfx_nvmc.h"
  #include "crc16.h"
  #include "boards.h"
  #include "bootloader_types.h"
  #include "bootloader_settings.h"
  #include "dfu_types.h"
  #if defined(MOTA_BOOTLOADER_UPDATE_ENABLED)
    #include "usb/uf2/uf2cfg.h"
    #include "nrf_mbr.h"
    #include "nrf_sdm.h"
    #include "ota_softdevice_fwid.h"
  #endif
  #define APP_BASE           ((uint32_t)DFU_BANK_0_REGION_START)
// Read flash through a VOLATILE pointer. In-place apply WRITES flash (nrfx_nvmc_words_write) and then
// READS IT BACK here (decode readback + the post-apply sha256). Those touch the same flash through two
// different pointer provenances (an integer-cast read pointer vs the nrfx write), so whole-program -flto
// alias analysis concludes they can't alias and caches/reorders a STALE read - the post-check then hashes
// pre-decode bytes -> mismatch -> apply silently refused. (-fno-strict-aliasing does NOT help: this is
// provenance, not type-based aliasing.) The host harness can't reproduce it: there read+write hit the
// same C array, an obvious alias. volatile forces the actual load each time.
static void fl_read(uint32_t a, void *d, uint32_t n) {
  const volatile uint8_t *s = (const volatile uint8_t *)(uintptr_t)a;
  uint8_t                *o = (uint8_t *)d;
  for (uint32_t i = 0; i < n; i++) {
    o[i] = s[i];
  }
}
static void fl_erase(uint32_t page) {
  nrfx_nvmc_page_erase(page);
}
static void fl_write_words(uint32_t a, const uint32_t *s, uint32_t nw) {
  nrfx_nvmc_words_write(a, s, nw);
}
static uint32_t gpregret_get(void) {
  return NRF_POWER->GPREGRET;
}
static void gpregret_set(uint32_t v) {
  NRF_POWER->GPREGRET = v;
}
// Diagnostic: stash an apply bail/progress code in GPREGRET2 (retained across the boot to the app, which
// reads it back). SD is off in the bootloader, so a direct write is fine.
static uint32_t gpregret2_get(void) {
  return NRF_POWER->GPREGRET2;
}
static void gpregret2_set(uint32_t v) {
  NRF_POWER->GPREGRET2 = v;
}
static uint16_t crc16_region(uint32_t a, uint32_t len) {
  return crc16_compute((const uint8_t *)(uintptr_t)a, len, NULL);
}
static void otah_settings_commit(uint16_t bank0, uint16_t crc, uint32_t size) {
  bootloader_settings_t        s;
  const bootloader_settings_t *cur;
  bootloader_util_settings_get(&cur);
  memcpy(&s, cur, sizeof(s));
  s.bank_0      = bank0;
  s.bank_0_crc  = crc;
  s.bank_0_size = size;
  nrfx_nvmc_page_erase(BOOTLOADER_SETTINGS_ADDRESS);
  nrfx_nvmc_words_write(BOOTLOADER_SETTINGS_ADDRESS, (const uint32_t *)&s, sizeof(s) / 4);
}
  #define BANK_VALID_APP_V   0x01u
  #define BANK_INVALID_APP_V 0xFFu
#endif

#if (defined(MOTA_SD_BOOTLOADER_UPDATE) && defined(MOTA_QSPI_BOOTLOADER_UPDATE)) || \
  (defined(MOTA_SD_BOOTLOADER_UPDATE) && defined(MOTA_INTERNAL_BOOTLOADER_UPDATE)) || \
  (defined(MOTA_QSPI_BOOTLOADER_UPDATE) && defined(MOTA_INTERNAL_BOOTLOADER_UPDATE))
  #error "Select exactly one bootloader-update staging backend"
#endif

#if defined(MOTA_BOOTLOADER_UPDATE_ENABLED)
  #if !defined(USB_DESC_VID) || !defined(USB_DESC_UF2_PID) || !defined(DEVICE_NAME)
    #error "Bootloader update requires USB_DESC_VID, USB_DESC_UF2_PID, and DEVICE_NAME identity"
  #endif
  #if !defined(OTA_DELTA_HOST_TEST) && !defined(NRF52840_XXAA)
    #error "App-preserving bootloader update requires the nRF52840 1 MiB flash layout"
  #endif
  #define BOOT_UPDATE_BOARD_ID (((uint32_t)USB_DESC_VID << 16) | USB_DESC_UF2_PID)
  #if defined(MOTA_INTERNAL_BOOTLOADER_UPDATE) || defined(MOTA_SD_BOOTLOADER_UPDATE)
    #define APP_APPLY_END MOTA_NRF52_APP_END
  #else
    #define APP_APPLY_END MOTA_NRF52_BL_SCRATCH_START
  #endif
#endif

#if defined(MOTA_SD_BOOTLOADER_UPDATE)
  #if !defined(MOTA_SD_CARD)
    #error "MOTA_SD_BOOTLOADER_UPDATE requires MOTA_SD_CARD"
  #endif
#elif defined(MOTA_QSPI_BOOTLOADER_UPDATE)
  #if !defined(MOTA_QSPI_FLASH)
    #error "MOTA_QSPI_BOOTLOADER_UPDATE requires MOTA_QSPI_FLASH"
  #endif
  #if !defined(USB_DESC_VID) || USB_DESC_VID != 0x2886 || \
    (!defined(USB_DESC_UF2_PID) || (USB_DESC_UF2_PID != 0x0044 && USB_DESC_UF2_PID != 0x0045))
    #error "MOTA_QSPI_BOOTLOADER_UPDATE is supported only on XIAO nRF52840 / Sense"
  #endif
#elif defined(MOTA_INTERNAL_BOOTLOADER_UPDATE)
  #if defined(MOTA_QSPI_FLASH) || defined(MOTA_SD_CARD)
    #error "MOTA_INTERNAL_BOOTLOADER_UPDATE requires internal-only OTA storage"
  #endif
#else
  #define APP_APPLY_END MOTA_NRF52_APP_END
#endif
#ifdef OTA_DELTA_HOST_TEST
  #define BANK_VALID_APP_V   0x01u
  #define BANK_INVALID_APP_V 0xFFu
#endif

#include "sha256.h"

// The application watchdog survives the NVIC reset used to enter this
// bootloader. Normal BLE/USB DFU reloads it from bootloader.c's event loop,
// while the in-place delta applier runs outside that loop. Keep every enabled
// reload channel alive throughout the potentially long scan/hash/apply path.
// A host test has no hardware watchdog, so this becomes a no-op there.
static void inherited_watchdog_feed(void) {
#ifndef OTA_DELTA_HOST_TEST
  static uint8_t external_feed_divider;

  if (NRF_WDT->RUNSTATUS != 0) {
    const uint32_t enabled_channels = NRF_WDT->RREN & 0xFFu;
    for (uint8_t channel = 0; channel < 8; channel++) {
      if ((enabled_channels & (1u << channel)) != 0) {
        NRF_WDT->RR[channel] = WDT_RR_RR_Reload;
      }
    }
  }

  // The external watchdog pulse is deliberately less frequent than the cheap
  // internal reload. Hashing calls this checkpoint every 256 bytes; pulsing on
  // every call would add several seconds to a large update.
  if ((external_feed_divider++ & 0x7FU) == 0) {
    board_watchdog_feed();
  }
#endif
}

static uint32_t rd_u32(const uint8_t *p) {
  return (uint32_t)p[0] | ((uint32_t)p[1] << 8) | ((uint32_t)p[2] << 16) | ((uint32_t)p[3] << 24);
}

#if defined(MOTA_BOOTLOADER_UPDATE_ENABLED)
static uint16_t rd_u16(const uint8_t *p) {
  return (uint16_t)p[0] | ((uint16_t)p[1] << 8);
}
#endif

#if defined(MOTA_SD_CARD)
static uint32_t g_sd_first_sector;
static uint32_t g_sd_total_size;
static mota_sd_auth_t g_sd_auth;
static int g_sd_auth_loaded;

// Copy the retained authorization into bootloader-owned RAM and consume it
// before the first access to removable media.  Thus every trigger is one-shot,
// including invalid records and SD failures.  A reset/power cut cannot retry.
static int sd_auth_take(uint8_t purpose, uint8_t format_ver) {
#ifdef OTA_DELTA_HOST_TEST
  otah_sd_auth_read(&g_sd_auth, sizeof(g_sd_auth));
  otah_sd_auth_consume();
#else
  const volatile uint8_t *src =
    (const volatile uint8_t *)(uintptr_t)MOTA_SD_AUTH_ADDRESS;
  uint8_t *dst = (uint8_t *)&g_sd_auth;
  for (uint32_t i = 0; i < sizeof(g_sd_auth); i++) {
    dst[i] = src[i];
  }
  volatile uint32_t *words =
    (volatile uint32_t *)(uintptr_t)MOTA_SD_AUTH_ADDRESS;
  for (uint32_t i = 0; i < sizeof(g_sd_auth) / sizeof(uint32_t); i++) {
    words[i] = 0u;
  }
  __DMB();
  __DSB();
#endif
  g_sd_auth_loaded = mota_sd_auth_valid(&g_sd_auth, purpose, format_ver);
  return g_sd_auth_loaded;
}

// In an SD build, patch/container offsets are relative to the first sector
// named by the consumed retained-RAM authorization. Internal-flash builds
// continue to use absolute flash addresses, preserving the existing path.
static int staged_read(uint32_t address_or_offset, void *dst, uint32_t len) {
  if (address_or_offset > g_sd_total_size ||
      len > g_sd_total_size - address_or_offset) {
    return 0;
  }
  return ota_sd_read_bytes(g_sd_first_sector, address_or_offset, dst, len) ? 1 : 0;
}
#elif defined(MOTA_QSPI_FLASH)
static uint32_t g_qspi_total_size;
static int      g_qspi_source;

static int staged_read(uint32_t address_or_offset, void *dst, uint32_t len) {
  if (g_qspi_source) {
    if ((uint64_t)address_or_offset + len > g_qspi_total_size) {
      return 0;
    }
    return ota_qspi_read(address_or_offset, dst, len) ? 1 : 0;
  }
  fl_read(address_or_offset, dst, len);
  return 1;
}
#else
static int staged_read(uint32_t address_or_offset, void *dst, uint32_t len) {
  fl_read(address_or_offset, dst, len);
  return 1;
}
#endif

// Tiny bounds-checked cursor so the manifest is parsed by reading each field by name in order, instead of
// hand-computed offsets (mirrors MeshCore's OtaByteIO.h ByteReader). Any over-read flips ok=0.
typedef struct {
  const uint8_t *p;
  uint32_t       len, n;
  int            ok;
} br_t;
static uint8_t br_u8(br_t *r) {
  if (r->ok && (uint64_t)r->n + 1 <= r->len) {
    return r->p[r->n++];
  }
  r->ok = 0;
  return 0;
}
static uint32_t br_u32(br_t *r) {
  if (r->ok && (uint64_t)r->n + 4 <= r->len) {
    uint32_t v = rd_u32(r->p + r->n);
    r->n += 4;
    return v;
  }
  r->ok = 0;
  return 0;
}
static const uint8_t *br_take(br_t *r, uint32_t k) {
  if (r->ok && (uint64_t)r->n + k <= r->len) {
    const uint8_t *x = r->p + r->n;
    r->n += k;
    return x;
  }
  r->ok = 0;
  return NULL;
}
static void br_skip(br_t *r, uint32_t k) {
  if (r->ok && (uint64_t)r->n + k <= r->len) {
    r->n += k;
  } else {
    r->ok = 0;
  }
}

static void sha256_region(uint32_t addr, uint32_t len, uint8_t out[32]) {
  sha256_ctx_t c;
  sha256_init(&c);
  uint8_t buf[256];
  while (len) {
    inherited_watchdog_feed();
    uint32_t n = len < sizeof(buf) ? len : sizeof(buf);
    fl_read(addr, buf, n);
    sha256_update(&c, buf, n);
    addr += n;
    len -= n;
  }
  sha256_final(&c, out);
}

// ---- coherent single-page write-back cache (in-place reads back shifted data it just wrote) -------
static uint8_t  g_cache[PAGE] __attribute__((aligned(4)));
static uint32_t g_cache_page; // page-aligned addr; 0 == INVALID (no app page is at 0)
static int      g_cache_dirty;

static void cache_flush(void) {
  if (!g_cache_page) {
    return;
  }
  if (g_cache_dirty) {
    inherited_watchdog_feed();
    fl_erase(g_cache_page);
    inherited_watchdog_feed();
    fl_write_words(g_cache_page, (const uint32_t *)g_cache, PAGE / 4);
  }
  g_cache_page  = 0;
  g_cache_dirty = 0;
}
static void cache_use(uint32_t page) {
  if (g_cache_page == page) {
    return;
  }
  cache_flush();
  g_cache_page  = page;
  g_cache_dirty = 0;
  fl_read(page, g_cache, PAGE);
}
static void cread(uint32_t addr, uint8_t *dst, uint32_t n) { // coherent read (overlay dirty page)
  fl_read(addr, dst, n);
  if (g_cache_page && g_cache_dirty) {
    uint32_t cs = g_cache_page, ce = g_cache_page + PAGE, a = addr, e = addr + n;
    uint32_t os = a > cs ? a : cs, oe = e < ce ? e : ce;
    if (os < oe) {
      memcpy(dst + (os - a), g_cache + (os - cs), oe - os);
    }
  }
}
static void cwrite(uint32_t addr, const uint8_t *src, uint32_t n) {
  while (n) {
    uint32_t page = addr & ~(PAGE - 1), off = addr - page, chunk = PAGE - off;
    if (chunk > n) {
      chunk = n;
    }
    cache_use(page);
    memcpy(g_cache + off, src, chunk);
    g_cache_dirty = 1;
    addr += chunk;
    src += chunk;
    n -= chunk;
  }
}
static void cerase(uint32_t addr, uint32_t n) { // detools calls this page-aligned
  while (n) {
    uint32_t page = addr & ~(PAGE - 1), off = addr - page, step = PAGE - off;
    if (step > n) {
      step = n;
    }
    if (g_cache_page == page) {
      memset(g_cache, 0xFF, PAGE);
      g_cache_dirty = 1;
    } else {
      fl_erase(page);
    }
    addr += step;
    n -= step;
  }
}

// ---- detools in-place callbacks (region addresses are 0-based; base sits at workspace offset 0) ---
struct apply_ctx {
  uint32_t patch_addr, patch_len, patch_pos;
  uint32_t ws_lo, ws_hi; // workspace = [ws_lo, ws_hi); ws_hi == mota start (never written)
  int      step;
};
static int dt_ws_addr(const struct apply_ctx *c, uintptr_t off, size_t n, uint32_t *addr) {
  uint32_t span = c->ws_hi - c->ws_lo;
  if (off > UINT32_MAX || n > UINT32_MAX) {
    return 0;
  }
  uint32_t o = (uint32_t)off, z = (uint32_t)n;
  if (o > span || z > span - o) {
    return 0;
  }
  *addr = c->ws_lo + o;
  return 1;
}
static int dt_mr(void *a, void *dst, uintptr_t src, size_t n) {
  struct apply_ctx *c = a;
  uint32_t          addr;
  if (!dt_ws_addr(c, src, n, &addr)) {
    return -DETOOLS_IO_FAILED;
  }
  inherited_watchdog_feed();
  cread(addr, dst, n);
  return DETOOLS_OK;
}
static int dt_mw(void *a, uintptr_t dst, void *src, size_t n) {
  struct apply_ctx *c = a;
  uint32_t          addr;
  if (!dt_ws_addr(c, dst, n, &addr)) {
    return -DETOOLS_IO_FAILED;
  }
  inherited_watchdog_feed();
  cwrite(addr, (const uint8_t *)src, n);
  return DETOOLS_OK;
}
static int dt_me(void *a, uintptr_t addr0, size_t n) {
  struct apply_ctx *c = a;
  uint32_t          addr;
  if (!dt_ws_addr(c, addr0, n, &addr)) {
    return -DETOOLS_IO_FAILED;
  }
  inherited_watchdog_feed();
  cerase(addr, n);
  return DETOOLS_OK;
}
static int dt_ss(void *a, int s) {
  ((struct apply_ctx *)a)->step = s;
  return DETOOLS_OK;
}
static int dt_sg(void *a, int *s) {
  *s = ((struct apply_ctx *)a)->step;
  return DETOOLS_OK;
}
static int dt_pr(void *a, uint8_t *dst, size_t n) {
  struct apply_ctx *c = a;
  if (n > UINT32_MAX || c->patch_pos > c->patch_len || (uint32_t)n > c->patch_len - c->patch_pos) {
    return -DETOOLS_IO_FAILED;
  }
  inherited_watchdog_feed();
  if (!staged_read(c->patch_addr + c->patch_pos, dst, (uint32_t)n)) {
    return -DETOOLS_IO_FAILED;
  }
  c->patch_pos += (uint32_t)n;
  return DETOOLS_OK;
}

// Decode the five unsigned sizes in an in-place detools header without starting the decoder. This lets
// us reject impossible flash geometry while the running application and its boot settings are intact.
static int dt_header_u32(uint32_t addr, uint32_t len, uint32_t *pos, uint32_t *out) {
  uint8_t b;
  if (*pos >= len) {
    return 0;
  }
  if (!staged_read(addr + (*pos)++, &b, 1)) {
    return 0;
  }
  uint32_t v     = b & 0x3Fu;
  uint32_t shift = 6;
  while (b & 0x80u) {
    if (*pos >= len) {
      return 0;
    }
    if (!staged_read(addr + (*pos)++, &b, 1)) {
      return 0;
    }
    uint32_t bits = b & 0x7Fu;
    if (shift >= 32 || bits > (UINT32_MAX >> shift)) {
      return 0;
    }
    v |= bits << shift;
    shift += 7;
  }
  if (v > 0x7FFFFFFFu) {
    return 0; // detools stores header sizes in signed int
  }
  *out = v;
  return 1;
}

// ---- `.mota` parse (fixed fields only) + EndF base location --------------------------------------
struct mota_min {
  uint32_t total, image_size, payload_size, payload_addr, approval_addr;
  uint32_t target_id, fw_version, block_count;
  uint8_t  base_hash[8], image_hash[32], hw_id[32];
  uint8_t  codec_id, is_full, approved, format_ver, flags, hash_algo, block_size_log2;
};
static int dt_geometry_ok(const struct mota_min *m, uint32_t body_len, uint32_t ws_span) {
  uint8_t  fixed;
  uint32_t p = 1, memory, segment, shift, from, to;
  if (m->payload_size < 2) {
    return 0;
  }
  if (!staged_read(m->payload_addr, &fixed, 1)) {
    return 0;
  }
  if (((fixed >> 4) & 0x07u) != 1u) {
    return 0; // detools PATCH_TYPE_IN_PLACE
  }
  if (!dt_header_u32(m->payload_addr, m->payload_size, &p, &memory) ||
      !dt_header_u32(m->payload_addr, m->payload_size, &p, &segment) ||
      !dt_header_u32(m->payload_addr, m->payload_size, &p, &shift) ||
      !dt_header_u32(m->payload_addr, m->payload_size, &p, &from) ||
      !dt_header_u32(m->payload_addr, m->payload_size, &p, &to)) {
    return 0;
  }
  if (memory == 0 || memory > ws_span || segment != PAGE || shift > memory || shift % segment != 0 ||
      from > memory - shift || to > memory) {
    return 0;
  }
  if (body_len > UINT32_MAX - ENDF_LEN || from != body_len + ENDF_LEN) {
    return 0;
  }
  return to == m->image_size && to <= ws_span;
}
static int parse_mota_at(uint32_t addr, uint32_t limit, struct mota_min *o) {
  uint8_t b[8 + MOTA_MFL]; // MAGIC+total + the whole fixed manifest-minus-leaves
  if (addr > limit) {
    return 0;
  }
  uint32_t avail = limit - addr;
  uint32_t hdr   = avail < sizeof(b) ? avail : sizeof(b);
  if (hdr < 8 + MOTA_MFL) {
    return 0; // need the whole fixed manifest in `b` (trailer read separately)
  }
  if (!staged_read(addr, b, hdr)) {
    return 0;
  }
  if (memcmp(b, MAGIC, 4) != 0) {
    return 0;
  }
  uint32_t total = rd_u32(b + 4);
  if (total < 8 + MOTA_MFL + 5 || total > avail) {
    return 0;
  }
  uint8_t tr[5];
  if (!staged_read(addr + total - 5, tr, 5)) {
    return 0;
  }
  if (memcmp(tr, TRAILER, 5) != 0) {
    return 0;
  }

  // Fixed-layout manifest - every field at a constant offset; base_hash/signer/signature are always
  // present (zero-filled when not applicable), so there are no conditionals (docs/ota_protocol.md Section 4).
  br_t r = {b, hdr, 0, 1};
  br_skip(&r, 4 + 4); // MAGIC + MOTA_TOTAL_SIZE (already validated above)
  o->format_ver = br_u8(&r);
  if (o->format_ver != 2
#if defined(MOTA_BOOTLOADER_UPDATE_ENABLED)
      && o->format_ver != 3
#endif
  ) {
    return 0;
  }
  o->flags     = br_u8(&r);
  o->hash_algo = br_u8(&r);
  o->target_id = br_u32(&r);
  o->fw_version = br_u32(&r);
  o->image_size   = br_u32(&r);
  o->payload_size = br_u32(&r);
  o->block_size_log2 = br_u8(&r);
  br_skip(&r, 4); // merkle_root (already verified by the approving app)
  const uint8_t *ih = br_take(&r, 32);
  if (ih) {
    memcpy(o->image_hash, ih, 32);
  }
  o->codec_id = br_u8(&r);
  const uint8_t *hw = br_take(&r, 32);
  if (hw) {
    memcpy(o->hw_id, hw, 32);
  }
  o->is_full        = (o->flags & MFLAG_FULL) ? 1 : 0;
  const uint8_t *bh = br_take(&r, 8);
  if (bh) {
    memcpy(o->base_hash, bh, 8); // base_hash (zero for full)
  }
  br_skip(&r, 32 + 64);          // signer pubkey + signature (zero when unsigned)
  if (!r.ok) {
    return 0;
  }
  o->approval_addr  = addr + r.n;
  const uint8_t *ap = br_take(&r, 4);
  o->approved       = (ap && memcmp(ap, APRV, 4) == 0) ? 1 : 0;
  if (o->block_size_log2 == 0 || o->block_size_log2 > 24 || o->payload_size == 0) {
    return 0;
  }
  // Do all variable-length geometry in 64 bits. With corrupt 32-bit sizes, the old ceil-divide,
  // leaf-count multiplication, and final sum could wrap together into a small self-consistent `total`,
  // leaving payload_addr far outside the staged container.
  uint64_t bs  = 1ull << o->block_size_log2;
  uint64_t bc  = ((uint64_t)o->payload_size + bs - 1) / bs;
  uint64_t off = (uint64_t)r.n + bc * 4u; // leaves[] then payload
  if (bc == 0 || bc > 0xFFFFu || off + o->payload_size + 5u != total) {
    return 0;                             // payload must end exactly at the trailer
  }
  o->block_count = (uint32_t)bc;
  o->payload_addr = addr + (uint32_t)off; // exact-total check proves off < total <= avail
  o->total        = total;
  return 1;
}

// Scan page boundaries for a valid `.mota`, returning the HIGHEST one below the selected ceiling.
//
// Direction matters for safety. The app stages the container bottom-aligned and writes [write_start,
// stage_ceiling) contiguously (0xFF-padding the tail), so the *current* `.mota` is always the
// highest in flash; a leftover from a prior, differently-sized fetch sits strictly BELOW it (a larger
// new fetch overwrites everything from its lower start up to the ceiling). Scanning top-down therefore
// returns the current container and never stops on a stale one - and the caller's APRV check is applied
// to THAT (highest) container only, so a stale lower `.mota` is never applied even if it is still
// approved. Exact bottom-alignment also prevents a mismatched ceiling hint from selecting a package.
// (EndF is the mirror image: the app image grows up from APP_BASE, so the current trailer is
// the LOWEST valid marker and find_body_len scans bottom-up. Each marker is scanned from the end where
// the current one is encountered first.)
#define MOTA_MIN_LEN (8 + MOTA_MFL + 5)
static uint32_t scan_mota(struct mota_min *o, uint32_t stage_ceiling) {
#if defined(MOTA_SD_CARD)
  (void)stage_ceiling;
  if (!g_sd_auth_loaded || !ota_sd_init()) {
    return 0;
  }
  const uint32_t first = g_sd_auth.first_sector;
  const uint32_t total = g_sd_auth.container_total;
  if (total < MOTA_MIN_LEN) {
    return 0;
  }
  g_sd_first_sector = first;
  g_sd_total_size   = total;
  if (!parse_mota_at(0, g_sd_total_size, o) || o->total != total) {
    return 0;
  }
  return 1; // nonzero sentinel; container begins at file offset 0
#else
  #if defined(MOTA_QSPI_FLASH)
  if (g_qspi_source) {
    if (!ota_qspi_init()) {
      return 0;
    }
    uint32_t capacity = ota_qspi_capacity();
    if (capacity < MOTA_MIN_LEN) {
      return 0;
    }
    g_qspi_total_size = capacity; // permits the initial bounded header/manifest reads
    if (!parse_mota_at(0, capacity, o)) {
      return 0;
    }
    g_qspi_total_size = o->total;
    return 1; // external container begins at offset zero
  }
  #endif
  if (stage_ceiling != MOTA_NRF52_STAGE_CEILING_LEGACY && stage_ceiling != MOTA_NRF52_STAGE_CEILING_EXPANDED
#if defined(MOTA_QSPI_BOOTLOADER_UPDATE)
      && stage_ceiling != MOTA_NRF52_BL_SCRATCH_START
#endif
  ) {
    return 0;
  }
  uint32_t top = (stage_ceiling - MOTA_MIN_LEN) & ~(PAGE - 1);
  for (uint32_t a = top + PAGE; a > APP_BASE;) { // walk page boundaries high -> low
    a -= PAGE;
    inherited_watchdog_feed();
    uint8_t m4[4];
    fl_read(a, m4, 4);
    if (memcmp(m4, MAGIC, 4) == 0 && parse_mota_at(a, stage_ceiling, o) &&
        ((stage_ceiling - o->total) & ~(PAGE - 1u)) == a) {
      return a;
    }
  }
  return 0;
#endif
}

// Locate the running image's EndF trailer (= body_len) by scanning bottom-up for the self-validating
// marker (mirrors the app's FirmwareInfo::find_self_firmware). We deliberately do NOT trust
// bootloader_settings.bank_0_size: the UF2 flasher does not set it to the exact EndF-inclusive image
// size, so trusting it makes a freshly UF2-flashed app fail the base check and silently refuse its first
// OTA update. Bottom-up returns the CURRENT (lowest) trailer; a stale one from a prior larger image sits
// above it and is never reached. No hash check is needed here: the caller immediately recomputes
// sha256(body) and compares it to the delta's base_hash, so a (vanishingly unlikely) coincidental "EndF"
// just fails that gate and the update is refused - never misapplied. Byte-by-byte (like the app) so no
// body_len alignment is assumed; the scan stops at the first match (the current image's trailer).
static int find_body_len(uint32_t app_limit, uint32_t *body_len_out) {
  if (app_limit <= APP_BASE) {
    return 0;
  }
  for (uint32_t off = 0; off + ENDF_LEN <= app_limit - APP_BASE; off++) {
    if ((off & (PAGE - 1u)) == 0) {
      inherited_watchdog_feed();
    }
    uint8_t e[8];
    fl_read(APP_BASE + off, e, 8); // marker(4) + body_len(4)
    if (memcmp(e, ENDF, 4) == 0 && rd_u32(e + 4) == off) {
      *body_len_out = off;
      return 1;
    }
  }
  return 0;
}

#if defined(MOTA_INTERNAL_BOOTLOADER_UPDATE) || defined(MOTA_SD_BOOTLOADER_UPDATE)
// An internal destination is safe only when the live application ends before
// it. Do not trust a marker-shaped byte sequence alone: bind the first EndF to
// the body by recomputing its truncated SHA-256 before any staged page is
// erased or rewritten.
bool ota_delta_live_app_fits_below(uint32_t limit) {
  uint32_t body_len;
  if (limit <= APP_BASE + ENDF_LEN || !find_body_len(limit, &body_len) || body_len == 0 ||
      body_len > limit - APP_BASE - ENDF_LEN) {
    return 0;
  }
  uint8_t expected[8];
  uint8_t actual[32];
  fl_read(APP_BASE + body_len + 8u, expected, sizeof(expected));
  sha256_region(APP_BASE, body_len, actual);
  if (memcmp(actual, expected, sizeof(expected)) != 0) {
    return 0;
  }

  // A CRC-bound bootloader setting can cover bytes beyond the first valid
  // EndF (for example padding in a full DFU image). Those bytes are part of the
  // live application even though EndF itself is earlier, so scratch must stay
  // above the complete recorded bank or a power cut would make the old app
  // fail its next boot CRC.
  uint32_t recorded_size = 0u;
#ifdef OTA_DELTA_HOST_TEST
  const int crc_bound = otah_crc_bound_app_size(&recorded_size);
#else
  const bootloader_settings_t *settings;
  bootloader_util_settings_get(&settings);
  const int crc_bound = settings && settings->bank_0 == BANK_VALID_APP_V &&
                        settings->bank_0_crc != 0u;
  recorded_size = crc_bound ? settings->bank_0_size : 0u;
#endif
  const uint32_t endf_size = body_len + ENDF_LEN;
  return !crc_bound || (recorded_size >= endf_size && recorded_size <= limit - APP_BASE);
}
#endif

static int clear_approval(const struct mota_min *o) {
#if defined(MOTA_SD_CARD)
  // This backend has no raw-sector write primitive, so APRV remains on the
  // card. The retained authorization and GPREGRET were consumed before any
  // validation or scratch erase, making the file inert across a normal reset.
  // A running app may re-arm it only after authenticating a new update command.
  (void)o;
  return 1;
#elif defined(MOTA_QSPI_FLASH)
  if (g_qspi_source) {
    uint8_t z[4] = {0, 0, 0, 0};
    return ota_qspi_write(o->approval_addr, z, sizeof(z)) ? 1 : 0;
  }
  uint8_t z[4] = {0, 0, 0, 0};
  cwrite(o->approval_addr, z, 4);
  cache_flush();
  return 1;
#else
  uint8_t z[4] = {0, 0, 0, 0};
  cwrite(o->approval_addr, z, 4);
  cache_flush();
  return 1;
#endif
}

static bool finish_apply(bool result);

#if defined(MOTA_SD_CARD) || defined(MOTA_QSPI_FLASH) || defined(MOTA_BOOTLOADER_UPDATE_ENABLED)
static int sha256_staged_region(uint32_t offset, uint32_t len, uint8_t out[32]) {
  sha256_ctx_t c;
  sha256_init(&c);
  uint8_t buf[256];
  while (len) {
    inherited_watchdog_feed();
    uint32_t n = len < sizeof(buf) ? len : sizeof(buf);
    if (!staged_read(offset, buf, n)) {
      return 0;
    }
    sha256_update(&c, buf, n);
    offset += n;
    len -= n;
  }
  sha256_final(&c, out);
  return 1;
}
#endif

#if defined(MOTA_SD_CARD)
// Hash the exact authorized container while normalizing the sole mutable field
// (approval) to zero.  The application computes the same digest in the pass
// that authenticates the signed manifest, leaf table, and payload.  Binding
// this full digest also protects delta bytecode, not only its final image hash.
static int sd_authorized_container_valid(void) {
  if (!g_sd_auth_loaded || g_sd_auth.container_total != g_sd_total_size ||
      g_sd_total_size < MOTA_SD_AUTH_APPROVAL_OFFSET + MOTA_SD_AUTH_APPROVAL_LEN) {
    return 0;
  }
  sha256_ctx_t c;
  sha256_init(&c);
  uint8_t buf[256];
  uint32_t offset = 0;
  while (offset < g_sd_total_size) {
    inherited_watchdog_feed();
    uint32_t n = g_sd_total_size - offset;
    if (n > sizeof(buf)) {
      n = sizeof(buf);
    }
    if (!staged_read(offset, buf, n)) {
      return 0;
    }
    // APRV is fixed at bytes 201..204, wholly inside the first 256-byte
    // chunk. The entry bounds above guarantee those bytes exist.
    if (offset == 0u) {
      memset(buf + MOTA_SD_AUTH_APPROVAL_OFFSET, 0,
             MOTA_SD_AUTH_APPROVAL_LEN);
    }
    sha256_update(&c, buf, n);
    offset += n;
  }
  uint8_t digest[32];
  sha256_final(&c, digest);
  return memcmp(digest, g_sd_auth.container_sha256, sizeof(digest)) == 0;
}
#endif

#if defined(MOTA_BOOTLOADER_UPDATE_ENABLED)
#if defined(MOTA_QSPI_BOOTLOADER_UPDATE) && USB_DESC_UF2_PID == 0x0044
static const uint8_t BOOT_UPDATE_HW_ID[32] = "XIAO_BL_28860044";
#elif defined(MOTA_QSPI_BOOTLOADER_UPDATE)
static const uint8_t BOOT_UPDATE_HW_ID[32] = "XIAO_BL_28860045";
#endif

#define BOOT_UPDATE_BLOCK_COUNT    40u
#define BOOT_UPDATE_PAYLOAD_OFFSET (8u + MOTA_MFL + (BOOT_UPDATE_BLOCK_COUNT * 4u))
#define BOOT_UPDATE_PACKAGE_SIZE   (BOOT_UPDATE_PAYLOAD_OFFSET + MOTA_NRF52_BL_SIZE + sizeof(TRAILER))
#if defined(MOTA_INTERNAL_BOOTLOADER_UPDATE)
  #define BOOT_UPDATE_RAW_START MOTA_NRF52_INTERNAL_BL_SLOT_START
#else
  #define BOOT_UPDATE_RAW_START MOTA_NRF52_BL_SCRATCH_START
#endif

typedef char boot_update_raw_start_must_be_page_aligned
  [((BOOT_UPDATE_RAW_START & (MOTA_NRF52_FLASH_PAGE - 1u)) == 0) ? 1 : -1];
typedef char boot_update_raw_image_must_end_before_bootloader
  [((BOOT_UPDATE_RAW_START + MOTA_NRF52_BL_SIZE) <= MOTA_NRF52_BL_START) ? 1 : -1];
typedef char boot_update_device_name_must_fit_manifest
  [(sizeof(DEVICE_NAME) <= BOOTLOADER_UPDATE_DEVICE_NAME_SIZE) ? 1 : -1];

#if defined(MOTA_SD_BOOTLOADER_UPDATE)
typedef char boot_update_sd_token_must_fit_first_scratch_page
  [(MOTA_SD_BOOT_TOKEN_LEN <= MOTA_NRF52_FLASH_PAGE) ? 1 : -1];

static int sd_boot_authorization_valid(const struct mota_min *m) {
  uint8_t token[MOTA_SD_BOOT_TOKEN_LEN];
  fl_read(MOTA_NRF52_BL_SCRATCH_START, token, sizeof(token));
  return m->total == BOOT_UPDATE_PACKAGE_SIZE &&
         mota_sd_boot_token_valid(token, m->total, m->image_hash);
}
#endif

#if defined(MOTA_INTERNAL_BOOTLOADER_UPDATE)
typedef char boot_update_internal_slot_end_must_be_page_aligned
  [((MOTA_NRF52_INTERNAL_BL_SLOT_END & (MOTA_NRF52_FLASH_PAGE - 1u)) == 0) ? 1 : -1];
typedef char boot_update_internal_package_must_bottom_align_at_slot_start
  [(((MOTA_NRF52_INTERNAL_BL_SLOT_END - BOOT_UPDATE_PACKAGE_SIZE) &
     ~(MOTA_NRF52_FLASH_PAGE - 1u)) == MOTA_NRF52_INTERNAL_BL_SLOT_START) ? 1 : -1];
typedef char boot_update_internal_raw_must_fit_shared_slot
  [((MOTA_NRF52_INTERNAL_BL_SLOT_START + MOTA_NRF52_BL_SIZE) <= MOTA_NRF52_INTERNAL_BL_SLOT_END) ? 1 : -1];
typedef char boot_update_internal_compaction_offset_must_fit_one_page
  [(BOOT_UPDATE_PAYLOAD_OFFSET > 0u && BOOT_UPDATE_PAYLOAD_OFFSET < MOTA_NRF52_FLASH_PAGE) ? 1 : -1];
typedef char boot_update_internal_source_must_fit_shared_slot
  [((MOTA_NRF52_INTERNAL_BL_SLOT_START + BOOT_UPDATE_PAYLOAD_OFFSET + MOTA_NRF52_BL_SIZE) <=
    MOTA_NRF52_INTERNAL_BL_SLOT_END) ? 1 : -1];
#endif

#ifndef OTA_DELTA_HOST_TEST
typedef char boot_update_start_must_match_dfu
  [(BOOTLOADER_ADDR_START == MOTA_NRF52_BL_START) ? 1 : -1];
typedef char boot_update_size_must_match_dfu
  [(DFU_BL_IMAGE_MAX_SIZE == MOTA_NRF52_BL_SIZE) ? 1 : -1];
#if defined(MOTA_SD_BOOTLOADER_UPDATE) || defined(MOTA_QSPI_BOOTLOADER_UPDATE)
typedef char boot_update_scratch_must_match_dfu
  [(BOOTLOADER_ADDR_NEW_RECEIVED == MOTA_NRF52_BL_SCRATCH_START) ? 1 : -1];
#endif
#endif

static int boot_update_expected_identity(uint8_t hw_id[32], uint32_t *target_id) {
#if defined(MOTA_QSPI_BOOTLOADER_UPDATE)
  memcpy(hw_id, BOOT_UPDATE_HW_ID, sizeof(BOOT_UPDATE_HW_ID));
  *target_id = BOOT_UPDATE_BOARD_ID;
  return 1;
#else
  static const uint8_t prefix[7] = {'N', 'R', 'F', '_', 'B', 'L', '_'};
  static const char    hex[16]   = "0123456789ABCDEF";
  static const char    name[]    = DEVICE_NAME;
  const uint32_t       name_len  = sizeof(name) - 1u;
  if (BOOT_UPDATE_BOARD_ID == 0 || BOOT_UPDATE_BOARD_ID == UINT32_MAX || name_len == 0 || name_len > 15u) {
    return 0;
  }
  memset(hw_id, 0, 32);
  memcpy(hw_id, prefix, sizeof(prefix));
  for (uint32_t i = 0; i < 8u; i++) {
    hw_id[7u + i] = (uint8_t)hex[(BOOT_UPDATE_BOARD_ID >> (28u - 4u * i)) & 0x0Fu];
  }
  hw_id[15] = '_';
  for (uint32_t i = 0; i < name_len; i++) {
    const uint8_t ch = (uint8_t)name[i];
    if (ch < 0x21u || ch > 0x7Eu) {
      return 0;
    }
    hw_id[16u + i] = ch;
  }
  uint8_t hash[32];
  sha256_ctx_t ctx;
  sha256_init(&ctx);
  sha256_update(&ctx, hw_id, 32);
  sha256_final(&ctx, hash);
  *target_id = rd_u32(hash);
  return *target_id != 0 && *target_id != UINT32_MAX;
#endif
}

static int boot_vectors_valid(const uint8_t vectors[8]) {
  const uint32_t initial_sp = rd_u32(vectors);
  const uint32_t reset      = rd_u32(vectors + 4);
  const uint32_t reset_addr = reset & ~1u;
  return (initial_sp & 7u) == 0 && initial_sp >= 0x20000000u && initial_sp <= 0x20040000u &&
         (reset & 1u) != 0 && reset_addr >= MOTA_NRF52_BL_START &&
         reset_addr < MOTA_NRF52_BL_START + MOTA_NRF52_BL_SIZE;
}

static int staged_vectors_valid(uint32_t payload_addr) {
  uint8_t vectors[8];
  return staged_read(payload_addr, vectors, sizeof(vectors)) && boot_vectors_valid(vectors);
}

static int boot_payload_integrity_valid(const struct mota_min *m) {
  uint8_t image_hash[32];
  // The running application verified the signed manifest, all leaves, and the
  // Merkle root before writing APRV. Recompute the full 256-bit image anchor
  // here; this covers every payload byte without duplicating the Merkle engine
  // in the size-constrained bootloader.
  return sha256_staged_region(m->payload_addr, m->payload_size, image_hash) &&
         memcmp(image_hash, m->image_hash, sizeof(image_hash)) == 0;
}

static int boot_update_policy_valid(const struct mota_min *m) {
  static const uint8_t zeros[8];
  uint8_t              expected_hw_id[32];
  uint32_t             expected_target_id;
  if (!boot_update_expected_identity(expected_hw_id, &expected_target_id)) {
    return 0;
  }
  return m->format_ver == 3 && m->flags == (MFLAG_FULL | MFLAG_SIGNED | MFLAG_BOOTLOADER) &&
         m->hash_algo == 0x12u && m->is_full && m->codec_id == CODEC_FULL &&
         m->target_id == expected_target_id && m->fw_version != 0 &&
         memcmp(m->hw_id, expected_hw_id, sizeof(m->hw_id)) == 0 &&
         memcmp(m->base_hash, zeros, sizeof(zeros)) == 0 && m->block_size_log2 == 10u &&
         m->block_count == BOOT_UPDATE_BLOCK_COUNT &&
         m->image_size == MOTA_NRF52_BL_SIZE && m->payload_size == MOTA_NRF52_BL_SIZE;
}

#ifdef OTA_DELTA_HOST_TEST
extern const uint8_t *otah_flash_pointer(uint32_t address, uint32_t len);
extern int            otah_mbr_copy_bl(uint32_t source, uint32_t word_count);
#endif

static const uint8_t *boot_image_pointer(uint32_t address) {
#ifdef OTA_DELTA_HOST_TEST
  return otah_flash_pointer(address, MOTA_NRF52_BL_SIZE);
#else
  return (const uint8_t *)(uintptr_t)address;
#endif
}

static int boot_image_caps_valid(const uint8_t *image) {
  static const uint8_t magic[8] = {MOTA_BL_MAGIC0, MOTA_BL_MAGIC1, MOTA_BL_MAGIC2, MOTA_BL_MAGIC3,
                                   MOTA_BL_MAGIC4, MOTA_BL_MAGIC5, MOTA_BL_MAGIC6, MOTA_BL_MAGIC7};
  // Scan every aligned match: a magic copy in a literal pool must not hide the
  // real capability marker later in the image. Count every structurally valid
  // privileged marker before requiring the sole marker to match this build's
  // exact storage profile; a second valid marker cannot hide behind a
  // different boot-update backend.
  uint32_t matches = 0;
  for (uint32_t off = 0; off + sizeof(mota_bl_info_t) <= MOTA_NRF52_BL_SIZE; off += 4u) {
    // The internal staged payload begins 365 bytes into its container, so its
    // base is deliberately unaligned even though marker offsets within the
    // raw image are 4-byte aligned. Read every field as bytes: even an enabled
    // Cortex-M UNALIGN_TRP cannot fault this validation path.
    const uint8_t *candidate = image + off;
    int magic_equal = 1;
    for (uint32_t i = 0; i < sizeof(magic); i++) {
      if (candidate[i] != magic[i]) {
        magic_equal = 0;
        break;
      }
    }
    const uint16_t apply_abi  = rd_u16(candidate + offsetof(mota_bl_info_t, apply_abi));
    const uint16_t codec_mask = rd_u16(candidate + offsetof(mota_bl_info_t, codec_mask));
    const uint8_t *storage    = candidate + offsetof(mota_bl_info_t, storage_flags);
    if (magic_equal && apply_abi >= 3u && apply_abi != UINT16_MAX &&
        (codec_mask & MOTA_BOOT_UPDATE_CODEC_MASK) == MOTA_BOOT_UPDATE_CODEC_MASK &&
        (storage[0] & MOTA_BL_STORAGE_BOOT_UPDATE) != 0u &&
        (storage[0] & (uint8_t)~MOTA_BL_STORAGE_KNOWN) == 0u &&
        (storage[1] | storage[2] | storage[3]) == 0u) {
      if (++matches > 1u || storage[0] != MOTA_BOOT_UPDATE_STORAGE_FLAGS) {
        return 0;
      }
    }
  }
  return matches == 1u;
}

static int installed_boot_info(bootloader_image_info_t *info) {
#ifdef OTA_DELTA_HOST_TEST
  return otah_installed_boot_info(info);
#else
  extern const bootloader_update_envelope_t bootloaderUpdateManifest;
  const bootloader_update_extension_t *ext = &bootloaderUpdateManifest.extension;
  if (!bootloader_extension_validate(ext)) {
    return 0;
  }
  info->boot_version = ext->boot_version;
  info->softdevice_family = ext->softdevice_family;
  info->softdevice_fwid = ext->softdevice_fwid;
  info->app_base = ext->app_base;
  info->layout_abi = ext->layout_abi;
  return 1;
#endif
}

static uint16_t runtime_softdevice_fwid(void) {
#ifdef OTA_DELTA_HOST_TEST
  return otah_runtime_softdevice_fwid();
#else
  return mota_runtime_softdevice_fwid_get();
#endif
}

static int boot_image_metadata_valid_at(uint32_t address,
                                        const struct mota_min *m) {
  const uint8_t *image = boot_image_pointer(address);
  bootloader_image_info_t candidate;
  bootloader_image_info_t installed;
#ifdef OTA_DELTA_HOST_TEST
  static const char expected_device_name[BOOTLOADER_UPDATE_DEVICE_NAME_SIZE] = DEVICE_NAME;
#else
  extern const bootloader_update_envelope_t bootloaderUpdateManifest;
  const char *expected_device_name = bootloaderUpdateManifest.manifest.device_name;
#endif
  if (!image || !m ||
      !bootloader_image_info(image, MOTA_NRF52_BL_START, MOTA_NRF52_BL_SIZE,
                             BOOT_UPDATE_BOARD_ID, expected_device_name, &candidate) ||
      !boot_image_caps_valid(image) || !installed_boot_info(&installed)) {
    return 0;
  }
  // Remote bootloader replacement cannot migrate the SoftDevice/application
  // layout.  Such recovery remains an explicit local USB/BLE/SWD operation.
  // The outer signed package version must describe the bytes themselves, and
  // every v2-to-v2 remote update is strictly monotonic.
  return candidate.softdevice_family == installed.softdevice_family &&
         candidate.softdevice_fwid == installed.softdevice_fwid &&
         candidate.app_base == installed.app_base &&
         candidate.layout_abi == installed.layout_abi &&
         candidate.softdevice_family == MOTA_SOFTDEVICE_FAMILY &&
         candidate.softdevice_fwid == runtime_softdevice_fwid() &&
         candidate.app_base == APP_BASE &&
         candidate.layout_abi == BOOTLOADER_UPDATE_LAYOUT_ABI &&
         candidate.boot_version == m->fw_version &&
         candidate.boot_version > installed.boot_version;
}

static int copy_bootloader_to_raw_source(const struct mota_min *m) {
  uint8_t readback[256];
  for (uint32_t off = 0; off < MOTA_NRF52_BL_SIZE; off += PAGE) {
    inherited_watchdog_feed();
    // Internal staging deliberately overlaps the destination. The payload is
    // BOOT_UPDATE_PAYLOAD_OFFSET bytes ahead (365 for the exact v3 profile).
    // Read the complete source page into RAM before erasing the lower
    // destination page; all future source bytes are above that page.
    if (!staged_read(m->payload_addr + off, g_cache, PAGE)) {
      return 0;
    }
    fl_erase(BOOT_UPDATE_RAW_START + off);
    inherited_watchdog_feed();
    fl_write_words(BOOT_UPDATE_RAW_START + off, (const uint32_t *)g_cache, PAGE / 4u);
    for (uint32_t page_off = 0; page_off < PAGE; page_off += sizeof(readback)) {
      inherited_watchdog_feed();
      fl_read(BOOT_UPDATE_RAW_START + off + page_off, readback, sizeof(readback));
      if (memcmp(readback, g_cache + page_off, sizeof(readback)) != 0) {
        return 0;
      }
    }
  }

  uint8_t hash[32];
  sha256_region(BOOT_UPDATE_RAW_START, MOTA_NRF52_BL_SIZE, hash);
  return memcmp(hash, m->image_hash, sizeof(hash)) == 0;
}

static int mbr_copy_bootloader(void) {
#ifdef OTA_DELTA_HOST_TEST
  return otah_mbr_copy_bl(BOOT_UPDATE_RAW_START, MOTA_NRF52_BL_SIZE / 4u);
#else
  sd_mbr_command_t command = {
    .command = SD_MBR_COMMAND_COPY_BL,
    .params.copy_bl.bl_src = (uint32_t *)BOOT_UPDATE_RAW_START,
    .params.copy_bl.bl_len = MOTA_NRF52_BL_SIZE / 4u,
  };
  (void)sd_mbr_command(&command); // success does not return
  return 0;
#endif
}

static bool boot_update_reject(const struct mota_min *m, uint32_t result) {
  gpregret2_set(result);
  if (m && m->approved && !clear_approval(m)) {
    gpregret2_set(GPREGRET2_BL_APPROVAL);
  }
  return finish_apply(false);
}

static uint32_t scan_bootloader_mota(struct mota_min *m, uint32_t source) {
#if defined(MOTA_SD_BOOTLOADER_UPDATE)
  if (source != GPREGRET2_OTA_STAGE_SD) {
    return 0;
  }
  return scan_mota(m, APP_APPLY_END);
#elif defined(MOTA_QSPI_BOOTLOADER_UPDATE)
  if (source != GPREGRET2_OTA_STAGE_QSPI) {
    return 0;
  }
  g_qspi_source = 1;
  return scan_mota(m, APP_APPLY_END);
#else
  if (source != GPREGRET2_OTA_STAGE_EXPANDED) {
    return 0;
  }
  const uint32_t address = scan_mota(m, MOTA_NRF52_INTERNAL_BL_SLOT_END);
  return address == MOTA_NRF52_INTERNAL_BL_SLOT_START ? address : 0;
#endif
}

static bool apply_bootloader_update(void) {
  const uint32_t source = gpregret2_get();
  gpregret_set(0); // consume first: every validation/copy failure is fail-closed
  gpregret2_set(GPREGRET2_BL_GATE);
#if defined(MOTA_SD_BOOTLOADER_UPDATE)
  const int sd_authorized =
    sd_auth_take(MOTA_SD_AUTH_PURPOSE_BOOTLOADER, 3u);
  if (source != GPREGRET2_OTA_STAGE_SD) {
    gpregret2_set(GPREGRET2_BL_CONTAINER);
    return finish_apply(false);
  }
  if (!sd_authorized) {
    gpregret2_set(GPREGRET2_BL_APPROVAL);
    return finish_apply(false);
  }
#endif
  struct mota_min m;
  if (!scan_bootloader_mota(&m, source) || !m.approved) {
    gpregret2_set(GPREGRET2_BL_CONTAINER);
    return finish_apply(false);
  }
#if defined(MOTA_SD_BOOTLOADER_UPDATE)
  if (!sd_authorized_container_valid()) {
    return boot_update_reject(&m, GPREGRET2_BL_APPROVAL);
  }
#endif
  if (!boot_update_policy_valid(&m)) {
    return boot_update_reject(&m, GPREGRET2_BL_POLICY);
  }
  if (!staged_vectors_valid(m.payload_addr) || !boot_payload_integrity_valid(&m)) {
    return boot_update_reject(&m, GPREGRET2_BL_INTEGRITY);
  }
#if defined(MOTA_INTERNAL_BOOTLOADER_UPDATE)
  // Validate both sides of the safety boundary while the exact staged package
  // is still intact. Neither check relies on the application's approval.
  if (!ota_delta_live_app_fits_below(MOTA_NRF52_INTERNAL_BL_SLOT_START)) {
    return boot_update_reject(&m, GPREGRET2_BL_POLICY);
  }
  if (!boot_image_metadata_valid_at(m.payload_addr, &m)) {
    return boot_update_reject(&m, GPREGRET2_BL_MANIFEST);
  }
#elif defined(MOTA_SD_BOOTLOADER_UPDATE)
  // SD-backed application builds do not reserve the fixed MBR scratch range
  // at link time. Prove the live EndF-inclusive image ends below 0xE0000
  // before erasing any scratch page.
  if (!ota_delta_live_app_fits_below(MOTA_NRF52_BL_SCRATCH_START)) {
    return boot_update_reject(&m, GPREGRET2_BL_POLICY);
  }
  // APRV is removable-media metadata. Bind the candidate bytes to the signed
  // manifest image_hash captured by the app in internal scratch before the
  // first erase. Copying page zero consumes the token.
  if (!sd_boot_authorization_valid(&m)) {
    return boot_update_reject(&m, GPREGRET2_BL_APPROVAL);
  }
#endif
  if (!clear_approval(&m)) {
    gpregret2_set(GPREGRET2_BL_APPROVAL);
    return finish_apply(false);
  }
  if (!copy_bootloader_to_raw_source(&m)) {
    gpregret2_set(GPREGRET2_BL_COPY);
    return finish_apply(false);
  }
  // Revalidate the exact embedded board manifest/CRC and continuity capability
  // from the final raw MBR source. A reset before the MBR call boots the
  // unchanged application and old bootloader.
  if (!boot_image_metadata_valid_at(BOOT_UPDATE_RAW_START, &m)) {
    gpregret2_set(GPREGRET2_BL_MANIFEST);
    return finish_apply(false);
  }

  // External staging is no longer needed after the scratch copy. Release the
  // SD/QSPI peripheral before handing control to the MBR.
#if defined(MOTA_SD_BOOTLOADER_UPDATE)
  ota_sd_deinit();
#elif defined(MOTA_QSPI_BOOTLOADER_UPDATE)
  ota_qspi_deinit();
  g_qspi_source = 0;
#endif
  gpregret2_set(GPREGRET2_BL_MBR_HANDOFF);
  if (mbr_copy_bootloader()) {
    return true; // host-only success model; hardware success never returns
  }
  gpregret2_set(GPREGRET2_BL_MBR_RETURNED);
  return false;
}
#endif // MOTA_BOOTLOADER_UPDATE_ENABLED

#if defined(MOTA_SD_CARD) || defined(MOTA_QSPI_FLASH)
static bool apply_full_external(const struct mota_min *m) {
  if (!m->is_full || m->codec_id != CODEC_FULL || m->image_size == 0 || m->payload_size != m->image_size ||
      m->image_size > APP_APPLY_END - APP_BASE) {
    gpregret2_set(0xB3);
    return false;
  }

  // Verify the complete external payload before the first destructive flash write.
  uint8_t h[32];
  if (!sha256_staged_region(m->payload_addr, m->payload_size, h) || memcmp(h, m->image_hash, sizeof(h)) != 0) {
    gpregret2_set(0xBA);
    return false;
  }

  if (!clear_approval(m)) {
    gpregret2_set(0xBC);
    return false;
  }
  inherited_watchdog_feed();
  otah_settings_commit(BANK_INVALID_APP_V, 0, 0);
  g_cache_page  = 0;
  g_cache_dirty = 0;

  for (uint32_t off = 0; off < m->image_size; off += PAGE) {
    inherited_watchdog_feed();
    uint32_t n = m->image_size - off;
    if (n > PAGE) {
      n = PAGE;
    }
    memset(g_cache, 0xFF, PAGE);
    if (!staged_read(m->payload_addr + off, g_cache, n)) {
      gpregret2_set(0xBB);
      return false;
    }
    fl_erase(APP_BASE + off);
    inherited_watchdog_feed();
    fl_write_words(APP_BASE + off, (const uint32_t *)g_cache, PAGE / 4);
  }

  sha256_region(APP_BASE, m->image_size, h);
  if (memcmp(h, m->image_hash, sizeof(h)) != 0) {
    gpregret2_set(0xB7);
    return false;
  }
  inherited_watchdog_feed();
  uint16_t image_crc = crc16_region(APP_BASE, m->image_size);
  inherited_watchdog_feed();
  otah_settings_commit(BANK_VALID_APP_V, image_crc, m->image_size);
  gpregret2_set(0xB8);
  return true;
}
#endif

static bool finish_apply(bool result) {
#if defined(MOTA_SD_CARD)
  // A rejected handoff falls through to the application without a hardware
  // reset, so do not leave SPIM2 or the SD pins owned by the bootloader.
  ota_sd_deinit();
#elif defined(MOTA_QSPI_FLASH)
  if (g_qspi_source) {
    ota_qspi_deinit();
  }
#endif
  return result;
}

bool ota_delta_check_and_apply(void) {
  inherited_watchdog_feed();
  // Force a volatile read of the capability marker so -flto / --gc-sections cannot fold the reference away
  // and drop it - the running app scans the bootloader flash for it (ota_bl_info.h / OtaBlInfo.h).
  volatile uint8_t keep = *(const volatile uint8_t *)&g_mota_bl_info.magic[0];
  if (keep == 0) {
    return finish_apply(false); // 'M' (0x4D) != 0, so never taken; keeps the marker live
  }

  // A completed apply result is retained in GPREGRET2 for the application to
  // report after the reset. Do not replace it during the normal post-apply
  // boot; only an explicit apply trigger starts a new diagnostic lifecycle.
  const uint32_t trigger = gpregret_get();
#if defined(MOTA_BOOTLOADER_UPDATE_ENABLED)
  if (trigger == GPREGRET_BOOTLOADER_APPLY) {
    return apply_bootloader_update();
  }
#endif
  if (trigger != GPREGRET_OTA_APPLY) {
    return finish_apply(false);
  }

  // Read the app's staging-window handoff BEFORE GPREGRET2 becomes our diagnostic result register.
  // Backward compatibility is deliberately one-way safe: an old app leaves no recognized expanded
  // marker, so this bootloader scans only below ExtraFS. A new app uses EXPANDED only after finding the
  // matching capability bit in g_mota_bl_info.
  const uint32_t stage_handoff = gpregret2_get();
#if defined(MOTA_QSPI_FLASH)
  g_qspi_source = stage_handoff == GPREGRET2_OTA_STAGE_QSPI;
#endif
  uint32_t stage_ceiling =
    stage_handoff == GPREGRET2_OTA_STAGE_EXPANDED ? MOTA_NRF52_STAGE_CEILING_EXPANDED : MOTA_NRF52_STAGE_CEILING_LEGACY;
#if defined(MOTA_QSPI_BOOTLOADER_UPDATE)
  if (stage_ceiling > APP_APPLY_END) {
    stage_ceiling = APP_APPLY_END;
  }
#endif
#if defined(MOTA_SD_CARD)
  const uint32_t app_limit = APP_APPLY_END;
#elif defined(MOTA_QSPI_FLASH)
  const uint32_t app_limit = g_qspi_source ? APP_APPLY_END : stage_ceiling;
#elif defined(MOTA_INTERNAL_BOOTLOADER_UPDATE)
  const uint32_t app_limit = APP_APPLY_END;
#else
  const uint32_t app_limit = stage_ceiling;
#endif
  // ---- DIAGNOSTIC: stash a bail/progress code in GPREGRET2; the app reads it back into `ota status`.
  // 0xB1 gate passed (GPREGRET was 0x6A) | 0xB2 no/unapproved mota |
  // 0xB3 bad full/codec | 0xB4 no body_len | 0xB5 base mismatch | 0xB9 bad detools geometry |
  // 0xBA external full pre-hash mismatch | 0xBB external read failure | 0xBC approval clear failure |
  // 0x9N detools err N | 0xB6 wrong size | 0xB7 result-hash mismatch | 0xB8 SUCCESS.
  gpregret_set(0); // consume the trigger so we never loop
  gpregret2_set(0xB1);

#if defined(MOTA_SD_CARD)
  if (!sd_auth_take(MOTA_SD_AUTH_PURPOSE_APP, 2u)) {
    gpregret2_set(0xBD);
    return finish_apply(false);
  }
#endif

  struct mota_min m;
  uint32_t        mota_addr = scan_mota(&m, stage_ceiling);
  if (!mota_addr || !m.approved) {
    gpregret2_set(0xB2);
    return finish_apply(false);
  } // nothing staged / unapproved

  // Format v3 is reserved for the distinct bootloader-update trigger. This is
  // the compatibility firewall that prevents a 40 KiB raw bootloader payload
  // from ever being installed at APP_BASE by the ordinary 0x6A path.
  if (m.format_ver != 2 || (m.flags & ~(MFLAG_FULL | MFLAG_SIGNED)) != 0) {
    gpregret2_set(0xB3);
    goto reject;
  }
#if defined(MOTA_SD_CARD)
  if (!sd_authorized_container_valid()) {
    gpregret2_set(0xBD);
    goto reject;
  }
#endif

#if defined(MOTA_SD_CARD)
  if (m.is_full) {
    return finish_apply(apply_full_external(&m));
  }
#elif defined(MOTA_QSPI_FLASH)
  if (g_qspi_source && m.is_full) {
    return finish_apply(apply_full_external(&m));
  }
#endif

  // base check (non-destructive): the running image's body must hash to the delta's base_hash.
  // Any pre-apply rejection clears the approval (this `.mota` is not applicable) and boots normally.
  uint32_t body_len;
  uint8_t  base32[32];
  if (m.is_full || m.codec_id != CODEC_INPLACE || m.image_size == 0 || m.image_size > app_limit - APP_BASE) {
    gpregret2_set(0xB3);
    goto reject;
  }
  if (!find_body_len(app_limit, &body_len)) {
    gpregret2_set(0xB4);
    goto reject;
  }
  sha256_region(APP_BASE, body_len, base32);
  if (memcmp(base32, m.base_hash, 8) != 0) {
    gpregret2_set(0xB5);
    goto reject;
  } // wrong base
#if defined(MOTA_SD_CARD)
  const uint32_t workspace_span = app_limit - APP_BASE;
#elif defined(MOTA_QSPI_FLASH)
  const uint32_t workspace_span = g_qspi_source ? app_limit - APP_BASE : mota_addr - APP_BASE;
#elif defined(MOTA_INTERNAL_BOOTLOADER_UPDATE)
  const uint32_t workspace_span = (mota_addr < app_limit ? mota_addr : app_limit) - APP_BASE;
#else
  const uint32_t workspace_span = mota_addr - APP_BASE;
#endif
  if (!dt_geometry_ok(&m, body_len, workspace_span)) {
    gpregret2_set(0xB9);
    goto reject;
  }

  // Consume approval before invalidating or modifying the application. A QSPI
  // program failure must leave the still-valid running image bootable.
  if (!clear_approval(&m)) {
    gpregret2_set(0xBC);
    return finish_apply(false);
  }

  // Commit point: make every subsequent reset enter DFU BEFORE the first destructive application write.
  // This is essential for UF2-installed apps, whose zero CRC otherwise makes a partial image bootable.
  inherited_watchdog_feed();
  otah_settings_commit(BANK_INVALID_APP_V, 0, 0);

  struct apply_ctx c;
  c.patch_addr = m.payload_addr;
  c.patch_len  = m.payload_size;
  c.patch_pos  = 0;
  c.ws_lo      = APP_BASE;
#if defined(MOTA_SD_CARD)
  c.ws_hi = app_limit; // patch is off-chip; whole app region is workspace
#elif defined(MOTA_QSPI_FLASH)
  c.ws_hi = g_qspi_source ? app_limit : mota_addr;
#elif defined(MOTA_INTERNAL_BOOTLOADER_UPDATE)
  // Application deltas and bootloader packages share the ED000 staging
  // ceiling. For an ordinary delta, detools workspace stops at the actual
  // bottom-aligned source so decoding can never erase its own container.
  c.ws_hi = mota_addr < app_limit ? mota_addr : app_limit;
#else
  c.ws_hi = mota_addr; // workspace stays strictly below internal mota
#endif
  c.step = 0;
  int r  = detools_apply_patch_in_place_callbacks(dt_mr, dt_mw, dt_me, dt_ss, dt_sg, dt_pr, (size_t)m.payload_size, &c);
  cache_flush();
  if (r < 0) {
    gpregret2_set(0x90 | ((uint32_t)(-r) & 0x0F));
    return finish_apply(false);
  }
  if ((uint32_t)r != m.image_size) {
    gpregret2_set(0xB6);
    return finish_apply(false);
  }

  uint8_t h[32];
  sha256_region(APP_BASE, m.image_size, h);
  if (memcmp(h, m.image_hash, 32) != 0) {
    gpregret2_set(0xB7);
    return finish_apply(false);
  }

  inherited_watchdog_feed();
  uint16_t image_crc = crc16_region(APP_BASE, m.image_size);
  inherited_watchdog_feed();
  otah_settings_commit(BANK_VALID_APP_V, image_crc, m.image_size);
  gpregret2_set(0xB8);
  return finish_apply(true);

reject:
  if (!clear_approval(&m)) {
    gpregret2_set(0xBC);
  }
  return finish_apply(false);
}
