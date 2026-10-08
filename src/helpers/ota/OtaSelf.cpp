#include "OtaSelf.h"
#include "FirmwareInfo.h"
#include "OtaByteIO.h"
#include <string.h>

#if defined(ESP32_PLATFORM)
  #include "esp_ota_ops.h"
  #include "esp_partition.h"
#elif defined(NRF52_PLATFORM)
  #include "OtaFlashLayout_nrf52.h"
#endif

#if defined(ESP32_PLATFORM) || defined(NRF52_PLATFORM)
  #include "OtaContext.h"     // serve our own fw from flash (cache leaves, read payload on demand)
  #include "MerkleTree.h"
  #include <SHA256.h>
  #include <stdlib.h>
  #ifndef OTA_SELF_LEAVES_MAX
  #define OTA_SELF_LEAVES_MAX 65536u   // cap heap for cached leaves (~16k blocks @1 KB = up to ~16 MB image)
  #endif
#endif

namespace mesh {
namespace ota {

#if defined(ESP32_PLATFORM)
// Scan the running app partition for the firmware's EndF trailer using esp_partition_read (stable
// across IDF versions - no mmap). Same rule as find_self_firmware(): the marker's absolute offset
// must equal its stored body_len, which uniquely identifies the running firmware's own trailer.
// The active app is immutable until reboot: OTA writes its inactive peer. Cache only a successful
// read, keyed to the current running partition's geometry, so status polling never rescans the app.
bool ota_self_firmware(SelfFwInfo& out) {
  struct MetadataCache {
    bool valid = false;
    uint32_t address = 0;
    uint32_t size = 0;
    SelfFwInfo info;
  };
  static MetadataCache cache;
  out = SelfFwInfo();
  const esp_partition_t* p = esp_ota_get_running_partition();
  if (!p) { cache.valid = false; return false; }
  if (cache.valid && cache.address == p->address && cache.size == p->size) {
    out = cache.info;
    return true;
  }
  // Missing/changed partitions and failed scans must not resurrect a previous app's metadata.
  cache.valid = false;

  const uint32_t CH = 512;
  uint8_t buf[CH + ENDF_LEN];                 // overlap so a marker spanning a chunk edge is still seen
  for (uint32_t base = 0; base + ENDF_LEN <= p->size; base += CH) {
    uint32_t want = CH + ENDF_LEN;
    if (base + want > p->size) want = p->size - base;
    if (esp_partition_read(p, base, buf, want) != ESP_OK) return false;
    for (uint32_t i = 0; i + ENDF_LEN <= want; i++) {
      if (buf[i] != ENDF_MAGIC[0]) continue;
      if (memcmp(buf + i, ENDF_MAGIC, 4) != 0) continue;
      uint32_t body_len = rd_u32le(buf + i + 4);
      if (body_len != base + i) continue;     // must sit immediately after a body of that length
      // Read the complete fixed trailer before publishing or caching ANY metadata. A transient
      // failure here must remain retryable, not become a valid entry with missing identity fields.
      uint8_t tr[ENDF_LEN];
      if (body_len > p->size - ENDF_LEN ||
          esp_partition_read(p, body_len, tr, ENDF_LEN) != ESP_OK) return false;
      if (memcmp(tr, ENDF_MAGIC, 4) != 0 || rd_u32le(tr + 4) != body_len) return false;
      out.valid = true;
      out.endf_offset = body_len;
      out.body_len = body_len;
      out.image_len = body_len + ENDF_LEN;
      memcpy(out.body_hash, tr + 8, 8);
      out.fw_version = rd_u32le(tr + 16);
      out.target_id = rd_u32le(tr + 20);
      memcpy(out.hw_id, tr + 24, 32); out.hw_id[32] = 0;
      cache.address = p->address;
      cache.size = p->size;
      cache.info = out;
      cache.valid = true;
      return true;
    }
  }
  return false;
}
#elif defined(NRF52_PLATFORM)
// nRF52 internal flash is memory-mapped, so the running app is directly scannable. The body starts at
// APP_BASE; find_self_firmware() picks the EndF whose stored body_len equals its offset (the running
// firmware's own trailer), ignoring any staged `.mota` (which carries its own embedded EndF) higher up.
bool ota_self_firmware(SelfFwInfo& out) {
  const uint32_t app_base = mota_nrf52_app_base();
  const uint32_t stage_ceiling =
#if defined(OTA_SD_STORE)
      MOTA_NRF52_APP_END;
#else
      mota_nrf52_layout_stage_ceiling();
#endif
  if (!mota_nrf52_layout_valid(app_base, stage_ceiling)) { out = SelfFwInfo(); return false; }
  const uint8_t* region = (const uint8_t*)(uintptr_t)app_base;
  const uint32_t region_len = stage_ceiling - app_base;
  return find_self_firmware(region, region_len, out, /*verify_body=*/true);
}
#else
bool ota_self_firmware(SelfFwInfo& out) {
  // STM32/RP2040: app-region access lands with their apply path.
  out = SelfFwInfo();
  return false;
}
#endif

bool ota_self_firmware_for_display(SelfFwInfo& out) {
#if defined(NRF52_PLATFORM)
  struct DisplayCache {
    bool valid = false;
    uint32_t app_base = 0;
    uint32_t stage_ceiling = 0;
    SelfFwInfo info;
  };
  static DisplayCache cache;
  out = SelfFwInfo();
  const uint32_t app_base = mota_nrf52_app_base();
  const uint32_t stage_ceiling =
#if defined(OTA_SD_STORE)
      MOTA_NRF52_APP_END;
#else
      mota_nrf52_layout_stage_ceiling();
#endif
  if (!mota_nrf52_layout_valid(app_base, stage_ceiling)) {
    cache.valid = false;
    return false;
  }
  if (cache.valid && cache.app_base == app_base && cache.stage_ceiling == stage_ceiling) {
    out = cache.info;
    return true;
  }
  // App updates reboot, so only diagnostic metadata needs a per-boot snapshot. Keep all explicit
  // verification and apply/base/headroom checks on the fresh accessor: a display hit proves nothing
  // about later flash mutations. Never cache failure or retain metadata from a different layout.
  cache.valid = false;
  if (!ota_self_firmware(out) || !out.valid) {
    out = SelfFwInfo();
    return false;
  }
  cache.app_base = app_base;
  cache.stage_ceiling = stage_ceiling;
  cache.info = out;
  cache.valid = true;
  return true;
#else
  return ota_self_firmware(out);
#endif
}

#if defined(ESP32_PLATFORM)
bool ota_self_read(uint32_t off, uint8_t* buf, uint32_t len) {
  const esp_partition_t* p = esp_ota_get_running_partition();
  return p && esp_partition_read(p, off, buf, len) == ESP_OK;
}
#elif defined(NRF52_PLATFORM)
bool ota_self_read(uint32_t off, uint8_t* buf, uint32_t len) {
  const uint32_t app_base = mota_nrf52_app_base();
#if defined(OTA_SD_STORE)
  if (app_base >= MOTA_NRF52_APP_END || (uint64_t)app_base + off + len > MOTA_NRF52_APP_END) return false;
#else
  const uint32_t stage_ceiling = mota_nrf52_layout_stage_ceiling();
  if (!mota_nrf52_layout_valid(app_base, stage_ceiling) ||
      (uint64_t)app_base + off + len > stage_ceiling) return false;
#endif
  memcpy(buf, (const uint8_t*)(uintptr_t)(app_base + off), len);
  return true;
}
#else
bool ota_self_read(uint32_t, uint8_t*, uint32_t) { return false; }
#endif

#if defined(ESP32_PLATFORM) || defined(NRF52_PLATFORM)
static bool self_read_cb(void* ctx, uint32_t off, uint8_t* buf, uint32_t len) {
  (void)ctx; return ota_self_read(off, buf, len);
}
// Build (once) the full-image manifest + merkle leaves for the running firmware, cache them in `c`, and
// hand the manager a flash-read callback for the payload. The image is read ONCE here to compute the
// leaves + image_hash; thereafter a block REQ reads only that block (proof comes from the cached leaves).
// Pack the first "MAJOR.MINOR.PATCH" found in `s` into the comparable uint32 the manifest uses
// (MAJOR<<24 | MINOR<<16 | PATCH<<8). Returns 0 if there's no dotted number (e.g. a "dev-<sha>" build).
static uint32_t parse_fw_version(const char* s) {
  if (!s) return 0;
  for (; *s; s++) {                                   // find the start of a "d.d" run
    if (*s < '0' || *s > '9') continue;
    const char* p = s; uint32_t a = 0, b = 0, d = 0; int dots = 0;
    uint32_t* cur = &a;
    for (; *p; p++) {
      if (*p >= '0' && *p <= '9') { *cur = *cur * 10 + (uint32_t)(*p - '0'); }
      else if (*p == '.' && dots < 2) { dots++; cur = (dots == 1) ? &b : &d; }
      else break;
    }
    if (dots >= 1) return FwVersion{ (uint8_t)a, (uint8_t)b, (uint8_t)d, 0 }.pack();
    s = p - 1;                                        // a bare number, no dots - keep scanning
  }
  return 0;
}

bool ota_serve_self(OtaContext& c, uint32_t fw_version) {
#if defined(RAK4631_COMBINED_ETHERNET)
  // Also cover diagnostic `ota dev serve self`, which bypasses the public
  // default-self-serve gate. Ethernet owns the alternative RAM workspace.
  if (rak4631_ethernet_owns_spi()) return false;
#endif
  // Derive our version from the build string when the caller didn't supply one, so the mOTA we advertise
  // carries a real version (was hard-coded 0 -> peers saw "v0.0.0"). A dev build with no dotted number
  // still reads 0 - the self-describing EndF identity (docs) is the durable fix for that.
#ifdef FIRMWARE_VERSION
  if (fw_version == 0) fw_version = parse_fw_version(FIRMWARE_VERSION);
#endif
  SelfFwInfo fi;
  if (!ota_self_firmware(fi) || !fi.valid) return false;
  // 2 KiB logical blocks (delivered as multiple LoRa fragments) reduce Merkle/proof overhead and give each
  // independent transport-DEFLATE stream more useful history than the deployed 1 KiB geometry. A ~530 KiB
  // image is about 265 blocks (roughly 1 KiB of leaves) instead of ~4,240 128-byte leaves.
  const uint32_t image_size = fi.image_len, BS = OTA_DEFAULT_BLOCK_SIZE;
  const uint32_t bc = image_size / BS + (image_size % BS != 0 ? 1u : 0u);
  if ((uint64_t)bc * 4 > OTA_SELF_LEAVES_MAX) return false;

  // A repeated `serve self` replaces caller-owned buffers. Revoke the old
  // view and queued replies before freeing them; OOM/read failure must leave
  // serving disabled rather than advertise dangling pointers. Avoid holding
  // two images' metadata at once on boards with little free heap.
  c.manager.clear_primary();
  c.serving = false;
  free(c.serve_self_leaves); free(c.serve_self_proof);
  c.serve_self_leaves = (uint8_t*)malloc((size_t)bc * 4);
  size_t proof_size = (size_t)bc * 4;
#if MESHCORE_OTA_DEVICE_DEFLATE
  // Proof generation and transport encoding use this buffer at different
  // times. Small nRF52 images can have <2 KiB of leaves: keep enough room for
  // a whole encoded block without allocating a second input/output buffer.
  // Receiver-only/Companion diagnostic raw exports retain the old allocation.
  if (proof_size < BS) proof_size = BS;
#endif
  c.serve_self_proof  = (uint8_t*)malloc(proof_size);
  if (!c.serve_self_leaves || !c.serve_self_proof) {
    free(c.serve_self_leaves); free(c.serve_self_proof);
    c.serve_self_leaves = c.serve_self_proof = nullptr;
    return false;
  }

  SHA256 sha; uint8_t blk[BS];
  for (uint32_t i = 0, off = 0; i < bc; i++, off += BS) {
    uint32_t blen = (off + BS <= image_size) ? BS : (image_size - off);
    if (!ota_self_read(off, blk, blen)) {
      free(c.serve_self_leaves); free(c.serve_self_proof);
      c.serve_self_leaves = c.serve_self_proof = nullptr;
      return false;
    }
    merkle_leaf(c.serve_self_leaves + (size_t)i * 4, blk, blen);
    sha.update(blk, blen);
  }
  uint8_t image_hash[32]; sha.finalize(image_hash, 32);
  uint8_t root[4]; merkle_root(root, c.serve_self_leaves, bc);

  // Prefer the SELF-DESCRIBING identity embedded in our own EndF (docs Section 2) over build flags / the param -
  // it's correct regardless of how the firmware was built (build.sh injection, IDE, etc.).
  uint32_t out_target = fi.target_id ? fi.target_id : c.manager.target();
  uint32_t out_ver    = fi.fw_version ? fi.fw_version : fw_version;
  const char* out_hw  = fi.hw_id[0] ? fi.hw_id : c.hw_id;

  // Assemble the fixed-layout manifest-minus-leaves (full, unsigned) = MOTA_MFL bytes. base_hash(89),
  // signer_pubkey(97) and signature(129) stay zero-filled (full + unsigned); only `approval` is set.
  uint8_t* m = c.serve_self_manifest;
  memset(m, 0, MOTA_MFL);
  m[0] = MOTA_FORMAT_VER; m[1] = MFLAG_FULL; m[2] = HASH_ALGO_SHA256;
  wr_u32le(m + 3, out_target); wr_u32le(m + 7, out_ver);
  wr_u32le(m + 11, image_size); wr_u32le(m + 15, image_size);   // full: payload == image
  m[19] = 11;                                 // block_size_log2 = 11 (2048 B logical block)
  memcpy(m + 20, root, 4);
  memcpy(m + 24, image_hash, 32);
  m[56] = CODEC_FULL;
  memcpy(m + 57, out_hw, strlen(out_hw) < 32 ? strlen(out_hw) : 32);   // hw_id[32] (NUL-padded by memset)
  memcpy(m + MOTA_OFF_APPROVAL, APPROVAL_NOT, 4);   // approval (fetching device's apply-gate handles it)
  c.serving = c.manager.serve_self(m, MOTA_MFL, c.serve_self_leaves, bc,
                                 c.serve_self_proof, proof_size, self_read_cb, nullptr);
  return c.serving;
}
#endif

} // namespace ota
} // namespace mesh
