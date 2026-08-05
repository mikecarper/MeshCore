#pragma once

#include <stdio.h>          // snprintf (hw_id mismatch message)
#include <stdlib.h>         // malloc/free (lazy ESP32 manual-stage buffer)
#include <string.h>         // strncmp/strncpy (hw_id)
#include "OtaManager.h"
#include "OtaStore.h"
#include "SignerAllowlist.h"
#include "OtaApply.h"
#include "OtaFormat.h"
#include "OtaSelf.h"          // ota_self_firmware() - prefer self-describing EndF identity at begin()
#include "OtaBlInfo.h"        // bootloader OTA-apply capability marker (nRF52); cached after first read
#if defined(NRF52_PLATFORM) && defined(OTA_SD_STORE)
  #include "OtaStoreSdNrf52.h"
  #include "OtaCacheSdNrf52.h"
#elif defined(NRF52_PLATFORM) && defined(OTA_FLASH_STORE)
  #include "OtaStoreFlashNrf52.h"
#elif defined(ESP32_PLATFORM) && defined(OTA_FLASH_STORE)
  #include "OtaStoreFlashEsp32.h"
#endif
#if defined(OTA_FOLDER_SERIAL)
  #include "MotaSourceSerial.h"   // relay an external folder served by a host daemon over the USB serial
  #ifndef OTA_FOLDER_SERIAL_STREAM
    #define OTA_FOLDER_SERIAL_STREAM Serial      // default: the same USB console the CLI uses (no extra HW)
  #endif
  #ifndef OTA_FOLDER_SERIAL_BAUD
    #define OTA_FOLDER_SERIAL_BAUD 115200
  #endif
  // The console Serial is already begun by the example; a DEDICATED UART (override the stream) needs init,
  // so define OTA_FOLDER_SERIAL_BEGIN to have attach_folder() call .begin(baud) on it.
#endif

// Per-device OTA singleton shared by the CLI (OtaCli) and the mesh adapter (the example's MyMesh).
// Holds the session engine, a staging store (fetch), a RAM serve buffer, and the signer allowlist.
// nRF52 stages into FLASH (OtaStoreFlashNrf52): a delta can be 100 KB+, too big to hold in RAM, and the
// COMPLETE container must persist so the bootloader can apply it after reboot. A flash page-erase halts
// the CPU (~85 ms) and starves the LoRa RX, so the store COALESCES writes to the 4 KB page (the erase
// unit) and commits each page once, off the per-packet path (see OtaManager.h) - RAM stays O(one page).
// (v1 has no mid-transfer resume; an interrupted fetch simply restarts.) ESP32/native use the RAM store.

namespace mesh {
namespace ota {

class FolderMotaStore;   // pull destination over the seeder link (full type only where instantiated/used)

#ifndef OTA_SERVE_BUF_SIZE
  // nRF52 self-serving streams from flash; this buffer is only for the manual `ota dev stage` helper.
  // Keep it to one flash page so the OTA singleton does not consume another 16 KB of scarce SRAM.
  #if defined(NRF52_PLATFORM) && (defined(OTA_FLASH_STORE) || defined(OTA_SD_STORE))
    #define OTA_SERVE_BUF_SIZE 4096
  #else
    #define OTA_SERVE_BUF_SIZE 16384
  #endif
#endif
#ifndef OTA_FETCH_BUF_SIZE
#define OTA_FETCH_BUF_SIZE 16384
#endif

struct OtaContext {
  OtaManager manager;
#if defined(NRF52_PLATFORM) && defined(OTA_SD_STORE)
  OtaStoreSdNrf52 fetch_store;             // MeshTower V2: persistent SD staging, full + delta
  OtaCacheSdNrf52 sd_cache;                 // persistent capture + source for every OTA container heard
#elif defined(NRF52_PLATFORM) && defined(OTA_FLASH_STORE)
  OtaStoreFlashNrf52 fetch_store;            // persistent flash staging (survives reboot; large deltas)
#elif defined(ESP32_PLATFORM) && defined(OTA_FLASH_STORE)
  OtaStoreFlashEsp32 fetch_store;            // stages in the inactive A/B slot (delta + full, RX-safe)
#else
  OtaStoreRam<OTA_FETCH_BUF_SIZE> fetch_store;
#endif
  SignerAllowlist allow;
#if defined(ESP32_PLATFORM)
  // Manual `ota dev stage` is a diagnostic path. Reserving its full buffer in
  // .bss prevents high-capacity classic ESP32 images from linking, even when
  // the command is never used, so allocate it only while a manual stage exists.
  uint8_t* serve_buf = nullptr;
  bool ensureServeBuffer() {
    if (!serve_buf) serve_buf = static_cast<uint8_t*>(malloc(OTA_SERVE_BUF_SIZE));
    return serve_buf != nullptr;
  }
  void releaseServeBuffer() {
    free(serve_buf);
    serve_buf = nullptr;
  }
#else
  uint8_t  serve_buf[OTA_SERVE_BUF_SIZE];
  bool ensureServeBuffer() { return true; }
  void releaseServeBuffer() {}
#endif
  uint32_t serve_expected = 0;   // size declared by `ota stage`
  bool     serving = false;      // manager.serve() succeeded
  // flash-backed self-serve: cached merkle leaves (heap, freed on re-serve) + assembled manifest of our
  // own running firmware. The payload is read from flash per block; only the metadata is held in RAM.
  // serve_self_proof is the proof-gen working buffer (>= block_count*4) - sized to OUR image's block
  // count (the manager's fixed 4 KB scratch only covers <=1024 blocks; a >1 MB image needs more).
  uint8_t* serve_self_leaves = nullptr;
  uint8_t* serve_self_proof  = nullptr;
  uint8_t  serve_self_manifest[MOTA_MFL];   // fixed-layout full+unsigned manifest-minus-leaves (197 B)
  ApplyState apply_st;           // pending apply (P6)

