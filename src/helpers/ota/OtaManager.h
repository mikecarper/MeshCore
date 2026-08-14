#pragma once

#include <stdint.h>
#include <stddef.h>
#include "OtaFormat.h"
#include "OtaByteIO.h"
#include "OtaStore.h"
#include "MotaContainer.h"
#include "OtaSource.h"
#include "MerkleTree.h"

// Transport-agnostic OTA session engine (docs/ota_protocol.md Section 5/Section 8). It SERVES a complete `.mota`
// (answering GET_MANIFEST / REQ) and/or FETCHES one into an OtaStore (verifying every block against
// the signed merkle root via proofs). It is portable (no Arduino / radio / Ed25519) so it can be
// driven by a host simulation; a thin Mesh adapter wires it to PAYLOAD_TYPE_OTA on device.
//
// Transfer keeps 1 KB logical blocks. A server paces each block's self-describing DATA fragments through a
// bounded response queue and follows them with the Merkle PROOF. A newer fetcher waits briefly for that
// proactive proof; the existing REQ_PROOF message remains as a loss/legacy-server fallback.

namespace mesh {
namespace ota {

// Try to emit one OTA message (one packet payload). `flood`=true for announce/query, false for direct
// replies. False applies backpressure: the manager retains paced DATA/PROOF egress and tries it again.
typedef bool (*OtaSend)(void* ctx, const uint8_t* msg, uint16_t len, bool flood);

// Read `len` payload bytes at offset `off` from the serve source (flash-backed self-serve); false on
// error. nullptr means the payload is a contiguous RAM buffer (the staged `.mota`).
typedef bool (*ServeReadFn)(void* ctx, uint32_t off, uint8_t* buf, uint32_t len);

#ifndef OTA_PROOFGEN_SCRATCH
  #if defined(OTA_SD_STORE)
    #define OTA_PROOFGEN_SCRATCH 8192  // SD archive can seed <=2048 blocks (about 2 MB at 1 KB/block)
  #else
    #define OTA_PROOFGEN_SCRATCH 4096  // server proof-gen working buffer (supports up to 1024 blocks)
  #endif
#endif

#ifndef OTA_MAX_BLOCK
#define OTA_MAX_BLOCK 1024          // largest logical block (merkle leaf unit) = reassembly buffer size
#endif
#ifndef OTA_CHECKPOINT_BLOCKS
#define OTA_CHECKPOINT_BLOCKS 4     // persist progress (store.checkpoint) every N committed blocks (resume)
#endif
#ifndef OTA_ADVERT_INTERVAL_MINS
#define OTA_ADVERT_INTERVAL_MINS 1440   // re-advertise our served set every N minutes after the boot burst (24h)
#endif
#ifndef OTA_HOP_LIMIT_DEFAULT
#define OTA_HOP_LIMIT_DEFAULT 3         // default OTA flood reach in hops: accept <= N, relay while < N (0=direct)
#endif
#ifndef OTA_MF_FRAG
#define OTA_MF_FRAG 176             // manifest bytes per OTA_MANIFEST fragment (<= MAX_PACKET_PAYLOAD - header)
#endif
#ifndef OTA_MF_MAXFRAG
#define OTA_MF_MAXFRAG 4            // max manifest fragments (the fixed 197 B manifest is always 2)
#endif
#ifndef OTA_MANIFEST_MAX_RETRY
#define OTA_MANIFEST_MAX_RETRY 20   // give up (FAILED) after this many GET_MANIFEST retries - frees the slot
#endif
// Leaf-diff warm-start (motatool folder-capture only): bulk-fetch the target's leaves[], diff a seed build
// locally, pull DATA only for mismatches. OTA_LEAVES is bitmap-fragmented like OTA_MANIFEST so a want_mask
// retry never re-bursts (anti-deadlock). The mask is a fixed uint16, so leaves are capped at MAXFRAG frags.
#ifndef OTA_LEAVES_FRAG
#define OTA_LEAVES_FRAG 176         // leaf bytes per OTA_LEAVES fragment (<= MAX_PACKET_PAYLOAD - 7 header) = 44 leaves
#endif
#ifndef OTA_LEAVES_MAXFRAG
#define OTA_LEAVES_MAXFRAG 16       // fixed want_mask width (uint16) -> at most 16 leaf fragments
#endif
#ifndef OTA_DIFF_MAX_BLOCKS
#define OTA_DIFF_MAX_BLOCKS 704     // = MAXFRAG*(OTA_LEAVES_FRAG/4); warm-start unavailable above this (full fetch)
#endif
#ifndef OTA_LEAVES_MAX_RETRY
#define OTA_LEAVES_MAX_RETRY 20     // give up (FAILED) after this many GET_LEAVES retries
#endif
#ifndef OTA_DIFF_BATCH
#define OTA_DIFF_BATCH 48           // seed blocks diffed per loop tick (bounded so the diff never blocks the
#endif                              // main loop long enough to starve the radio / trip the watchdog)
#ifndef OTA_MAX_SOURCES
#define OTA_MAX_SOURCES 12          // heard OTA sources (beacon senders) tracked (LRU); ~12 B each
#endif
#ifndef OTA_MAX_SERVE
  #if defined(OTA_SD_STORE)
    #define OTA_MAX_SERVE 255        // protocol maximum: own fw plus up to 254 persistent SD archive entries
  #else
    #define OTA_MAX_SERVE 12         // mOTAs THIS node offers (own fw + external folder); == one HAVE fragment
  #endif
#endif
#ifndef OTA_MAX_SOURCE_OBJ
#define OTA_MAX_SOURCE_OBJ 4        // external MotaSource objects (folders/transports) attached at once
#endif
#ifndef OTA_SRC_MANIFEST_MAX
#define OTA_SRC_MANIFEST_MAX 256    // manifest-minus-leaves buffer for the loaded source mota (head+sig+approval)
#endif
#ifndef OTA_MAX_CATALOG
#define OTA_MAX_CATALOG 255          // protocol maximum; large SD seeders must remain browsable remotely
#endif
#ifndef OTA_INLINE_CATALOG
#define OTA_INLINE_CATALOG 12        // keep the common case static; expand to OTA_MAX_CATALOG only on demand
#endif
#if OTA_INLINE_CATALOG < 1
#error "OTA_INLINE_CATALOG must be at least 1"
#endif
#if OTA_INLINE_CATALOG > OTA_MAX_CATALOG
#undef OTA_INLINE_CATALOG
#define OTA_INLINE_CATALOG OTA_MAX_CATALOG
#endif
#ifndef OTA_QUERY_MIN_MS
#define OTA_QUERY_MIN_MS 300        // min delay before sending a catalog query (overhear-suppression window)
#endif
#ifndef OTA_QUERY_SPREAD_MS
#define OTA_QUERY_SPREAD_MS 4000    // random jitter span so 50 neighbours don't all query at once (storm)
#endif
#ifndef OTA_CATALOG_RETRY_MS
#define OTA_CATALOG_RETRY_MS 15000  // wait for a fragmented HAVE burst before requesting only its holes
#endif
#ifndef OTA_CATALOG_MAX_RETRY
#define OTA_CATALOG_MAX_RETRY 5     // bound retries until the source's next ADV or an explicit `ota ls`
#endif
#ifndef OTA_REQ_SPREAD_MS
#define OTA_REQ_SPREAD_MS 3000      // initial random hold before a fetch's first REQ (de-sync N fetchers)
#endif
#ifndef OTA_REQ_SUPPRESS_MS
#define OTA_REQ_SUPPRESS_MS 2500    // after overhearing a peer's REQ for a block, don't also request it -
#endif                              // its DATA is broadcast and will fill our hole too (swarm de-dup)
#ifndef OTA_SERVE_SUPPRESS_MS
#define OTA_SERVE_SUPPRESS_MS 1500  // don't re-serve a block whose DATA we just overheard another holder send
#endif                              // (so multiple sources of the same mota don't duplicate-broadcast it)
#ifndef OTA_FRAG_DATA
#define OTA_FRAG_DATA 160           // data bytes per DATA fragment (<= MAX_PACKET_PAYLOAD - 9-byte header)
#endif
#ifndef OTA_SERVE_QUEUE
#define OTA_SERVE_QUEUE 4           // requested blocks retained without allocating one packet per fragment
#endif
#if OTA_SERVE_QUEUE < 1
#error "OTA_SERVE_QUEUE must be at least 1"
#endif
#ifndef OTA_PROOF_GRACE_MS
#define OTA_PROOF_GRACE_MS 500      // wait for the server's proactive proof before legacy REQ_PROOF fallback
#endif
#ifndef OTA_FETCH_PIPELINE
#define OTA_FETCH_PIPELINE 2        // concurrent client block slots; keeps one block ready behind proof/flash work
#endif
#if OTA_FETCH_PIPELINE < 1
#error "OTA_FETCH_PIPELINE must be at least 1"
#endif
#ifndef OTA_FETCH_PIPELINE_INITIAL
  #if OTA_FETCH_PIPELINE >= 2
    #define OTA_FETCH_PIPELINE_INITIAL 2
  #else
    #define OTA_FETCH_PIPELINE_INITIAL 1
  #endif
#endif
#if OTA_FETCH_PIPELINE_INITIAL < 1 || OTA_FETCH_PIPELINE_INITIAL > OTA_FETCH_PIPELINE
#error "OTA_FETCH_PIPELINE_INITIAL must be between 1 and OTA_FETCH_PIPELINE"
#endif
#ifndef OTA_FETCH_PIPELINE_GROW_BLOCKS
#define OTA_FETCH_PIPELINE_GROW_BLOCKS 4  // clean verified blocks before adding one concurrent request slot
#endif
#if OTA_FETCH_PIPELINE_GROW_BLOCKS < 1
#error "OTA_FETCH_PIPELINE_GROW_BLOCKS must be at least 1"
#endif
#ifndef OTA_FETCH_PIPELINE_SHRINK_TICKS
#define OTA_FETCH_PIPELINE_SHRINK_TICKS 2 // consecutive no-progress retry ticks before removing one slot
#endif
#if OTA_FETCH_PIPELINE_SHRINK_TICKS < 1
#error "OTA_FETCH_PIPELINE_SHRINK_TICKS must be at least 1"
#endif
// nRF52 note: a flash page-erase halts the CPU (~85 ms, code runs from flash) and starves the LoRa RX,
// so writing to flash on every received packet drops in-flight DATA and the transfer stalls. The SD-safe
// driver (Adafruit flash_nrf5x) always erases on flush, so there is no erase-free write; instead
// OtaStoreFlashNrf52 COALESCES to the 4 KB page (the erase unit) and writes each page once - RAM stays
// O(one page), never O(mota). It pins flash page 0 (header+manifest+merkle leaves, which update all
// transfer long) in RAM and streams the payload through one sliding page buffer, flushing page 0 and the
// last page at finalize() (radio idle). Flash is then touched ~once per 4 KB (~1 per 4 blocks), not per
// packet; a small delta that fits page 0 does ZERO flash I/O until COMPLETE. (Pacing alone is not enough.)

class OtaManager {
public:
  OtaManager() = default;
  ~OtaManager();
  OtaManager(const OtaManager&) = delete;
  OtaManager& operator=(const OtaManager&) = delete;

