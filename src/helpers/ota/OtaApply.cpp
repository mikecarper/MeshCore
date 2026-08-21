#include "OtaApply.h"
#include "OtaFormat.h"
#include "MotaContainer.h"
#include "Identity.h"
#include "OtaByteIO.h"
#include <string.h>

#if defined(ESP32_PLATFORM)
  #include <SHA256.h>            // rweather streaming SHA-256 (for hashing the slot in chunks)
  #include "esp_ota_ops.h"
  #include "esp_partition.h"
  #include "esp_system.h"
  extern "C" {
    #include "detools/detools.h" // vendored detools 0.53.0 embeddable decoder (CRLE-only build)
  }
  #if defined(OTA_FLASH_STORE)
    #include "OtaStoreFlashEsp32.h"   // flash-staged container (delta patch / full image in the slot)
    #include "OtaSelf.h"              // SelfFwInfo / ota_self_firmware (running-image base_hash gate)
  #endif
#elif defined(NRF52_PLATFORM)
  #include "OtaVerify.h"
  #include "OtaSelf.h"
  #include "OtaFlashLayout_nrf52.h"
  #include "OtaBlInfo.h"            // read the bootloader capability marker before arming an apply
  #include "flash/flash_nrf5x.h"  // Adafruit core internal-flash driver (has its own extern "C")
  #include "nrf.h"
  #include "nrf_soc.h"
  #include "nrf_sdm.h"
  #if defined(OTA_SD_STORE)
    #include "OtaStoreSdNrf52.h"
  #endif
  #if defined(OTA_QSPI_STORE)
    #include "OtaStoreQspiNrf52.h"
  #endif
#endif