  // OTA policy (persisted via NodePrefs; autofetch lives in the manager). Conservative defaults: a fresh
  // node discovers + announces but never fetches/installs without operator intent.
  static const uint8_t AUTOINSTALL_OFF = 0, AUTOINSTALL_TRUSTED = 1;
  uint8_t  autoinstall = AUTOINSTALL_OFF;   // 1 = auto-apply a COMPLETE fetch IF signed + allowlisted
  bool     config_dirty = false;            // CLI set a policy/key -> CommonCLI persists + clears
  char     hw_id[33] = {0};                 // this device's hardware tag (from board.getOtaHwId(), set in begin)

  // ---- Pull destinations (`ota pull <#> <dest>`): where a fetched .mota is staged. `flash` (fetch_store,
  //      always present) or `folder` - an external host folder over the seeder link, registered by the app
  //      while a motatool `serve` connection is attached (else no `folder` dest is offered). Captures the
  //      container to the host as <mid>.mota so an exact firmware copy can be pulled for delta-building. ----
  FolderMotaStore* folder_dest = nullptr;   // non-null == a folder destination is currently connected
  char     folder_dest_info[24] = {0};      // human id of the link (e.g. "tcp 192.168.4.5", "serial")
  void set_folder_dest(FolderMotaStore* fs, const char* info) {
    folder_dest = fs;
    strncpy(folder_dest_info, info ? info : "?", sizeof(folder_dest_info) - 1);
    folder_dest_info[sizeof(folder_dest_info) - 1] = 0;
  }
  void clear_folder_dest() { folder_dest = nullptr; folder_dest_info[0] = 0; }

  // True if the staged .mota's hw_id is compatible with this device: equal tags, or either side empty
  // ("unknown" -> can't enforce -> permissive). Brick-safety gate for apply (esp. manual cross-target).
  bool hwMatches(const uint8_t* mhw /*32B, may be null*/) const {
    if (!hw_id[0] || !mhw) return true;
    bool declared = false; for (int i = 0; i < 32; i++) if (mhw[i]) { declared = true; break; }
    if (!declared) return true;
    return strncmp((const char*)mhw, hw_id, 32) == 0;
  }