  // PAUSED: a folder-destination write failed mid-transfer (the seeder link dropped). Progress is held on
  // the host; the manager stops requesting and does NOT fall back to RAM/flash. resumeStaged() (called on
  // reconnect) re-STATs the host file, recomputes which blocks are missing, and resumes.
  enum FetchState : uint8_t {
    IDLE, WANT_MANIFEST, WANT_LEAVES, VERIFYING_STAGED, FETCHING,
    COMPLETE, FAILED, PAUSED
  };

  // Sentinel for "no block" in the reassembly / peer-REQ / recently-served slots (a real block index is
  // a small uint16, so 0xFFFFFFFF is never valid).
  static const uint32_t NO_BLOCK = 0xFFFFFFFFu;

  // --- multi-mota serve ---  A ServeView is everything a serve handler needs for ONE mota. Two can be
  // resident: view0 = our own fw / a RAM `.mota` (always loaded), plus one on-demand source view that is
  // (re)loaded from a MotaSource when a request targets a different external mota. Requests dispatch by
  // manifest_id (carried in every fetch message) -> the matching ServeView via resolve().
  struct ServeView {
    bool          valid = false;
    MotaManifest  m;                       // parsed manifest (fields/pointers into the backing buffers)
    uint16_t      mfl = 0;                 // manifest-minus-leaves length (the OTA_MANIFEST payload)
    ServeReadFn   read = nullptr;          // payload reader (nullptr => m.payload is contiguous in RAM)
    void*         read_ctx = nullptr;
    uint8_t*      scratch = nullptr;       // proof-gen working buffer (>= block_count*4)
    uint32_t      scratch_sz = 0;
  };
  // A lightweight catalog entry: what we advertise per mota + how to load its ServeView on demand.
  struct ServeEntry {
    uint8_t     mid[4];
    uint32_t    target_id, fw_version;
    uint8_t     codec_id, flags;
    uint32_t    have_count;                // blocks we currently hold (== block_count when complete)
    bool        is_self;                   // true => entry is view0 (our own fw / RAM mota)
    MotaSource* src;                       // load from this external source ...
    uint8_t     src_idx;                   // ... at this index
    MotaDesc    desc;                      // cached region offsets for the source entry
  };
  // Context for the source-payload reader trampoline.
  struct SrcReadCtx { MotaSource* src; uint8_t idx; uint32_t payload_off; };