namespace mesh {
namespace ota {

#if defined(ESP32_PLATFORM)

bool ota_apply_slot_info(uint32_t* addr, uint32_t* size) {
  const esp_partition_t* p = esp_ota_get_next_update_partition(nullptr);
  if (!p) return false;
  if (addr) *addr = p->address;
  if (size) *size = p->size;
  return true;
}

bool ota_apply_set_manifest(const uint8_t* mf, uint32_t len, const SignerAllowlist& allow, ApplyState& st) {
  st = ApplyState();
  ota_apply_slot_info(&st.slot_addr, &st.slot_size);
  MotaManifest m;
  if (!mota_parse_manifest(mf, len, m)) return false;
  if (m.is_bootloader() || !m.is_full()) return false; // boot packages never enter an application slot
  st.image_size = m.image_size;
  memcpy(st.image_hash, m.image_hash, 32);
  st.manifest_ok = true;
  if (m.is_signed()) {
    mesh::Identity signer(m.signer_pubkey);
    st.sig_ok = signer.verify(m.signature, m.manifest_start, (int)m.signed_len);
    st.trusted = st.sig_ok && allow.contains(m.signer_pubkey);
  }
  return true;
}

bool ota_apply_verify_slot(ApplyState& st) {
  st.slot_ok = false;
  if (!st.manifest_ok || st.image_size == 0 || st.image_size > st.slot_size) return false;
  const esp_partition_t* p = esp_ota_get_next_update_partition(nullptr);
  if (!p) return false;
  SHA256 sha;
  uint8_t buf[512];
  uint32_t off = 0;
  while (off < st.image_size) {
    uint32_t n = st.image_size - off; if (n > sizeof(buf)) n = sizeof(buf);
    if (esp_partition_read(p, off, buf, n) != ESP_OK) return false;
    sha.update(buf, n);
    off += n;
  }
  uint8_t h[32];
  sha.finalize(h, 32);
  st.slot_ok = (memcmp(h, st.image_hash, 32) == 0);
  return st.slot_ok;
}

bool ota_apply_commit() {
  const esp_partition_t* p = esp_ota_get_next_update_partition(nullptr);
  if (!p) return false;
  if (esp_ota_set_boot_partition(p) != ESP_OK) return false;
  esp_restart();   // does not return
  return true;
}

// --- detools callback context -----------------------------------------------------------------
// The delta base is the running OTA slot; the reconstructed image is streamed into the inactive slot
// via esp_ota_write (sequential, append-only -- matches detools' sequential output ordering) and
// hashed on the fly so we can check it against the signed manifest image_hash before arming.
struct DetoolsCtx {
  const esp_partition_t* base;    // delta base (running image), read at absolute `from_pos`
  long           from_pos;        // absolute byte offset into `base`
#if defined(OTA_FLASH_STORE)
  OtaStoreFlashEsp32* store;      // staged container; patch = payload region [patch_base, +patch_len)
  uint32_t       patch_base;      // container offset where the payload (patch) begins
#else
  const uint8_t* patch;           // .mota payload held wholly in RAM (RAM store; bring-up/host)
#endif
  uint32_t       patch_len;
  uint32_t       patch_pos;
  esp_ota_handle_t out;           // inactive slot write handle
  SHA256*        sha;             // running hash of the reconstructed output
  uint32_t       out_pos;         // #bytes written to the output slot
  bool           io_ok;
};

static int dt_from_read(void* arg, uint8_t* buf, size_t size) {
  DetoolsCtx* c = (DetoolsCtx*)arg;
  if (c->from_pos < 0 || (uint32_t)(c->from_pos) + size > c->base->size) return -DETOOLS_IO_FAILED;
  if (esp_partition_read(c->base, (size_t)c->from_pos, buf, size) != ESP_OK) { c->io_ok = false; return -DETOOLS_IO_FAILED; }
  c->from_pos += (long)size;
  return DETOOLS_OK;
}
static int dt_from_seek(void* arg, int offset) {          // detools uses relative seeks
  DetoolsCtx* c = (DetoolsCtx*)arg;
  c->from_pos += offset;
  if (c->from_pos < 0 || (uint32_t)c->from_pos > c->base->size) return -DETOOLS_IO_FAILED;
  return DETOOLS_OK;
}
static int dt_patch_read(void* arg, uint8_t* buf, size_t size) {
  DetoolsCtx* c = (DetoolsCtx*)arg;
  if (c->patch_pos + size > c->patch_len) return -DETOOLS_IO_FAILED;
#if defined(OTA_FLASH_STORE)
  if (!c->store->read(c->patch_base + c->patch_pos, buf, size)) { c->io_ok = false; return -DETOOLS_IO_FAILED; }
#else
  memcpy(buf, c->patch + c->patch_pos, size);
#endif
  c->patch_pos += (uint32_t)size;
  return DETOOLS_OK;
}
static int dt_to_write(void* arg, const uint8_t* buf, size_t size) {
  DetoolsCtx* c = (DetoolsCtx*)arg;
  if (esp_ota_write(c->out, buf, size) != ESP_OK) { c->io_ok = false; return -DETOOLS_IO_FAILED; }
  c->sha->update(buf, size);
  c->out_pos += (uint32_t)size;
  return DETOOLS_OK;
}

#if defined(OTA_FLASH_STORE)
// --- in-place delta on ESP32 (codec 2) ----------------------------------------------------------
// A single in-place `.mota` can target BOTH nRF52 (bootloader applies it) and ESP32. On ESP32 the
// inactive slot is used as the in-place working memory: we copy the running image (the base) into the
// slot's bottom-staged-container-FREE region [0, write_start), then run detools' in-place decoder over
// that region (it reads the base, erases segments, writes the target back), reading the patch from the
// staged container's payload (which lives at/below write_start, disjoint from the working region). The
// decoded image is hashed against the signed image_hash BEFORE arming, so a bad decode never boots; the
// callbacks are bounded to [0, write_start) so they fail gracefully instead of touching the patch.
// (Sequential is still preferred on ESP32 - it streams straight to the slot with no base-copy; in-place
// exists only for single-artifact distribution. Requires the patch built with --inplace-segment 4096.)
struct InPlaceCtx {
  const esp_partition_t* slot;    // in-place working memory = slot[0, mem_max)
  uint32_t       mem_max;         // = container write_start; accesses beyond this are refused
  OtaStoreFlashEsp32* store;      // staged container; patch = payload region [patch_base, +patch_len)
  uint32_t       patch_base, patch_len, patch_pos;
  int            step;            // detools resume cursor (RAM; no cross-reboot resume of the apply)
  bool           io_ok;
  const char*    fail;            // first failure point (diagnostic), nullptr until set
  uint32_t       fa, fn; int frc;  // failing addr / len / esp_err
};
static inline int ip_fail(InPlaceCtx* c, const char* w, uint32_t a, size_t n, int rc) {
  if (!c->fail) { c->fail = w; c->fa = a; c->fn = (uint32_t)n; c->frc = rc; }
  c->io_ok = false; return -DETOOLS_IO_FAILED;
}
static int ip_mem_read(void* a, void* dst, uintptr_t src, size_t n) {
  InPlaceCtx* c = (InPlaceCtx*)a;
  if ((uint32_t)src + n > c->mem_max) return ip_fail(c, "rd>max", (uint32_t)src, n, 0);
  int rc = esp_partition_read(c->slot, (size_t)src, dst, n);
  if (rc != ESP_OK) return ip_fail(c, "rd", (uint32_t)src, n, rc);
  return DETOOLS_OK;
}
static int ip_mem_write(void* a, uintptr_t dst, void* src, size_t n) {
  InPlaceCtx* c = (InPlaceCtx*)a;
  if ((uint32_t)dst + n > c->mem_max) return ip_fail(c, "wr>max", (uint32_t)dst, n, 0);
  int rc = esp_partition_write(c->slot, (size_t)dst, src, n);
  if (rc != ESP_OK) return ip_fail(c, "wr", (uint32_t)dst, n, rc);
  return DETOOLS_OK;
}
static int ip_mem_erase(void* a, uintptr_t addr, size_t n) {
  InPlaceCtx* c = (InPlaceCtx*)a;
  // esp_partition_erase_range requires a SECTOR-aligned size; detools' final in-place segment is partial
  // (the image tail past the last full sector). addr is sector-aligned (== --inplace-segment), so round
  // the length UP to a full sector. The over-erased bytes are scratch beyond image_size (never hashed),
  // and - since detools processes high->low and erases-before-writing - they are never live patch data.
  const uint32_t SEC = 4096;
  if ((uint32_t)addr % SEC != 0) return ip_fail(c, "er!align", (uint32_t)addr, n, 0);
  uint32_t len = ((uint32_t)n + SEC - 1) & ~(SEC - 1);
  if ((uint32_t)addr + len > c->mem_max) return ip_fail(c, "er>max", (uint32_t)addr, len, 0);
  int rc = esp_partition_erase_range(c->slot, (size_t)addr, len);
  if (rc != ESP_OK) return ip_fail(c, "er", (uint32_t)addr, len, rc);
  return DETOOLS_OK;
}
static int ip_step_set(void* a, int s) { ((InPlaceCtx*)a)->step = s; return DETOOLS_OK; }
static int ip_step_get(void* a, int* s) { *s = ((InPlaceCtx*)a)->step; return DETOOLS_OK; }
static int ip_patch_read(void* a, uint8_t* b, size_t n) {
  InPlaceCtx* c = (InPlaceCtx*)a;
  if (c->patch_pos + n > c->patch_len) return ip_fail(c, "patch>len", c->patch_pos, n, 0);
  if (!c->store->read(c->patch_base + c->patch_pos, b, n)) return ip_fail(c, "patch_rd", c->patch_pos, n, 0);
  c->patch_pos += (uint32_t)n;
  return DETOOLS_OK;
}

static bool esp32_inplace_apply(OtaStoreFlashEsp32& store, const MotaManifest& m, ApplyState& st, char* msg) {
  const esp_partition_t* slot = store.partition();
  const esp_partition_t* base = esp_ota_get_running_partition();
  if (!slot || !base) { strcpy(msg, "no slot/base partition"); return false; }
  SelfFwInfo fi;
  if (!ota_self_firmware(fi) || !fi.valid) { strcpy(msg, "cannot read running firmware (no EndF)"); return false; }
  if (!m.base_hash || memcmp(m.base_hash, fi.body_hash, 8) != 0) { strcpy(msg, "not built for the running firmware (base mismatch)"); return false; }
  uint32_t mem_max = store.write_start();          // working region [0, mem_max); the patch sits at/above it
  if (mem_max == 0) { strcpy(msg, "in-place needs a bottom-staged container"); return false; }
  if (fi.image_len > mem_max || m.image_size > mem_max) { strcpy(msg, "in-place region too small for base/image"); return false; }

  // load the base (running image) into the working region [0, base_len), sector by sector (erase + copy)
  uint8_t buf[512];
  for (uint32_t off = 0; off < fi.image_len; ) {
    uint32_t sec = off & ~(4096u - 1);
    if (esp_partition_erase_range(slot, sec, 4096) != ESP_OK) { strcpy(msg, "base erase failed"); return false; }
    uint32_t secend = sec + 4096; if (secend > fi.image_len) secend = fi.image_len;
    for (uint32_t p = (off > sec ? off : sec); p < secend; ) {
      uint32_t n = secend - p; if (n > sizeof(buf)) n = sizeof(buf);
      if (esp_partition_read(base, p, buf, n) != ESP_OK || esp_partition_write(slot, p, buf, n) != ESP_OK) {
        strcpy(msg, "base copy failed"); return false; }
      p += n;
    }
    off = secend;
  }

  // patch in place over the working region; patch streamed from the staged container payload
  InPlaceCtx c;
  c.slot = slot; c.mem_max = mem_max; c.store = &store;
  c.patch_base = store.meta_bytes(); c.patch_len = m.payload_size; c.patch_pos = 0; c.step = 0; c.io_ok = true;
  c.fail = nullptr; c.fa = c.fn = 0; c.frc = 0;
  int r = detools_apply_patch_in_place_callbacks(ip_mem_read, ip_mem_write, ip_mem_erase,
                                                 ip_step_set, ip_step_get, ip_patch_read,
                                                 (size_t)m.payload_size, &c);
  if (r < 0 || !c.io_ok) {
    if (c.fail) sprintf(msg, "in-place decode err %d @%s a=%u n=%u rc=%d max=%u", r, c.fail,
                        (unsigned)c.fa, (unsigned)c.fn, c.frc, (unsigned)mem_max);
    else sprintf(msg, "in-place decode err %d", r);
    return false;
  }
  if ((uint32_t)r != m.image_size) { sprintf(msg, "in-place size %u!=%u", (unsigned)r, (unsigned)m.image_size); return false; }

  // verify the decoded slot image against the signed image_hash BEFORE arming (mismatch -> never boots)
  SHA256 sha;
  for (uint32_t off = 0; off < m.image_size; ) {
    uint32_t n = m.image_size - off; if (n > sizeof(buf)) n = sizeof(buf);
    if (esp_partition_read(slot, off, buf, n) != ESP_OK) { strcpy(msg, "slot read failed"); return false; }
    sha.update(buf, n); off += n;
  }
  uint8_t hh[32]; sha.finalize(hh, 32);
  st.slot_ok = (memcmp(hh, m.image_hash, 32) == 0);
  if (!st.slot_ok) { strcpy(msg, "image_hash MISMATCH after in-place decode"); return false; }
  if (esp_ota_set_boot_partition(slot) != ESP_OK) { strcpy(msg, "set_boot failed"); return false; }
  sprintf(msg, "verified%s; in-place decoded %u B, image hash OK - armed, rebooting to apply",
          m.is_signed() ? " (signer trusted)" : " (unsigned)", (unsigned)m.image_size);
  return true;
}

// Apply the `.mota` staged in the inactive slot by OtaStoreFlashEsp32 (no contiguous RAM copy).
//   FULL: the payload was streamed straight to slot offset 0 during the fetch -> hash the slot image
//         and compare to the signed image_hash, then arm. No decode, no copy.
//   DELTA (sequential): base = the running slot; the patch is read from the staged payload region (the
//         slot's bottom); the reconstructed image is written to the inactive slot via esp_ota_write and
//         hashed vs image_hash. esp_ota_begin only erases [0, image_size], which the fetch-time fit
//         check kept below the bottom-staged container, so the patch survives while we decode over it.
//   DELTA (in-place): copy the running image into the slot's working region then patch in place
//         (esp32_inplace_apply); image_hash-gated before arming. Lets one in-place .mota target both
//         ESP32 and nRF52. Sequential is still preferred on ESP32 (no base-copy).
// The result is verified (signature/trust up front, image_hash after) and the slot armed; the caller
// reboots once the confirmation reply has gone out.
bool ota_apply_detools_mota(OtaStoreFlashEsp32& store, const SignerAllowlist& allow, ApplyState& st, char* msg) {
  st = ApplyState();
  const esp_partition_t* slot = store.partition();
  if (!slot || store.staged_size() < 16) { strcpy(msg, "no staged update"); return false; }
  st.slot_addr = slot->address; st.slot_size = slot->size;

  // read + parse the manifest out of the staged container (header = MAGIC(4) + total(4))
  uint8_t hdr[8];
  if (!store.read(0, hdr, 8) || memcmp(hdr, MOTA_MAGIC, 4) != 0) { strcpy(msg, "bad container"); return false; }
  uint8_t mfbuf[256];
  uint32_t mflen = store.meta_bytes() > 8 ? store.meta_bytes() - 8 : 0;   // manifest+leaves; cap to mfbuf
  if (mflen > sizeof(mfbuf)) mflen = sizeof(mfbuf);
  MotaManifest m;
  if (mflen < 57 || !store.read(8, mfbuf, mflen) || !mota_parse_manifest(mfbuf, mflen, m)) {
    strcpy(msg, "manifest parse failed"); return false; }
  if (m.is_bootloader()) { strcpy(msg, "bootloader package requires explicit bootloader install"); return false; }
  st.image_size = m.image_size; memcpy(st.image_hash, m.image_hash, 32); st.manifest_ok = true;
  if (m.image_size == 0 || m.image_size > slot->size) { strcpy(msg, "image > slot"); return false; }

  // signature / trust BEFORE arming an untrusted image (image_hash below is the target-firmware gate)
  if (m.is_signed()) {
    mesh::Identity signer(m.signer_pubkey);
    st.sig_ok = signer.verify(m.signature, m.manifest_start, (int)m.signed_len);
    st.trusted = st.sig_ok && allow.contains(m.signer_pubkey);
    if (!st.sig_ok)  { strcpy(msg, "bad signature"); return false; }
    if (!st.trusted) { strcpy(msg, "untrusted signer (pubkey not in allowlist)"); return false; }
  }

  // ---- FULL: payload already in slot[0]; verify hash + arm ----
  if (m.is_full()) {
    SHA256 sha; uint8_t buf[512];
    for (uint32_t off = 0; off < m.image_size; ) {
      uint32_t n = m.image_size - off; if (n > sizeof(buf)) n = sizeof(buf);
      if (esp_partition_read(slot, off, buf, n) != ESP_OK) { strcpy(msg, "slot read failed"); return false; }
      sha.update(buf, n); off += n;
    }
    uint8_t hh[32]; sha.finalize(hh, 32);
    st.slot_ok = (memcmp(hh, m.image_hash, 32) == 0);
    if (!st.slot_ok) { strcpy(msg, "image_hash MISMATCH (slot)"); return false; }
    if (esp_ota_set_boot_partition(slot) != ESP_OK) { strcpy(msg, "set_boot failed"); return false; }
    sprintf(msg, "verified%s full image %u B in slot - armed, rebooting to apply",
            m.is_signed() ? " (trusted)" : " (unsigned)", (unsigned)m.image_size);
    return true;
  }

  // ---- DELTA ----
  if (m.codec_id == CODEC_DETOOLS_INPLACE) return esp32_inplace_apply(store, m, st, msg);  // single-artifact codec
  if (m.codec_id != CODEC_DETOOLS_SEQUENTIAL) { strcpy(msg, "unknown delta codec"); return false; }

  // delta must be built for the running firmware (cheap early gate; image_hash is the definitive check)
  if (m.base_hash) {
    SelfFwInfo fi;
    if (!ota_self_firmware(fi) || !fi.valid) { strcpy(msg, "cannot read running firmware (no EndF)"); return false; }
    if (memcmp(m.base_hash, fi.body_hash, 8) != 0) { strcpy(msg, "delta not built for the running firmware (base mismatch)"); return false; }
  }

  const esp_partition_t* base = esp_ota_get_running_partition();
  if (!base) { strcpy(msg, "no running partition"); return false; }
  esp_ota_handle_t h;
  if (esp_ota_begin(slot, m.image_size, &h) != ESP_OK) { strcpy(msg, "ota_begin failed"); return false; }

  SHA256 sha;
  DetoolsCtx ctx;
  ctx.base = base; ctx.from_pos = 0;
  ctx.store = &store; ctx.patch_base = store.meta_bytes(); ctx.patch_len = m.payload_size; ctx.patch_pos = 0;
  ctx.out = h; ctx.sha = &sha; ctx.out_pos = 0; ctx.io_ok = true;

  int r = detools_apply_patch_callbacks(dt_from_read, dt_from_seek, dt_patch_read,
                                        (size_t)m.payload_size, dt_to_write, &ctx);
  if (r < 0 || !ctx.io_ok) { esp_ota_abort(h); sprintf(msg, "detools err %d @%u/%u",
                             ctx.io_ok ? r : -DETOOLS_IO_FAILED, (unsigned)ctx.out_pos, (unsigned)m.image_size); return false; }
  if ((uint32_t)r != m.image_size || ctx.out_pos != m.image_size) {
    esp_ota_abort(h); sprintf(msg, "size mismatch %u!=%u", (unsigned)ctx.out_pos, (unsigned)m.image_size); return false; }
  uint8_t hh[32]; sha.finalize(hh, 32);
  st.slot_ok = (memcmp(hh, m.image_hash, 32) == 0);
  if (!st.slot_ok) { esp_ota_abort(h); strcpy(msg, "image_hash MISMATCH after decode"); return false; }
  if (esp_ota_end(h) != ESP_OK) { strcpy(msg, "ota_end failed"); return false; }
  if (esp_ota_set_boot_partition(slot) != ESP_OK) { strcpy(msg, "set_boot failed"); return false; }
  sprintf(msg, "verified%s; decoded %u B, image hash OK - armed, rebooting to apply",
          m.is_signed() ? " (signer trusted)" : " (unsigned)", (unsigned)m.image_size);
  return true;
}

#else  // !OTA_FLASH_STORE: RAM-staged apply (whole .mota in a contiguous RAM buffer; bring-up/host)

bool ota_apply_detools_mota(const uint8_t* buf, uint32_t len, const SignerAllowlist& allow,
                            ApplyState& st, char* msg) {
  st = ApplyState();
  MotaManifest m;
  if (!mota_parse(buf, len, m)) { strcpy(msg, "no valid .mota (parse failed)"); return false; }
  if (m.is_bootloader()) { strcpy(msg, "bootloader package cannot enter an application slot"); return false; }
  if (m.is_full() || m.codec_id != CODEC_DETOOLS_SEQUENTIAL) { strcpy(msg, "not a detools-sequential delta"); return false; }
  st.image_size = m.image_size;
  memcpy(st.image_hash, m.image_hash, 32);
  st.manifest_ok = true;
  if (m.is_signed()) {
    mesh::Identity signer(m.signer_pubkey);
    st.sig_ok = signer.verify(m.signature, m.manifest_start, (int)m.signed_len);
    st.trusted = st.sig_ok && allow.contains(m.signer_pubkey);
    if (!st.sig_ok)  { strcpy(msg, "bad signature"); return false; }
    if (!st.trusted) { strcpy(msg, "untrusted signer (pubkey not in allowlist)"); return false; }
  }
  const esp_partition_t* base = esp_ota_get_running_partition();
  const esp_partition_t* out  = esp_ota_get_next_update_partition(nullptr);
  if (!base || !out) { strcpy(msg, "no A/B slot"); return false; }
  st.slot_addr = out->address; st.slot_size = out->size;
  if (m.image_size > out->size) { strcpy(msg, "image > slot"); return false; }
  esp_ota_handle_t h;
  if (esp_ota_begin(out, m.image_size, &h) != ESP_OK) { strcpy(msg, "ota_begin failed"); return false; }
  SHA256 sha;
  DetoolsCtx ctx;
  ctx.base = base; ctx.from_pos = 0;
  ctx.patch = m.payload; ctx.patch_len = m.payload_size; ctx.patch_pos = 0;
  ctx.out = h; ctx.sha = &sha; ctx.out_pos = 0; ctx.io_ok = true;
  int r = detools_apply_patch_callbacks(dt_from_read, dt_from_seek, dt_patch_read,
                                        (size_t)m.payload_size, dt_to_write, &ctx);
  if (r < 0 || !ctx.io_ok) { esp_ota_abort(h); sprintf(msg, "detools err %d @%u/%u",
                             ctx.io_ok ? r : -DETOOLS_IO_FAILED, (unsigned)ctx.out_pos, (unsigned)m.image_size); return false; }
  if ((uint32_t)r != m.image_size || ctx.out_pos != m.image_size) {
    esp_ota_abort(h); sprintf(msg, "size mismatch %u!=%u", (unsigned)ctx.out_pos, (unsigned)m.image_size); return false; }
  uint8_t hh[32]; sha.finalize(hh, 32);
  st.slot_ok = (memcmp(hh, m.image_hash, 32) == 0);
  if (!st.slot_ok) { esp_ota_abort(h); strcpy(msg, "image_hash MISMATCH after decode"); return false; }
  if (esp_ota_end(h) != ESP_OK) { strcpy(msg, "ota_end failed"); return false; }
  if (esp_ota_set_boot_partition(out) != ESP_OK) { strcpy(msg, "set_boot failed"); return false; }
  sprintf(msg, "verified%s; decoded %u B, image hash OK - armed, rebooting to apply",
          m.is_signed() ? " (signer trusted)" : " (unsigned)", (unsigned)m.image_size);
  return true;
}
#endif // OTA_FLASH_STORE

bool ota_apply_mota_nrf52(const uint8_t*, uint32_t, const SignerAllowlist&, ApplyState& st, char* msg) {
  st = ApplyState(); strcpy(msg, "nRF52-only (ESP32 uses ota_apply_detools_mota)"); return false;
}
bool ota_rescue_mota_nrf52(const uint8_t*, uint32_t, const SignerAllowlist&, const uint8_t*,
                           uint32_t, ApplyState& st, char* msg) {
  st = ApplyState(); strcpy(msg, "rescue is internal-flash nRF52-only"); return false;
}

void ota_reboot_to_apply() { esp_restart(); }  // boots the slot armed by ota_apply_detools_mota; no return
void ota_reboot_to_bootloader_update() {}
bool ota_installed_bootloader_identity(OtaBootloaderIdentity& out) {
  out = OtaBootloaderIdentity(); return false;
}

#elif defined(NRF52_PLATFORM)  // single-slot: verify + mark APPROVED + hand off to the bootloader

// Capture a real OTAFIX result before board power-management initialization
// consumes GPREGRET2. Ignore shutdown reasons and staging handoff markers that
// share the register but are not bootloader apply results.
static uint8_t g_bootloader_last_rc = 0;
static void __attribute__((constructor(101))) ota_capture_bootloader_last_rc() {
  g_bootloader_last_rc = ota_nrf52_boot_result_or_zero((uint8_t)NRF_POWER->GPREGRET2);
}

struct InplacePatchDims {
  uint32_t memory = 0;
  uint32_t segment = 0;
  uint32_t shift = 0;
  uint32_t from = 0;
  uint32_t to = 0;
};

static bool parse_inplace_patch_dims(const uint8_t* payload, uint32_t payload_len,
                                     InplacePatchDims& d) {
  if (!payload || payload_len < 2 || ((payload[0] >> 4) & 0x07u) != 1u) return false;
  ByteReader r(payload, payload_len);
  r.u8(); // patch type/compression header
  return r.detools_size(d.memory) && r.detools_size(d.segment) && r.detools_size(d.shift) &&
         r.detools_size(d.from) && r.detools_size(d.to) && r.ok;
}

// ESP32 A/B-only entry points are unsupported on nRF52.
bool ota_apply_slot_info(uint32_t*, uint32_t*) { return false; }
bool ota_apply_set_manifest(const uint8_t*, uint32_t, const SignerAllowlist&, ApplyState& st) { st = ApplyState(); return false; }
bool ota_apply_verify_slot(ApplyState&) { return false; }
bool ota_apply_commit() { return false; }
bool ota_apply_detools_mota(const uint8_t*, uint32_t, const SignerAllowlist&, ApplyState& st, char* msg) { st = ApplyState(); strcpy(msg, "use ota_apply_mota_nrf52"); return false; }

void ota_reboot_to_apply() {                   // public: set the apply magic + reset (does not return)
  uint8_t stage_handoff = GPREGRET2_OTA_STAGE_LEGACY;
#if defined(OTA_QSPI_STORE)
  stage_handoff = GPREGRET2_OTA_STAGE_QSPI;
#elif defined(OTA_FLASH_STORE)
  if (ota_nrf52_effective_stage_ceiling() == MOTA_NRF52_STAGE_CEILING_EXPANDED)
    stage_handoff = GPREGRET2_OTA_STAGE_EXPANDED;
#endif
  uint8_t sd_en = 0;
  sd_softdevice_is_enabled(&sd_en);
  if (sd_en) {                                 // POWER is SD-restricted while the SoftDevice runs
    sd_power_gpregret_clr(1, 0xFFFFFFFF);
    sd_power_gpregret_set(1, stage_handoff);
    sd_power_gpregret_clr(0, 0xFFFFFFFF);
    sd_power_gpregret_set(0, GPREGRET_OTA_APPLY);
  } else {
    NRF_POWER->GPREGRET2 = stage_handoff;
    NRF_POWER->GPREGRET = GPREGRET_OTA_APPLY;
  }
  NVIC_SystemReset();                          // does not return
}

void ota_reboot_to_bootloader_update() {
  uint8_t sd_en = 0;
  sd_softdevice_is_enabled(&sd_en);
  if (sd_en) {
    sd_power_gpregret_clr(1, 0xFFFFFFFF);
    sd_power_gpregret_set(1, GPREGRET2_OTA_STAGE_QSPI);
    sd_power_gpregret_clr(0, 0xFFFFFFFF);
    sd_power_gpregret_set(0, GPREGRET_OTA_BOOTLOADER_UPDATE);
  } else {
    NRF_POWER->GPREGRET2 = GPREGRET2_OTA_STAGE_QSPI;
    NRF_POWER->GPREGRET = GPREGRET_OTA_BOOTLOADER_UPDATE;
  }
  NVIC_SystemReset();
}

uint8_t ota_bootloader_last_rc() { return g_bootloader_last_rc; }

bool ota_installed_bootloader_identity(OtaBootloaderIdentity& out) {
  return ota_bootloader_identity_from_image(
      (const uint8_t*)(uintptr_t)OTA_BOOT_IMAGE_START, OTA_BOOT_IMAGE_SIZE, out);
}

static bool ota_apply_mota_nrf52_impl(const uint8_t* buf, uint32_t len,
                                      const SignerAllowlist& allow,
                                      const uint8_t* rescue_base_hash,
                                      uint32_t local_target_id,
                                      ApplyState& st, char* msg) {
  st = ApplyState();
  MotaManifest m;
  if (!mota_parse(buf, len, m)) { strcpy(msg, "parse failed"); return false; }
  if (m.is_bootloader()) { strcpy(msg, "use explicit ota bootloader install"); return false; }
  if (m.is_full() || m.codec_id != CODEC_DETOOLS_INPLACE) { strcpy(msg, "not an in-place delta"); return false; }
  st.image_size = m.image_size;
  memcpy(st.image_hash, m.image_hash, 32);
  st.manifest_ok = true;

  // 0) THIS device's bootloader must be able to apply this .mota - otherwise staging + approving + rebooting
  //    just bounces back unchanged (a legacy/stock/older-OTAFIX bootloader). Refuse here, before any reboot.
  {
    OtaBlCaps bl = ota_bootloader_caps();
    if (!bl.present) { strcpy(msg, "this bootloader has no OTA-apply support - update the bootloader first"); return false; }
    if (bl.apply_abi < m.format_ver || !(bl.codec_mask & (1u << m.codec_id))) {
      snprintf(msg, 159, "bootloader too old to apply this update (bl abi=%u codecs=0x%x; need fmt>=%u codec=%u) - update the bootloader",
               bl.apply_abi, bl.codec_mask, m.format_ver, m.codec_id);
      return false;
    }
  }

  // Gated verification, in order, returning the FIRST failing reason (the bootloader re-checks integrity
  // again before booting, so authenticity is gated here and re-validated there):
  VerifyResult vr = ota_verify(buf, len, allow);
  st.sig_ok = vr.sig_ok; st.trusted = vr.trusted;

  // 1) downloaded payload: the fetched blocks must match the manifest's merkle root (intact + complete)
  if (!vr.root_ok || !vr.payload_ok || !vr.image_ok) {
    strcpy(msg, "payload hash mismatch (incomplete or corrupt .mota)"); return false;
  }

  // 2) target firmware. Normally the delta must match the validated EndF body hash. The explicit rescue
  //    path exists only for a firmware whose normal EndF validation is broken: it requires the operator's
  //    exact package base hash and the package's target_id. It then delegates the actual running-image
  //    base check to the bootloader, which performs that check before its first application write.
  SelfFwInfo fi;
  const bool self_valid = ota_self_firmware(fi) && fi.valid;
  if (rescue_base_hash) {
    switch (ota_nrf52_rescue_gate(self_valid, m.base_hash, rescue_base_hash,
                                  m.target_id, local_target_id)) {
      case NRF52_RESCUE_SELF_VALID:
        strcpy(msg, "EndF OK; use ota install"); return false;
      case NRF52_RESCUE_BASE_MISSING:
      case NRF52_RESCUE_BASE_MISMATCH:
        strcpy(msg, "rescue base mismatch"); return false;
      case NRF52_RESCUE_TARGET_MISMATCH:
        strcpy(msg, "rescue target mismatch"); return false;
      case NRF52_RESCUE_OK:
        break;
    }
  } else {
    if (!self_valid) { strcpy(msg, "cannot read running firmware (no EndF)"); return false; }
    if (!m.base_hash || memcmp(m.base_hash, fi.body_hash, 8) != 0) {
      strcpy(msg, "not built for the running firmware (base mismatch)"); return false;
    }
  }
  st.slot_ok = true;

  // 3) signature (only if the .mota is signed): valid Ed25519 AND signer in this device's allowlist
  if (vr.is_signed) {
    if (!vr.sig_ok)  { strcpy(msg, "bad signature"); return false; }
    if (!vr.trusted) { strcpy(msg, "untrusted signer (pubkey not in allowlist)"); return false; }
  }

  // 4) detools geometry. memory_size is selected by motatool for this exact staged address; reject a
  // mismatched/legacy package before writing APRV so the bootloader never starts a doomed in-place apply.
  {
    InplacePatchDims d;
    if (!parse_inplace_patch_dims(m.payload, m.payload_size, d)) {
      strcpy(msg, "bad in-place patch header"); return false;
    }
    if (d.memory == 0 || d.segment != MOTA_NRF52_FLASH_PAGE || d.shift > d.memory ||
        d.shift % d.segment != 0 || d.from > d.memory - d.shift || d.to > d.memory ||
        d.to != m.image_size || (self_valid && d.from != fi.image_len)) {
      strcpy(msg, "invalid in-place patch geometry"); return false;
    }
#if defined(OTA_FLASH_STORE)
    const uint32_t app_base = mota_nrf52_app_base();
    const uint32_t mota_start = (uint32_t)(uintptr_t)buf;
    const uint32_t stage_ceiling = ota_nrf52_effective_stage_ceiling();
    if (mota_start < app_base || mota_start >= stage_ceiling ||
        d.memory > mota_start - app_base) {
      snprintf(msg, 159, "patch memory 0x%x exceeds staging at 0x%x",
               (unsigned)d.memory, (unsigned)mota_start);
      return false;
    }
#endif
  }

  // mark the staged manifest APPROVED in flash (buf is the memory-mapped staging region, so
  // m.approval is a real flash address). NOR-clear over the erased 0xFFFFFFFF -> "APRV".
  uint32_t approval_addr = (uint32_t)(uintptr_t)m.approval;
  if (flash_nrf5x_write(approval_addr, APPROVAL_YES, 4) < 0) { strcpy(msg, "approval write failed"); return false; }
  flash_nrf5x_flush();
  if (memcmp((const void*)(uintptr_t)approval_addr, APPROVAL_YES, 4) != 0) { strcpy(msg, "approval not set"); return false; }

  // Approved. Do NOT reset here - return so the caller can deliver `msg` to the operator first; the
  // deferred ota_reboot_to_apply() (after the reply is sent) does the actual handoff to the bootloader.
  if (rescue_base_hash) {
    strcpy(msg, "rescue armed; bootloader checks base");
  } else {
    sprintf(msg, "verified%s; applying - rebooting into bootloader once this reply is sent",
            vr.is_signed ? " (signer trusted)" : " (unsigned)");
  }
  return true;
}

bool ota_apply_mota_nrf52(const uint8_t* buf, uint32_t len, const SignerAllowlist& allow,
                          ApplyState& st, char* msg) {
  return ota_apply_mota_nrf52_impl(buf, len, allow, nullptr, 0, st, msg);
}

bool ota_rescue_mota_nrf52(const uint8_t* buf, uint32_t len, const SignerAllowlist& allow,
                           const uint8_t operator_base_hash[8], uint32_t local_target_id,
                           ApplyState& st, char* msg) {
  return ota_apply_mota_nrf52_impl(buf, len, allow, operator_base_hash, local_target_id, st, msg);
}

#if defined(OTA_SD_STORE) || defined(OTA_QSPI_STORE)
static const size_t NRF52_APPLY_MSG_CAP = 96;

template <typename Store>
static bool ota_apply_mota_nrf52_external(Store& store, const SignerAllowlist& allow,
                                          uint8_t storage_flag, const char* storage_name,
                                          ApplyState& st, char* msg) {
  st = ApplyState();
  uint8_t hdr[8], manifest[MOTA_MFL];
  uint32_t total = store.staged_size();
  if (total < 8 + MOTA_MFL + 5 || !store.read(0, hdr, sizeof(hdr)) ||
      memcmp(hdr, MOTA_MAGIC, 4) != 0 || rd_u32le(hdr + 4) != total ||
      !store.read(8, manifest, sizeof(manifest))) {
    snprintf(msg, NRF52_APPLY_MSG_CAP, "%s container parse failed", storage_name);
    return false;
  }
  MotaManifest m;
  if (!mota_parse_manifest(manifest, sizeof(manifest), m)) {
    snprintf(msg, NRF52_APPLY_MSG_CAP, "%s manifest parse failed", storage_name);
    return false;
  }
  if (m.is_bootloader()) {
    strcpy(msg, "use explicit ota bootloader install");
    return false;
  }
  const bool full = m.is_full() && m.codec_id == CODEC_FULL &&
                    m.payload_size == m.image_size;
  const bool delta = !m.is_full() && m.codec_id == CODEC_DETOOLS_INPLACE;
  if (!full && !delta) {
    snprintf(msg, NRF52_APPLY_MSG_CAP,
             "nRF52 %s bootloader accepts full or in-place delta only", storage_name);
    return false;
  }
  const uint32_t app_base = mota_nrf52_app_base();
  const uint32_t app_ceiling = mota_nrf52_application_ceiling();
  if (m.image_size == 0 || app_base >= app_ceiling ||
      m.image_size > app_ceiling - app_base) {
    strcpy(msg, "image exceeds nRF52 application region");
    return false;
  }
  st.image_size = m.image_size;
  memcpy(st.image_hash, m.image_hash, sizeof(st.image_hash));
  st.manifest_ok = true;

  OtaBlCaps bl = ota_bootloader_caps();
  if (!bl.present || !(bl.storage_flags & storage_flag)) {
    snprintf(msg, NRF52_APPLY_MSG_CAP,
             "this bootloader has no %s OTA support - update the bootloader first", storage_name);
    return false;
  }
  if (bl.apply_abi < m.format_ver || !(bl.codec_mask & (1u << m.codec_id))) {
    snprintf(msg, NRF52_APPLY_MSG_CAP,
             "bootloader cannot apply this %s update (abi=%u codecs=0x%x; need fmt=%u codec=%u)",
             storage_name, bl.apply_abi, bl.codec_mask, m.format_ver, m.codec_id);
    return false;
  }

  VerifyResult vr = ota_verify(static_cast<const OtaStore&>(store), allow);
  st.sig_ok = vr.sig_ok;
  st.trusted = vr.trusted;
  if (!vr.root_ok || !vr.payload_ok || !vr.image_ok) {
    snprintf(msg, NRF52_APPLY_MSG_CAP,
             "payload hash mismatch (incomplete or corrupt %s .mota)", storage_name);
    return false;
  }

  SelfFwInfo fi;
  if (delta) {
    if (!ota_self_firmware(fi) || !fi.valid) {
      strcpy(msg, "cannot read running firmware (no EndF)");
      return false;
    }
    if (!m.base_hash || memcmp(m.base_hash, fi.body_hash, 8) != 0) {
      strcpy(msg, "not built for the running firmware (base mismatch)");
      return false;
    }
  }
  st.slot_ok = true;
  if (vr.is_signed) {
    if (!vr.sig_ok)  { strcpy(msg, "bad signature"); return false; }
    if (!vr.trusted) { strcpy(msg, "untrusted signer (pubkey not in allowlist)"); return false; }
  }
  if (delta) {
    const uint64_t payload_off64 = 8u + MOTA_MFL + (uint64_t)m.block_count * 4u;
    uint8_t patch_header[32]; // fixed byte + five detools varints (at most 26 bytes for uint32)
    uint32_t header_len = m.payload_size < sizeof(patch_header) ? m.payload_size : sizeof(patch_header);
    InplacePatchDims d;
    if (payload_off64 > UINT32_MAX || payload_off64 + m.payload_size + 5u != total ||
        !store.read((uint32_t)payload_off64, patch_header, header_len) ||
        !parse_inplace_patch_dims(patch_header, header_len, d)) {
      strcpy(msg, "bad in-place patch header");
      return false;
    }
    if (!mota_nrf52_external_patch_geometry_valid(
            d.memory, d.segment, d.shift, d.from, d.to,
            app_ceiling - app_base, fi.image_len, m.image_size)) {
      strcpy(msg, "invalid in-place patch geometry");
      return false;
    }
  }
  if (!store.approve_for_bootloader()) {
    snprintf(msg, NRF52_APPLY_MSG_CAP, "%s handoff failed: %s", storage_name,
             store.last_error());
    return false;
  }
  snprintf(msg, NRF52_APPLY_MSG_CAP,
           "verified%s %s image on %s; rebooting into bootloader once this reply is sent",
           vr.is_signed ? " (signer trusted)" : " (unsigned)", full ? "full" : "delta",
           storage_name);
  return true;
}
#endif

#if defined(OTA_SD_STORE)
bool ota_apply_mota_nrf52(OtaStoreSdNrf52& store, const SignerAllowlist& allow,
                          ApplyState& st, char* msg) {
  return ota_apply_mota_nrf52_external(store, allow, OTA_BL_STORAGE_SD, "SD", st, msg);
}
#endif

#if defined(OTA_QSPI_STORE)
bool ota_apply_mota_nrf52(OtaStoreQspiNrf52& store, const SignerAllowlist& allow,
                          ApplyState& st, char* msg) {
  return ota_apply_mota_nrf52_external(store, allow, OTA_BL_STORAGE_QSPI, "QSPI", st, msg);
}

#if defined(OTA_QSPI_BOOTLOADER_UPDATE)
static bool qspi_bootloader_crc_ok(OtaStoreQspiNrf52& store, uint32_t payload_off,
                                   const OtaBootloaderIdentity& identity) {
  uint8_t buf[512];
  uint32_t crc = UINT32_MAX;
  for (uint32_t off = 0; off < OTA_BOOT_IMAGE_SIZE; off += sizeof(buf)) {
    uint32_t len = OTA_BOOT_IMAGE_SIZE - off;
    if (len > sizeof(buf)) len = sizeof(buf);
    if (!store.read(payload_off + off, buf, len)) return false;
    for (uint32_t i = 0; i < len; i++) {
      const uint32_t pos = off + i;
      const uint8_t value = pos >= identity.manifest_offset + 40u &&
                            pos < identity.manifest_offset + 44u ? 0 : buf[i];
      crc = ota_boot_crc32_update(crc, value);
    }
  }
  return ~crc == identity.crc32;
}

static bool qspi_bootloader_image_metadata(OtaStoreQspiNrf52& store, uint32_t payload_off,
                                            const OtaBootloaderIdentity& installed,
                                            OtaBootloaderIdentity& candidate,
                                            OtaBootloaderCapsMarker& caps) {
  // Keep enough overlap to catch a 44-byte manifest or 16-byte capability marker split across a
  // window. Every aligned manifest candidate is considered; a magic string in a literal pool is not
  // a terminal failure.
  static const uint32_t STEP = 512;
  uint8_t buf[STEP + OTA_BOOT_MANIFEST_SIZE - 1];
  uint8_t valid_identities = 0;
  for (uint32_t base = 0; base < OTA_BOOT_IMAGE_SIZE; base += STEP) {
    uint32_t len = OTA_BOOT_IMAGE_SIZE - base;
    if (len > sizeof(buf)) len = sizeof(buf);
    if (!store.read(payload_off + base, buf, len)) return false;
    const uint32_t starts = (OTA_BOOT_IMAGE_SIZE - base < STEP)
        ? OTA_BOOT_IMAGE_SIZE - base : STEP;
    for (uint32_t local = 0; local < starts; local++) {
      const uint32_t absolute = base + local;
      if (!caps.present && (absolute & 3u) == 0 && local + 16u <= len) {
        OtaBootloaderCapsMarker parsed;
        if (ota_bootloader_caps_marker_parse(buf + local, parsed) &&
            parsed.apply_abi >= MOTA_BOOT_FORMAT_VER &&
            (parsed.codec_mask & (1u << CODEC_FULL)) != 0 &&
            (parsed.storage_flags & (OTA_BL_STORAGE_QSPI | OTA_BL_STORAGE_BOOT_UPDATE)) ==
                (OTA_BL_STORAGE_QSPI | OTA_BL_STORAGE_BOOT_UPDATE)) {
          caps = parsed;
        }
      }
      if ((absolute & 3u) != 0 || local + OTA_BOOT_MANIFEST_SIZE > len) continue;
      OtaBootloaderIdentity parsed;
      if (!ota_bootloader_manifest_parse(buf + local, absolute, parsed) ||
          parsed.board_id != installed.board_id ||
          memcmp(parsed.device_name, installed.device_name, OTA_BOOT_DEVICE_NAME_SIZE) != 0) continue;
      if (qspi_bootloader_crc_ok(store, payload_off, parsed)) {
        parsed.crc_ok = true;
        if (!ota_bootloader_identity_add_unique(parsed, candidate, valid_identities)) return false;
      }
    }
  }
  return valid_identities == 1u && candidate.present && candidate.crc_ok && caps.present;
}

bool ota_prepare_bootloader_update_nrf52(OtaStoreQspiNrf52& store,
                                         const SignerAllowlist& allow,
                                         const OtaBootloaderIdentity& installed,
                                         const uint8_t actual_mid[4],
                                         const uint8_t operator_mid[4],
                                         const uint8_t operator_hash8[8],
                                         ApplyState& st, char* msg) {
  static const size_t CAP = 96;
  st = ApplyState();
  const uint32_t total = store.staged_size();
  uint8_t hdr[8], manifest[MOTA_MFL];
  if (total < 8u + MOTA_MFL + 5u || !store.read(0, hdr, sizeof(hdr)) ||
      memcmp(hdr, MOTA_MAGIC, sizeof(MOTA_MAGIC)) != 0 || rd_u32le(hdr + 4) != total ||
      !store.read(8, manifest, sizeof(manifest))) {
    strcpy(msg, "QSPI bootloader container parse failed"); return false;
  }
  MotaManifest m;
  if (!mota_parse_manifest(manifest, sizeof(manifest), m)) {
    strcpy(msg, "not a valid v3 bootloader package"); return false;
  }
  switch (ota_bootloader_confirmation_gate(m, installed, actual_mid, operator_mid,
                                            operator_hash8)) {
    case OTA_BOOT_CONFIRM_OK: break;
    case OTA_BOOT_CONFIRM_NOT_BOOT_PACKAGE: strcpy(msg, "not a bootloader package"); return false;
    case OTA_BOOT_CONFIRM_GEOMETRY: strcpy(msg, "bootloader package geometry mismatch"); return false;
    case OTA_BOOT_CONFIRM_LOCAL_IDENTITY: strcpy(msg, "installed bootloader identity is invalid"); return false;
    case OTA_BOOT_CONFIRM_TARGET: strcpy(msg, "bootloader board ID mismatch"); return false;
    case OTA_BOOT_CONFIRM_HW_ID: strcpy(msg, "bootloader signed hw_id mismatch"); return false;
    case OTA_BOOT_CONFIRM_MID: strcpy(msg, "bootloader MID confirmation mismatch"); return false;
    case OTA_BOOT_CONFIRM_IMAGE_HASH: strcpy(msg, "bootloader image-hash confirmation mismatch"); return false;
  }

  const OtaBlCaps current_caps = ota_bootloader_caps();
  if (!current_caps.present || current_caps.apply_abi < MOTA_BOOT_FORMAT_VER ||
      (current_caps.codec_mask & (1u << CODEC_FULL)) == 0 ||
      (current_caps.storage_flags & (OTA_BL_STORAGE_QSPI | OTA_BL_STORAGE_BOOT_UPDATE)) !=
          (OTA_BL_STORAGE_QSPI | OTA_BL_STORAGE_BOOT_UPDATE)) {
    strcpy(msg, "installed bootloader cannot safely self-update from QSPI"); return false;
  }

  const uint64_t payload_off64 = 8u + MOTA_MFL + (uint64_t)m.block_count * 4u;
  if (payload_off64 > UINT32_MAX || payload_off64 + OTA_BOOT_IMAGE_SIZE + 5u != total) {
    strcpy(msg, "bootloader container layout mismatch"); return false;
  }
  const uint32_t payload_off = (uint32_t)payload_off64;

  VerifyResult vr = ota_verify(static_cast<const OtaStore&>(store), allow);
  st.manifest_ok = vr.parsed;
  st.sig_ok = vr.sig_ok;
  st.trusted = vr.trusted;
  st.image_size = m.image_size;
  memcpy(st.image_hash, m.image_hash, sizeof(st.image_hash));
  if (!vr.auto_appliable()) {
    if (!vr.root_ok || !vr.payload_ok || !vr.image_ok)
      strcpy(msg, "bootloader payload hash mismatch");
    else if (!vr.sig_ok)
      strcpy(msg, "bootloader package signature invalid");
    else
      strcpy(msg, "bootloader signer is not in the trusted allowlist");
    return false;
  }

  uint8_t vectors[8];
  if (!store.read(payload_off, vectors, sizeof(vectors)) || !ota_bootloader_vector_sane(vectors)) {
    strcpy(msg, "bootloader vector table is invalid"); return false;
  }
  OtaBootloaderIdentity candidate;
  OtaBootloaderCapsMarker candidate_caps;
  if (!qspi_bootloader_image_metadata(store, payload_off, installed, candidate, candidate_caps) ||
      !ota_bootloader_identity_matches(installed, candidate)) {
    strcpy(msg, "candidate bootloader identity/capability/CRC mismatch"); return false;
  }
  st.slot_ok = true;
  if (!store.approve_for_bootloader()) {
    snprintf(msg, CAP, "QSPI bootloader handoff failed: %s", store.last_error()); return false;
  }
  strcpy(msg, "trusted bootloader verified and armed; rebooting after this reply");
  return true;
}
#endif
#endif

#else  // native / other platforms

bool ota_apply_slot_info(uint32_t*, uint32_t*) { return false; }
bool ota_apply_set_manifest(const uint8_t*, uint32_t, const SignerAllowlist&, ApplyState& st) { st = ApplyState(); return false; }
bool ota_apply_verify_slot(ApplyState&) { return false; }
bool ota_apply_commit() { return false; }
bool ota_apply_detools_mota(const uint8_t*, uint32_t, const SignerAllowlist&, ApplyState& st, char* msg) { st = ApplyState(); strcpy(msg, "unsupported"); return false; }
bool ota_apply_mota_nrf52(const uint8_t*, uint32_t, const SignerAllowlist&, ApplyState& st, char* msg) { st = ApplyState(); strcpy(msg, "unsupported"); return false; }
bool ota_rescue_mota_nrf52(const uint8_t*, uint32_t, const SignerAllowlist&, const uint8_t*,
                           uint32_t, ApplyState& st, char* msg) {
  st = ApplyState(); strcpy(msg, "unsupported"); return false;
}
void ota_reboot_to_apply() {}
void ota_reboot_to_bootloader_update() {}
bool ota_installed_bootloader_identity(OtaBootloaderIdentity& out) {
  out = OtaBootloaderIdentity(); return false;
}
uint8_t ota_bootloader_last_rc() { return 0; }

#endif

} // namespace ota
} // namespace mesh