  // Apply the COMPLETE fetched .mota (platform dispatch) and arm the slot; sets apply_pending on success
  // so the deferred-reboot path (mesh loop) takes over. Caller ensures the fetch is COMPLETE. Shared by
  // manual `ota applydelta` and the auto-install path.
  bool apply_fetched(char* msg) {
#if defined(NRF52_PLATFORM) && defined(OTA_SD_STORE)
    if (sdCacheFetching()) {
      strncpy(msg, "refused: SD archive capture owns the OTA receive slot", 96);
      msg[95] = 0;
      return false;
    }
#endif
    // hardware-compatibility gate (brick-safety) - refuse a .mota whose hw_id is for different hardware,
    // independent of signature; covers a manual cross-target `ota dev want` onto an incompatible board.
    {
      uint8_t hdr[8], mb[256];
      uint32_t total = fetch_store.staged_size();
      if (total >= 13 && fetch_store.read(0, hdr, 8) && memcmp(hdr, MOTA_MAGIC, 4) == 0) {
        uint32_t mr = total - 8; if (mr > sizeof(mb)) mr = sizeof(mb);
        MotaManifest mm;
        if (fetch_store.read(8, mb, mr) && mota_parse_manifest(mb, mr, mm) && !hwMatches(mm.hw_id)) {
          char want[33] = {0}; memcpy(want, mm.hw_id, 32);
          snprintf(msg, 96, "refused: .mota hw_id '%.32s' != this device '%s' (incompatible hardware)", want, hw_id);
          return false;
        }
      }
    }
    bool ok;
#if defined(NRF52_PLATFORM) && defined(OTA_SD_STORE)
    ok = ota_apply_mota_nrf52(fetch_store, allow, apply_st, msg);
#elif defined(NRF52_PLATFORM)
    ok = ota_apply_mota_nrf52(fetch_store.data(), fetch_store.staged_size(), allow, apply_st, msg);
#elif defined(ESP32_PLATFORM) && defined(OTA_FLASH_STORE)
    ok = ota_apply_detools_mota(fetch_store, allow, apply_st, msg);
#else
    ok = ota_apply_detools_mota(fetch_store.data(), fetch_store.staged_size(), allow, apply_st, msg);
#endif
    if (ok) apply_pending = true;
    return ok;
  }

  // Deferred apply-reboot: a verified `ota applydelta` approves the update but does NOT reboot inline,
  // so the CLI can first deliver the "verified; applying" reply (over LoRa it's the only way the
  // operator learns the apply started). The mesh loop then calls ota_reboot_to_apply() once that reply
  // has actually been transmitted. apply_at/apply_hard are mesh-clock deadlines the loop fills in.
  bool     apply_pending = false;
  uint32_t apply_at = 0;         // earliest reboot time (lets the reply get queued + start sending)
  uint32_t apply_hard = 0;       // hard cap, in case the TX queue never idles on a busy node

  // Bootloader OTA-apply capability (nRF52): can THIS device's bootloader apply a .mota? Read from flash
  // ONCE (the scan is ~40 KB) and cached in RAM. On other platforms present=false (apply is in-app).
  OtaBlCaps _bl_caps;
  bool      _bl_caps_read = false;
  const OtaBlCaps& bootloaderCaps() {
    if (!_bl_caps_read) { _bl_caps = ota_bootloader_caps(); _bl_caps_read = true; }
    return _bl_caps;
  }

  // --- discovery: the "what mOTAs are available around me" view ----------------------------------
  // The catalog (heard mOTAs) + the heard-sources table now live in OtaManager (built from beacons +
  // OTA_HAVE catalog replies, the two-tier discovery). `ota neighbors` renders manager.catalogRow();
  // `ota pull` acts on a mid. Here we only keep the fetch-session age stamp.
  uint32_t session_started_ms = 0;   // when the fetch session last left IDLE (for the age display)
  uint8_t  prev_fstate = OtaManager::IDLE;
  bool     folder_active = false;    // an external `.mota` folder is attached + being relayed
  enum FolderLink : uint8_t {
    FOLDER_LINK_NONE = 0,
    FOLDER_LINK_SERIAL,
    FOLDER_LINK_TCP,
  };
  FolderLink folderLink() const { return _folder_link; }

  bool attach_folder_source(MotaSource* source, FolderLink link, const char* label,
                            char* msg, size_t cap) {
    if (!msg || cap == 0) return false;
    if (!source || link == FOLDER_LINK_NONE) {
      strncpy(msg, "ERR invalid folder source", cap);
      msg[cap - 1] = 0;
      return false;
    }
    if (folder_active && _folder_link != link) {
      snprintf(msg, cap, "ERR folder already attached via %s",
               _folder_link == FOLDER_LINK_TCP ? "tcp" : "serial");
      return false;
    }
    if (folder_active && _folder_source == source) {
      manager.refresh_sources();
      snprintf(msg, cap, "OK folder refreshed (%s) - serving %u mOTA total",
               label ? label : "external", (unsigned)manager.servedCount());
      return true;
    }
    if (folder_active && _folder_source) {
      manager.remove_source(_folder_source);       // replace this link without detaching the SD archive
      folder_active = false;
      _folder_link = FOLDER_LINK_NONE;
      _folder_source = nullptr;
    }
    if (!manager.add_source(source)) {
      strncpy(msg, "ERR no free source slot", cap);
      msg[cap - 1] = 0;
      return false;
    }
    folder_active = true;
    _folder_link = link;
    _folder_source = source;
    snprintf(msg, cap, "OK folder attached (%s) - serving %u mOTA total (own fw + folder)",
             label ? label : "external", (unsigned)manager.servedCount());
    return true;
  }