  void begin(uint32_t my_target_id, OtaSend send, void* ctx);

  // --- serve ---  Provide a complete, contiguous `.mota` to serve (caller keeps it alive).
  bool serve(const uint8_t* mota, uint32_t len);
  // Serve from a non-contiguous source (e.g. our own firmware in flash): a pre-assembled manifest
  // (manifest-minus-leaves, `mfl` bytes), the pre-computed merkle `leaves` (kept alive by caller), and a
  // `read` callback for payload blocks. Lets a node host its own image without holding it in RAM.
  bool serve_self(const uint8_t* manifest, uint16_t mfl, const uint8_t* leaves, uint32_t block_count,
                  uint8_t* proof_scratch, uint32_t proof_scratch_sz, ServeReadFn read, void* ctx);
  // Attach an external "folder" of `.mota` images (USB-serial daemon, BLE, WiFi URLs, NFS/samba, ...).
  // The node then advertises + RELAYS them transparently alongside its own fw - peers just see more mOTAs.
  // Re-enumerates the source into the serve registry. Returns false if no slots remain. (Trustless: the
  // fetcher verifies merkle+signature, so the source is never trusted - see OtaSource.h.)
  bool add_source(MotaSource* src);
  // Detach one source without disturbing other attached sources (for example, keep the persistent SD
  // archive attached while a temporary host-folder link comes and goes).
  bool remove_source(MotaSource* src);
  // Re-read every attached source's catalog (call when the folder's contents change). Rebuilds entries
  // [1..] from the sources; entry 0 (our own fw) is preserved.
  void refresh_sources();
  // Drop all external sources (keep serving our own fw).
  void clear_sources();
  // Stop serving the primary caller-owned image while preserving attached sources.
  // Call this before releasing or overwriting the primary image's backing buffer.
  void clear_primary();
  uint8_t servedCount() const { return _n_serve; }   // total mOTAs we offer (own fw + folder)
  // Read-only view of one served entry (for `ota serve` listing): mid/target/fwver/codec/flags + is_self.
  const ServeEntry* servedEntry(uint8_t i) const { return i < _n_serve ? &_serve[i] : nullptr; }
  // 4-byte fingerprint of our served set (== the set_digest carried in the beacon); for admin OTA stats.
  void servedDigest(uint8_t out[4]) const { setDigest(out); }

  // Broadcast the tiny per-node BEACON (OTA_ADV): seeder_id + count + set-digest of everything we serve.
  // Constant size regardless of how many mOTAs - peers ask for the catalog via OTA_QUERY only on interest.
  void announce();

  // --- fetch ---  Provide the staging store; fetching starts on a matching OTA_ADV.
  void set_fetch_store(OtaStore* s) { _fetch = s; }

  // Resume a fetch from a container already persisted in the store (after a reboot). want_mid=nullptr
  // accepts whatever is staged; otherwise only resumes if the staged manifest_id matches. Re-parses the
  // stored manifest, recomputes geometry, then incrementally rehashes every staged payload block before
  // continuing FETCHING the holes. A fully staged image also has to reproduce the manifest Merkle root
  // before it can become COMPLETE. Returns true if it adopted a staged container.
  bool resumeStaged(const uint8_t* want_mid);

  // Manual cross-target override (decision: deliberate role switch, e.g. companion -> repeater on the
  // same hardware). Normally a node only auto-fetches its OWN target_id; `want(T)` makes it accept an
  // ADV for target T instead (T=0 restores auto). The user takes responsibility for HW compatibility;
  // a hw_id brick-safety check is the planned safety layer (see docs/ota_protocol.md / plan).
  void want(uint32_t target_id) { _desired_target = target_id; reDiscover(); }
  uint32_t wanted() const { return _desired_target; }
  uint32_t target() const { return _target; }   // this node's own OTA target_id (set in begin)

  // Pull a SPECIFIC advertised mOTA by manifest_id (e.g. `ota pull <#>` picks the one more peers have),
  // not just any firmware for the target. mid=nullptr clears the filter (accept any mid for the target).
  void want_mid(const uint8_t* mid) {
    if (mid) { for (int i = 0; i < 4; i++) _desired_mid[i] = mid[i]; _have_desired_mid = true; }
    else _have_desired_mid = false;
    reDiscover();
  }

  // Begin fetching a chosen mid now (sets want + starts the manifest fetch / resume). Used by `ota pull`
  // once the user picks a catalogued mOTA (the source is reached via the flooded GET_MANIFEST).
  // `validate` enables the motatool folder-capture warm-start: bulk-fetch the target leaves, diff a seed
  // build already staged in the destination, and pull DATA only for the differing blocks. Ignored unless a
  // seed is present (a plain fetch just re-transfers everything). Normal P2P pulls pass false.
  void pull(const uint8_t* mid, uint32_t target, bool validate = false) {
    _archive_fetch = false;
    want(target); want_mid(mid); startFetch(mid, target, validate);
  }
  // Capture an advertised container for relaying, not installation. Archive pulls accept every codec and
  // target because the receiver only verifies and stores bytes; it never tries to apply the result locally.
  void pull_archive(const uint8_t* mid, uint32_t target) {
    _archive_fetch = true;
    want(target); want_mid(mid); startFetch(mid, target, false);
  }
  // Ask every known source for its catalog (populates `ota neighbors`). Async - rows arrive via OTA_HAVE.
  void queryAll();
  // Coarse clock for source/catalog ages + LRU (the Mesh adapter feeds millis; 0 in host tests is fine).
  void set_clock(uint32_t ms) { _now_ms = ms; }