  // Attach/detach an external folder of `.mota` served by a host daemon over the seeder UART (the node
  // then advertises + relays them alongside its own fw). Only built when OTA_FOLDER_SERIAL is configured.
#if defined(OTA_FOLDER_SERIAL)
  bool attach_folder(char* msg, size_t cap) {
    static SerialMotaSource src(OTA_FOLDER_SERIAL_STREAM, 600);
#ifdef OTA_FOLDER_SERIAL_BEGIN
    OTA_FOLDER_SERIAL_STREAM.begin(OTA_FOLDER_SERIAL_BAUD);     // dedicated UART; console is already up
#endif
    return attach_folder_source(&src, FOLDER_LINK_SERIAL, "serial", msg, cap);
  }
#endif
  void detach_folder() {
    if (_folder_source) manager.remove_source(_folder_source);
    folder_active = false;
    _folder_link = FOLDER_LINK_NONE;
    _folder_source = nullptr;
  }

#if defined(NRF52_PLATFORM) && defined(OTA_SD_STORE)
  // Initialize and attach the persistent source lazily. This lets Mesh::begin finish quickly when no card
  // is inserted, while archive interest is already enabled so early OTA advertisements still get queried.
  bool ensureSdCache() {
    sd_cache.attach(fetch_store);
    if (!sd_cache.initialize()) return false;
    if (!_sd_cache_source_attached) {
      if (!manager.add_source(&sd_cache)) return false;
      _sd_cache_source_attached = true;
    }
    manager.set_archive_interest(sd_cache.autoCaptureEnabled());
    return true;
  }

  bool setSdCacheEnabled(bool enabled) {
    if (!ensureSdCache()) return false;
    bool saved = sd_cache.setAutoCaptureEnabled(enabled);
    // The marker create/remove can succeed even if the card's final sync reports late. Keep the live
    // manager consistent with the cache object's actual state while still reporting the persistence error.
    bool active = sd_cache.autoCaptureEnabled();
    manager.set_archive_interest(active);
    if (!active) stopSdCacheFetch();
    else if (saved) manager.queryAll();
    return saved;
  }

  bool sdCacheFetching() const { return _sd_cache_fetching; }

  // Give install-oriented/manual work immediate ownership of OtaManager's single receive slot. The partial
  // archive remains on SD and is resumed later instead of being discarded.
  void stopSdCacheFetch() {
    if (!_sd_cache_fetching) return;
    sd_cache.checkpoint();
    manager.reset_session();
    manager.want(0);
    manager.want_mid(nullptr);
    manager.set_fetch_store(&fetch_store);
    _sd_cache_fetching = false;
  }

  // Close every archive handle before card-wide format/erase, then rebuild the source view afterwards.
  void prepareSdCardReset() {
    stopSdCacheFetch();
    if (_sd_cache_source_attached) manager.remove_source(&sd_cache);
    _sd_cache_source_attached = false;
    sd_cache.resetMedia();
    manager.set_fetch_store(&fetch_store);
    manager.set_archive_interest(true);        // a freshly formatted card defaults to capture on
    _sd_cache_init_retry_at = 0;
  }

  void finishSdCardReset(uint32_t now) {
    sd_cache.resetMedia();
    _sd_cache_init_retry_at = now;
    ensureSdCache();
  }