  // Codec compatibility: a node only fetches/accepts firmware it can actually apply. ESP32 A/B accepts
  // full images; nRF52 single-slot does not and disables them (it requires an in-place delta). A manual
  // pull to an external folder may temporarily allow full images because it is capture, not install.
  void set_apply_codec(uint8_t c) { _apply_codec = c; }
  // A platform may apply MORE than one delta codec (ESP32 does both sequential AND in-place, so a single
  // in-place `.mota` can target both ESP32 and nRF52). 0xFF = unset.
  void set_apply_codec2(uint8_t c) { _apply_codec2 = c; }
  void set_accept_full(bool on) { _accept_full = on; }
  bool codecOk(uint8_t c) const {
    return (c == CODEC_FULL && _accept_full) || c == _apply_codec || c == _apply_codec2;
  }

  // Auto-fetch policy (manual `ota pull` always works regardless): 0=off (discover only), 1=any
  // compatible own-target advert, 2=only signed adverts. Conservative default = off.
  static const uint8_t AUTOFETCH_OFF = 0, AUTOFETCH_ANY = 1, AUTOFETCH_SIGNED = 2;
  void set_autofetch(uint8_t p) { _autofetch = p; reDiscover(); }
  uint8_t autofetch() const { return _autofetch; }
  // A persistent archive needs full catalogs even when install-oriented autofetch is off.
  void set_archive_interest(bool on) {
    if (_archive_interest != on) { _archive_interest = on; reDiscover(); }
  }
  bool archive_interest() const { return _archive_interest; }

  // Resume checkpoint cadence (runtime-tunable, persisted in NodePrefs): persist progress every N
  // committed blocks. 0 = never (resume only from a finalized container). Default OTA_CHECKPOINT_BLOCKS.
  void set_checkpoint_blocks(uint16_t n) { _checkpoint_blocks = n; }
  uint16_t checkpoint_blocks() const { return _checkpoint_blocks; }

  // Beacon re-advertise cadence in MINUTES (runtime-tunable, persisted in NodePrefs): after the boot burst,
  // re-send the discovery beacon every N minutes so a long-running node stays discoverable. 0 = disabled
  // (boot burst only). Default OTA_ADVERT_INTERVAL_MINS (24h).
  void set_advert_mins(uint16_t m) { _advert_mins = m; }
  uint16_t advert_mins() const { return _advert_mins; }

  // Max OTA flood reach in HOPS (runtime-tunable, persisted): a node accepts OTA from up to N hops away and
  // relays packets still under N hops; 0 = direct only (accept path_count 0, never relay). Bounds duty-cycle
  // when crossing repeaters. Default OTA_HOP_LIMIT_DEFAULT. Set via `ota config hops`.
  void set_max_hops(uint8_t h) { _max_hops = h; }
  uint8_t max_hops() const { return _max_hops; }
  bool fetched_is_signed() const { return (_fflags & MFLAG_SIGNED) != 0; }  // flags of the fetched manifest

  // This node's id (pubkey[0:4]), stamped into adverts we send so receivers can count distinct seeders.
  void set_seeder_id(const uint8_t* id4) { if (id4) for (int i = 0; i < 4; i++) _seeder_id[i] = id4[i]; }

  void on_message(const uint8_t* msg, uint16_t len);   // feed one received OTA message
  void loop();                                         // drive fetch (re-request missing blocks)
  // Fast, non-blocking service called from the main radio loop. It admits at most one retained server
  // response or proof fallback per call; the send callback provides packet-pool/queue backpressure.
  void serviceEgress();
  void clearPendingEgress();
  uint8_t pendingServeJobs() const { return _n_serve_jobs; }

  // Drop the current fetch session back to IDLE so a fresh `ota pull` / advert starts a new one.
  void reset_session() {
    _fstate = IDLE; _have = 0; _req_count = 0; _mf_retries = 0;
    clearReassembly();
    _loop_last_have = 0; _loop_last_progress = pipelineProgress();
    _mf_total = 0; _mf_mask = 0; _mf_len = 0; _loop_last_mfmask = 0;
    freeLeaves(); _validate = false; _archive_fetch = false;
    _lv_retries = 0; _loop_last_lvmask = 0;
    _resume_verify_idx = 0; _resume_invalidated = false;
    _resume_merkle.reset();
  }

  FetchState fetchState() const { return _fstate; }
  uint32_t blocksHave() const { return _have; }
  uint32_t blocksTotal() const { return _fbc; }
  uint8_t fetchPipelineWidth() const { return _pipeline_width; }
  static constexpr uint8_t fetchPipelineCapacity() { return OTA_FETCH_PIPELINE; }
  const uint8_t* fetchManifestId() const { return _fid; }

  // --- discovery catalog (for `ota neighbors`): mOTAs heard around us via OTA_HAVE, deduped by mid ---
  static const uint8_t OTA_CAT_SEEDERS = 4;   // distinct sources tracked per catalog row (for "N nodes have it")
  struct CatRow {
    uint8_t  mid[4];
    uint32_t target_id, fw_version;
    uint8_t  codec, flags;
    uint8_t  seeders[OTA_CAT_SEEDERS][4];  // distinct sources advertising this mid (deduped; capped)
    uint8_t  n_seeders;                    // count of the above (capped at OTA_CAT_SEEDERS) - "N+ nodes have it"
    uint32_t have_max;                     // best block-count any source reported (== total when a full copy exists)
    uint32_t last_ms;
    uint32_t retry_after_ms;                // per-image archive retry deadline (0 = eligible)
  };
  uint8_t catalogCount() const { return _n_cat; }
  const CatRow* catalogRow(uint8_t i) const { return i < _n_cat ? &catalogData()[i] : nullptr; }
  uint8_t sourceCount() const { return _n_src; }   // distinct OTA sources (beacon senders) heard
  void deferCatalog(const uint8_t mid[4], uint32_t until_ms);
  bool catalogReady(const CatRow* row, uint32_t now_ms) const {
    return row && (row->retry_after_ms == 0 || (int32_t)(now_ms - row->retry_after_ms) >= 0);
  }

private:
  CatRow* catalogData() { return _catalog_heap ? _catalog_heap : _catalog_inline; }
  const CatRow* catalogData() const { return _catalog_heap ? _catalog_heap : _catalog_inline; }
  uint16_t catalogCapacity() const { return _catalog_heap ? OTA_MAX_CATALOG : OTA_INLINE_CATALOG; }
  bool expandCatalog();

  bool emit(const uint8_t* b, uint16_t n, bool flood) {
    return _send && n && _send(_ctx, b, n, flood);
  }
  void handleAdv(const uint8_t* m, uint16_t n);     // beacon -> sources table (+ query if interested)
  void handleQuery(const uint8_t* m, uint16_t n);   // serve: reply OTA_HAVE catalog
  void handleHave(const uint8_t* m, uint16_t n);    // peer: catalog rows (+ startFetch if a row matches)
  void handleGetManifest(const uint8_t* m, uint16_t n);
  void handleManifest(const uint8_t* m, uint16_t n);
  void handleGetLeaves(const uint8_t* m, uint16_t n);    // serve: send the requested leaf fragments
  void handleLeaves(const uint8_t* m, uint16_t n);       // fetcher (validate): reassemble the target leaves
  bool beginLeafDiff();                                  // enter WANT_LEAVES if a seed diff is viable
  void diffStep();                                       // diff one batch of seed blocks per loop tick
  void freeLeaves();                                     // release the heap leaves buffer + diff state
  void handleReq(const uint8_t* m, uint16_t n);
  void handleData(const uint8_t* m, uint16_t n);
  void handleReqProof(const uint8_t* m, uint16_t n);
  void handleProof(const uint8_t* m, uint16_t n);
  void startFetch(const uint8_t* mid, uint32_t target, bool validate = false);   // begin/resume a fetch
  bool wantRow(const uint8_t* mid, uint32_t target, uint8_t codec, uint8_t flags) const;  // fetch this row?
  void clearReassembly();                                 // forget every in-flight pipeline slot
  void clearReassemblySlot(uint8_t slot);
  int findReassemblySlot(uint32_t block) const;
  int findEmptyReassemblySlot() const;
  bool blockInPipeline(uint32_t block) const;
  void notePipelineBlockVerified();
  void notePipelineStall();
  uint32_t pipelineProgress() const;
  uint32_t pickMissingBlock();                            // choose the next missing block not already in flight
  int  serveEntryIndex(const uint8_t* mid) const;         // registry slot serving this mid (-1 if none)
  ServeView* resolve(const uint8_t* mid);                 // pick/load the ServeView for this mid (nullptr)
  bool loadSource(const ServeEntry& e);                   // load an external mota into _srcv (head+leaves)
  void registerSelfEntry();                               // (re)build entry[0] from view0
  static bool srcReadTramp(void* c, uint32_t off, uint8_t* buf, uint32_t len);  // source payload reader
  bool queueServeJob(const uint8_t* mid, uint16_t block, uint16_t want_mask);
  bool loadActiveServeBlock();
  void popServeJob();
  void sendQuery(const uint8_t* seeder, const uint8_t* digest, uint32_t filter_target,
                 uint32_t want_fragments);                  // ask a source for all/selected catalog fragments
  void scheduleQuery(const uint8_t* seeder, const uint8_t* digest);   // jittered + suppressible
  void reDiscover() {
    for (uint8_t i = 0; i < _n_src; i++) {
      _sources[i].have_catalog = false;
      _sources[i].have_total = 0;
      _sources[i].have_mask = 0;
      _sources[i].query_pending = false;
      _sources[i].query_owned = false;
      _sources[i].query_retries = 0;
      _sources[i].query_retry_at = 0;
    }
  }
  void setDigest(uint8_t out[4]) const;                   // sha2-256:4 over our served mids
  bool blockPresent(uint32_t i) const;
  bool storedLeavesRootMatches() const;
  void beginStagedVerification();
  void verifyStagedStep();
  bool requestSlot(uint8_t slot);                         // request DATA holes or the proof for one slot
  bool fillPipeline();                                    // assign and request blocks until the window is full
  void requestMissing();                                  // fill the window, or retry one stalled slot
  uint32_t blockLen(uint32_t i) const;