  void serviceSdCache(uint32_t now) {
    if (!_sd_cache_source_attached) {
      if (_sd_cache_init_retry_at && (int32_t)(now - _sd_cache_init_retry_at) < 0) return;
      if (!ensureSdCache()) {
        _sd_cache_init_retry_at = now + 60000UL;
        return;
      }
      _sd_cache_init_retry_at = 0;
    }

    if (_sd_cache_fetching) {
      OtaManager::FetchState state = manager.fetchState();
      if (state == OtaManager::COMPLETE) {
        _sd_cache_fetching = false;            // cache store already finalized + published the file
        manager.reset_session();
        manager.want(0);
        manager.want_mid(nullptr);
        manager.set_fetch_store(&fetch_store);
        manager.refresh_sources();
        manager.announce();                    // peers learn the expanded persistent served set immediately
        _sd_cache_next_at = now + 3000UL;
      } else if (state == OtaManager::FAILED || state == OtaManager::PAUSED) {
        manager.deferCatalog(_sd_cache_mid, now + 300000UL);
        stopSdCacheFetch();                    // retain .part; a later pass resumes its verified blocks
        _sd_cache_next_at = now + 3000UL;
      } else if (state == OtaManager::IDLE) {
        // A CLI/reset path may have reset the manager directly. Never leave the archive ownership flag
        // wedged merely because the shared manager reached IDLE outside this service function.
        stopSdCacheFetch();
        _sd_cache_next_at = now + 3000UL;
      }
      return;
    }

    if (!sd_cache.autoCaptureEnabled() || apply_pending || manager.fetchState() != OtaManager::IDLE ||
        (_sd_cache_next_at && (int32_t)(now - _sd_cache_next_at) < 0)) return;

    for (uint8_t i = 0; i < manager.catalogCount(); i++) {
      const OtaManager::CatRow* row = manager.catalogRow(i);
      if (!row || row->have_max == 0 || sd_cache.contains(row->mid)) continue;
      if (!manager.catalogReady(row, now)) continue;

      memcpy(_sd_cache_mid, row->mid, 4);
      sd_cache.set_mid(row->mid);
      manager.set_fetch_store(&sd_cache);
      _sd_cache_fetching = true;
      manager.pull_archive(row->mid, row->target_id);
      return;
    }
  }
#endif

  void track_session(uint8_t fstate, uint32_t now) {            // stamp the session start (age display)
    if (fstate != prev_fstate) {
      if (prev_fstate == OtaManager::IDLE && fstate != OtaManager::IDLE) session_started_ms = now;
      prev_fstate = fstate;
    }
  }

  void begin(uint32_t target_id, OtaSend send, void* ctx, const char* hw = nullptr) {
    // Prefer the firmware's SELF-DESCRIBING EndF identity (docs Section 2) over the build-flag values the caller
    // passed - it's correct on any build (build.sh injection, bare IDE build, ...), so `ota ls`/`status`
    // and fetch-routing show the right hardware/role instead of 0 / "".
    SelfFwInfo _fi;
    if (ota_self_firmware(_fi) && _fi.valid) {
      if (_fi.target_id) target_id = _fi.target_id;
      if (_fi.hw_id[0]) hw = _fi.hw_id;
    }
    manager.begin(target_id, send, ctx);
    if (hw) { strncpy(hw_id, hw, sizeof(hw_id) - 1); hw_id[sizeof(hw_id) - 1] = 0; }
    // a node only fetches firmware it can apply: ESP32 A/B -> sequential, nRF52 single-slot -> in-place
#if defined(NRF52_PLATFORM) && defined(OTA_SD_STORE)
    manager.set_accept_full(true);
    manager.set_apply_codec(CODEC_DETOOLS_INPLACE);
#elif defined(NRF52_PLATFORM)
    manager.set_accept_full(false);                       // single-slot bootloader applies deltas only
    manager.set_apply_codec(CODEC_DETOOLS_INPLACE);
#elif defined(ESP32_PLATFORM)
    manager.set_accept_full(true);
    manager.set_apply_codec(CODEC_DETOOLS_SEQUENTIAL);   // preferred (streams straight to the slot)
    manager.set_apply_codec2(CODEC_DETOOLS_INPLACE);     // also accepted -> a single in-place .mota fits both
#endif
    manager.set_fetch_store(&fetch_store);
#if defined(NRF52_PLATFORM) && defined(OTA_SD_STORE)
    sd_cache.attach(fetch_store);
    manager.set_archive_interest(true);        // default on; the SD marker can turn it off at first mount
#endif
  }

private:
  FolderLink _folder_link = FOLDER_LINK_NONE;
  MotaSource* _folder_source = nullptr;
#if defined(NRF52_PLATFORM) && defined(OTA_SD_STORE)
  bool _sd_cache_source_attached = false;
  bool _sd_cache_fetching = false;
  uint8_t _sd_cache_mid[4] = {0};
  uint32_t _sd_cache_init_retry_at = 0;
  uint32_t _sd_cache_next_at = 0;
#endif
};

OtaContext& ota_ctx();   // process-wide singleton

} // namespace ota
} // namespace mesh