  uint32_t _target = 0;
  OtaSend  _send = nullptr;
  void*    _ctx = nullptr;

  // serve (multi-mota): view0 = our own fw / a RAM `.mota`; _srcv = the currently-loaded external mota.
  ServeView   _view0;
  ServeView   _srcv;
  uint8_t     _srcv_mid[4] = {0};
  SrcReadCtx  _srcv_rdctx = {nullptr, 0, 0};
  ServeEntry  _serve[OTA_MAX_SERVE];                 // catalog (what we advertise) - entry 0 is view0
  uint8_t     _n_serve = 0;
  MotaSource* _src_list[OTA_MAX_SOURCE_OBJ] = {nullptr};
  uint8_t     _n_src_obj = 0;
  uint8_t     _src_manifest[OTA_SRC_MANIFEST_MAX];   // manifest-minus-leaves of the loaded source mota
#if defined(ESP32_PLATFORM)
  // Folder-source leaves and proof scratch are cold-path working storage. Keep
  // them off classic ESP32 .bss and allocate them on first use.
  uint8_t*    _src_leaves = nullptr;
  uint8_t*    _scratch = nullptr;
#else
  uint8_t     _src_leaves[OTA_PROOFGEN_SCRATCH];     // leaves[] of the loaded source mota (<=1024 blocks)
  uint8_t     _scratch[OTA_PROOFGEN_SCRATCH];        // proof-gen / fetch root-check working buffer
#endif

  // Server-side response descriptors are tiny. The active 1 KB block has its own buffer so proof generation
  // or a simultaneous fetch cannot overwrite DATA retained behind radio-queue backpressure.
  struct ServeJob {
    uint8_t mid[4] = {0};
    uint16_t block = 0;
    uint16_t pending_mask = 0;
    uint16_t emitted_mask = 0;
  };
  ServeJob   _serve_jobs[OTA_SERVE_QUEUE];
  uint8_t    _n_serve_jobs = 0;
  uint8_t    _serve_block[OTA_MAX_BLOCK];
  uint16_t   _serve_block_len = 0;
  bool       _serve_block_loaded = false;

  // fetch
  OtaStore*  _fetch = nullptr;
  FetchState _fstate = IDLE;
  uint8_t    _fid[4] = {0};
  uint8_t    _froot[4] = {0};
  uint32_t   _ftotal = 0, _fpoff = 0, _floff = 0, _fpsize = 0, _fbc = 0, _fbs = 0;
  uint32_t   _have = 0;
  uint32_t   _resume_verify_idx = 0;
  bool       _resume_invalidated = false;
  MerkleAccumulator _resume_merkle;
  uint32_t   _req_start = 0, _req_count = 0;   // most recently requested block + active window size
  uint32_t   _loop_last_have = 0;              // for stall detection in loop()
  uint32_t   _desired_target = 0;              // manual cross-target override (0 = auto / own target)
  uint8_t    _desired_mid[4] = {0,0,0,0};      // pull a specific manifest_id (see want_mid)
  bool       _have_desired_mid = false;
  uint8_t    _apply_codec = CODEC_DETOOLS_SEQUENTIAL;  // platform's delta codec (OtaContext sets it)
  uint8_t    _apply_codec2 = 0xFF;                     // optional 2nd accepted delta codec (ESP32: in-place)
  bool       _accept_full = true;                       // false on nRF52 flash (single-slot cannot apply full)
  bool       _archive_fetch = false;                    // capture-only pull: accept any target/codec
  bool       _archive_interest = false;                 // query full catalogs for the persistent archive
  uint8_t    _seeder_id[4] = {0,0,0,0};        // our node id (pubkey[0:4]) for advert seeder counting
  uint8_t    _autofetch = AUTOFETCH_OFF;       // auto-fetch policy (persisted in NodePrefs)
  uint16_t   _checkpoint_blocks = OTA_CHECKPOINT_BLOCKS;  // resume checkpoint cadence (persisted)
  uint16_t   _advert_mins = OTA_ADVERT_INTERVAL_MINS;    // beacon re-advertise cadence, minutes; 0=off (persisted)
  uint8_t    _max_hops = OTA_HOP_LIMIT_DEFAULT;          // OTA flood reach in hops; 0=direct only (persisted)
  uint8_t    _fflags = 0;                       // flags of the manifest currently being fetched
  // Bounded adaptive multi-block client pipeline. Each slot independently reassembles DATA and awaits its
  // Merkle proof, so proof/flash work for one block can overlap radio delivery of the next. The live window
  // grows on clean verified blocks and contracts after stalled service ticks; the wire protocol is unchanged.
  struct ReassemblySlot {
    uint32_t block = NO_BLOCK;
    uint16_t mask = 0;                         // received FRAG_DATA-slice bitmap
    uint16_t need = 0;                         // full bitmap for this block
    bool awaiting_proof = false;
    uint32_t proof_request_at = 0;              // proactive-proof grace deadline; 0 after fallback is sent
    uint8_t buf[OTA_MAX_BLOCK];
  };
  ReassemblySlot _reasm[OTA_FETCH_PIPELINE];
  uint32_t   _loop_last_progress = 0;           // aggregate pipeline progress for stall detection
  uint8_t    _retry_slot = 0;                   // round-robin stalled-slot retry cursor
  uint8_t    _pipeline_width = OTA_FETCH_PIPELINE_INITIAL; // live request window, adaptive up to array capacity
  uint8_t    _pipeline_success_streak = 0;      // verified blocks since the last grow/stall decision
  uint8_t    _pipeline_stall_streak = 0;        // consecutive no-progress service ticks
  // multi-fragment manifest reassembly (a signed v2 manifest exceeds one packet)
  uint8_t    _mf_buf[OTA_MF_MAXFRAG * OTA_MF_FRAG];   // sized to the fragment cap so no valid manifest is silently dropped
  uint16_t   _mf_retries = 0;                          // GET_MANIFEST retries while WANT_MANIFEST (give up after a cap)
  uint8_t    _mf_total = 0;                    // frag_total of the manifest being reassembled (0 = none)
  uint16_t   _mf_mask = 0;                     // received manifest-fragment bitmap
  uint16_t   _loop_last_mfmask = 0;            // manifest bitmap last loop tick (retry only on no-progress)
  uint32_t   _mf_len = 0;                      // assembled manifest length (set by the last fragment)

  // leaf-diff warm-start (motatool folder-capture only; buffer is heap-allocated only while WANT_LEAVES)
  bool       _validate = false;                // this fetch should try the seed leaf-diff before transferring
  uint8_t*   _leaves_buf = nullptr;            // target leaves[] (bc*4), malloc'd on WANT_LEAVES, freed after diff
  uint8_t    _lv_total = 0;                    // frag_total of the leaves reply being reassembled (0 = none)
  uint16_t   _lv_mask = 0;                     // received leaves-fragment bitmap
  uint16_t   _lv_retries = 0;                  // GET_LEAVES *stalled* retries while WANT_LEAVES
  uint16_t   _loop_last_lvmask = 0;            // leaves bitmap last loop tick (retry only on no-progress)
  bool       _diffing = false;                 // leaves are in; diffing the seed a batch per tick
  uint32_t   _diff_idx = 0;                    // next block index to diff against the seed

  uint8_t* ensureSourceLeaves();
  uint8_t* ensureScratch();

  // discovery: heard sources (beacon senders) + the catalog assembled from their OTA_HAVE replies
  struct Source {
    uint8_t seeder[4];
    uint8_t digest[4];
    uint8_t n_motas;
    uint32_t last_ms;
    bool have_catalog;
    uint8_t have_total;
    uint32_t have_mask;
    bool query_pending;
    bool query_owned;
    uint8_t query_retries;
    uint32_t query_at;
    uint32_t query_retry_at;
  };
  Source     _sources[OTA_MAX_SOURCES];
  uint8_t    _n_src = 0;
  CatRow     _catalog_inline[OTA_INLINE_CATALOG];
  CatRow*    _catalog_heap = nullptr;
  uint8_t    _n_cat = 0;
  uint32_t   _now_ms = 0;                       // coarse clock (fed by set_clock; for ages/LRU/jitter)
};

} // namespace ota
} // namespace mesh
