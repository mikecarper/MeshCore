#include "OtaManager.h"
#include "OtaProtocol.h"
#include "MerkleTree.h"
#include "Multihash.h"
#include "OtaByteIO.h"
#include "OtaDebug.h"
#include <string.h>
#include <stdlib.h>   // malloc/free for the (transient) leaf-diff buffer

namespace mesh {
namespace ota {

OtaManager::~OtaManager() {
  free(_catalog_heap);
}

bool OtaManager::expandCatalog() {
  if (_catalog_heap || OTA_INLINE_CATALOG >= OTA_MAX_CATALOG) return false;
  CatRow* expanded = static_cast<CatRow*>(malloc(sizeof(CatRow) * OTA_MAX_CATALOG));
  if (!expanded) return false;
  memcpy(expanded, _catalog_inline, sizeof(CatRow) * _n_cat);
  _catalog_heap = expanded;
  return true;
}

uint8_t* OtaManager::ensureSourceLeaves() {
#if defined(ESP32_PLATFORM)
  if (!_src_leaves) {
    _src_leaves = static_cast<uint8_t*>(malloc(OTA_PROOFGEN_SCRATCH));
  }
#endif
  return _src_leaves;
}

uint8_t* OtaManager::ensureScratch() {
#if defined(ESP32_PLATFORM)
  if (!_scratch) {
    _scratch = static_cast<uint8_t*>(malloc(OTA_PROOFGEN_SCRATCH));
  }
#endif
  return _scratch;
}

void OtaManager::begin(uint32_t my_target_id, OtaSend send, void* ctx) {
  _target = my_target_id; _send = send; _ctx = ctx;
  _fstate = IDLE; _have = 0; _fbc = 0;
  _fetch_error = FETCH_ERROR_NONE;
  _archive_fetch = false; _validate = false;
  clearFetchIntent();
  _resume_verify_idx = 0; _resume_invalidated = false;
  _resume_merkle.reset();
  _n_serve = 0; _n_src_obj = 0; _view0.valid = false; _srcv.valid = false;
  memset(_src_offered, 0, sizeof(_src_offered));
  memset(_src_advertised, 0, sizeof(_src_advertised));
  _n_src = 0; _n_cat = 0;
  clearPendingEgress();
}

// ---------------- serve (multi-mota registry) ----------------
//
// A node offers a SET of mOTAs: its own firmware (view0) plus any external "folder" sources (OtaSource).
// Every fetch message carries the manifest_id, so a request dispatches to the matching ServeView via
// resolve() - view0 is resident; an external mota is (re)loaded on demand into _srcv. The catalog (what
// we advertise / answer OTA_QUERY with) is the lightweight _serve[] registry.

bool OtaManager::serve(const uint8_t* mota, uint32_t len) {
  uint8_t* scratch = ensureScratch();
  if (!scratch) return false;
  if (!mota_parse(mota, len, _view0.m)) return false;
  _view0.mfl = (uint16_t)(_view0.m.leaves - _view0.m.manifest_start);  // contiguous container
  _view0.read = nullptr; _view0.read_ctx = nullptr;                    // payload is contiguous _view0.m.payload
  _view0.scratch = scratch; _view0.scratch_sz = OTA_PROOFGEN_SCRATCH;  // <=1024 blocks (RAM .mota is small)
  _view0.valid = true;
  clearPendingEgress();
  if (_n_src_obj) refresh_sources(); else registerSelfEntry();
  return true;
}

bool OtaManager::serve_self(const uint8_t* manifest, uint16_t mfl, const uint8_t* leaves,
                            uint32_t block_count, uint8_t* proof_scratch, uint32_t proof_scratch_sz,
                            ServeReadFn read, void* ctx) {
  if (proof_scratch_sz < (uint64_t)block_count * 4) return false;   // proof-gen needs count*4 working bytes
  if (!mota_parse_manifest(manifest, mfl, _view0.m)) return false;  // fixed fields: root, image_hash, sizes
  _view0.m.manifest_start = manifest;
  _view0.m.leaves   = leaves;        // pre-computed, caller-owned (heap)
  _view0.m.payload  = nullptr;       // read on demand via `read`
  _view0.m.block_count = block_count;
  _view0.mfl = mfl; _view0.read = read; _view0.read_ctx = ctx;
  _view0.scratch = proof_scratch; _view0.scratch_sz = proof_scratch_sz;   // sized for our (large) image
  _view0.valid = true;
  clearPendingEgress();
  if (_n_src_obj) refresh_sources(); else registerSelfEntry();
  return true;
}

// (Re)build registry slot 0 from view0 (our own fw / RAM mota). Keeps any source entries in [1..].
void OtaManager::registerSelfEntry() {
  if (!_view0.valid) return;
  if (_n_serve > 0 && !_serve[0].is_self) {
    if (_n_serve < OTA_MAX_SERVE) _n_serve++;
    for (uint8_t i = _n_serve - 1; i > 0; i--) {
      _serve[i] = _serve[i - 1];
    }
  } else if (_n_serve == 0) {
    _n_serve = 1;
  }
  ServeEntry& e = _serve[0];
  memcpy(e.mid, _view0.m.merkle_root, 4);
  e.target_id = _view0.m.target_id; e.fw_version = _view0.m.fw_version;
  e.codec_id = _view0.m.codec_id; e.flags = _view0.m.flags; e.have_count = _view0.m.block_count;
  e.is_self = true; e.src = nullptr; e.src_idx = 0;
}

bool OtaManager::add_source(MotaSource* src) {
  if (!src) return false;
  for (uint8_t i = 0; i < _n_src_obj; i++) {
    if (_src_list[i] == src) { refresh_sources(); return true; }
  }
  if (_n_src_obj >= OTA_MAX_SOURCE_OBJ) return false;
  _src_list[_n_src_obj] = src;
  _src_offered[_n_src_obj] = 0;
  _src_advertised[_n_src_obj] = 0;
  _n_src_obj++;
  refresh_sources();
  return true;
}

bool OtaManager::remove_source(MotaSource* src) {
  if (!src) return false;
  for (uint8_t i = 0; i < _n_src_obj; i++) {
    if (_src_list[i] != src) continue;
    for (uint8_t j = i + 1; j < _n_src_obj; j++) {
      _src_list[j - 1] = _src_list[j];
      _src_offered[j - 1] = _src_offered[j];
      _src_advertised[j - 1] = _src_advertised[j];
    }
    _n_src_obj--;
    _src_list[_n_src_obj] = nullptr;
    _src_offered[_n_src_obj] = 0;
    _src_advertised[_n_src_obj] = 0;
    refresh_sources();
    return true;
  }
  return false;
}

void OtaManager::refresh_sources() {
  clearPendingEgress();
  _n_serve = 0;
  if (_view0.valid) registerSelfEntry();
  for (uint8_t s = 0; s < _n_src_obj; s++) {
    _src_offered[s] = 0;
    _src_advertised[s] = 0;
    MotaSource* src = _src_list[s];
    if (!src) continue;
    uint8_t cnt = src->count();
    _src_offered[s] = cnt;
    for (uint8_t i = 0; i < cnt; i++) {
      if (_n_serve >= OTA_MAX_SERVE) break;
      MotaDesc d;
      if (!src->describe(i, d)) continue;
      if (d.block_count == 0 || (uint64_t)d.block_count * 4 > OTA_PROOFGEN_SCRATCH) continue;
      // A legacy serial descriptor left the geometry byte zero. Inspect its fixed manifest once so it is
      // either proven servable before advertisement or excluded from the registry.
      if (d.block_size_log2 == 0) {
        uint8_t mf[MOTA_MFL];
        MotaManifest parsed;
        if (!src->read(i, 8, mf, sizeof(mf)) || !mota_parse_manifest(mf, sizeof(mf), parsed) ||
            memcmp(parsed.merkle_root, d.mid, 4) != 0 || parsed.block_count != d.block_count ||
            parsed.payload_size != d.payload_size) continue;
        d.block_size_log2 = parsed.block_size_log2;
      }
      if (d.block_size_log2 >= 32 || (1UL << d.block_size_log2) > OTA_MAX_BLOCK) continue;
      if (d.leaves_off != 8 + MOTA_MFL ||
          (uint64_t)d.leaves_off + (uint64_t)d.block_count * 4 != d.payload_off ||
          (uint64_t)d.payload_off + d.payload_size + 5 != d.total_size) continue;
      if (serveEntryIndex(d.mid) >= 0) continue;             // already offered (e.g. our own fw in the folder)
      ServeEntry& e = _serve[_n_serve++];
      memcpy(e.mid, d.mid, 4);
      e.target_id = d.target_id; e.fw_version = d.fw_version;
      e.codec_id = d.codec_id; e.flags = d.flags; e.have_count = d.block_count;   // a folder mota is fully held
      e.is_self = false; e.src = src; e.src_idx = i; e.desc = d;
      _src_advertised[s]++;
    }
  }
  _srcv.valid = false;                        // a loaded source view may now be stale; reloads on demand
}

bool OtaManager::sourceStats(const MotaSource* src, uint16_t& offered, uint16_t& advertised) const {
  offered = 0;
  advertised = 0;
  if (!src) return false;
  for (uint8_t i = 0; i < _n_src_obj; i++) {
    if (_src_list[i] != src) continue;
    offered = _src_offered[i];
    advertised = _src_advertised[i];
    return true;
  }
  return false;
}

void OtaManager::clear_sources() {
  clearPendingEgress();
  _n_src_obj = 0; _srcv.valid = false;
  memset(_src_list, 0, sizeof(_src_list));
  memset(_src_offered, 0, sizeof(_src_offered));
  memset(_src_advertised, 0, sizeof(_src_advertised));
  _n_serve = 0;
  if (_view0.valid) registerSelfEntry();
}

void OtaManager::clear_primary() {
  clearPendingEgress();
  _view0.valid = false;
  if (_n_src_obj) refresh_sources();
  else _n_serve = 0;
}

int OtaManager::serveEntryIndex(const uint8_t* mid) const {
  for (uint8_t i = 0; i < _n_serve; i++)
    if (memcmp(_serve[i].mid, mid, 4) == 0) return i;
  return -1;
}

OtaManager::ServeView* OtaManager::resolve(const uint8_t* mid) {
  if (_view0.valid && memcmp(mid, _view0.m.merkle_root, 4) == 0) return &_view0;
  if (_srcv.valid  && memcmp(mid, _srcv_mid, 4) == 0)             return &_srcv;
  int i = serveEntryIndex(mid);
  if (i < 0) return nullptr;
  if (_serve[i].is_self) return _view0.valid ? &_view0 : nullptr;
  return loadSource(_serve[i]) ? &_srcv : nullptr;
}

// Load an external mota into the on-demand _srcv: read its manifest-minus-leaves + leaves[] from the
// source into RAM, parse, and wire a payload reader that streams blocks from the source on REQ. (The
// payload itself is NOT held in RAM - only the small head and the leaves, bounded by the configured
// proof-generation scratch size.)
bool OtaManager::loadSource(const ServeEntry& e) {
  const MotaDesc& d = e.desc;
  if (d.leaves_off < 8) return false;
  if (!e.src) return false;
  uint16_t mfl = (uint16_t)(d.leaves_off - 8);
  if (mfl == 0 || mfl > sizeof(_src_manifest)) return false;
  if (d.block_count == 0 || (uint64_t)d.block_count * 4 > OTA_PROOFGEN_SCRATCH) return false;
  uint8_t* src_leaves = ensureSourceLeaves();
  uint8_t* scratch = ensureScratch();
  if (!src_leaves || !scratch) return false;
  bool ok = e.src->read(e.src_idx, 8, _src_manifest, mfl);
  if (!ok || !mota_parse_manifest(_src_manifest, mfl, _srcv.m)) return false;
  if (_srcv.m.block_size() == 0 || _srcv.m.block_size() > OTA_MAX_BLOCK) return false;
  if (memcmp(_srcv.m.merkle_root, d.mid, 4) != 0) return false;       // descriptor/bytes disagree
  if (_srcv.m.block_count != d.block_count) return false;
  ok = e.src->read(e.src_idx, d.leaves_off, src_leaves, d.block_count * 4);
  if (!ok) return false;
  _srcv.m.manifest_start = _src_manifest;
  _srcv.m.leaves   = src_leaves;
  _srcv.m.payload  = nullptr;
  _srcv.mfl = mfl;
  _srcv_rdctx.src = e.src; _srcv_rdctx.idx = e.src_idx;
  _srcv_rdctx.payload_off = d.payload_off;
  _srcv.read = srcReadTramp; _srcv.read_ctx = &_srcv_rdctx;
  _srcv.scratch = scratch; _srcv.scratch_sz = OTA_PROOFGEN_SCRATCH;
  memcpy(_srcv_mid, d.mid, 4);
  _srcv.valid = true;
  return true;
}

// ServeReadFn trampoline: payload-relative offset -> absolute read of the backing (external source or fetch store).
bool OtaManager::srcReadTramp(void* c, uint32_t off, uint8_t* buf, uint32_t len) {
  SrcReadCtx* x = (SrcReadCtx*)c;
  return x->src->read(x->idx, x->payload_off + off, buf, len);
}

// sha2-256:4 over the SORTED set of mids we serve - peers use it to tell if our offering changed. Sorting
// makes it canonical across nodes regardless of insert order; for a single mota it is mh4(mid) (unchanged).
void OtaManager::setDigest(uint8_t out[4]) const {
  if (_n_serve == 0) { memset(out, 0, 4); return; }
  uint8_t order[OTA_MAX_SERVE];
  for (uint8_t i = 0; i < _n_serve; i++) order[i] = i;
  for (uint8_t i = 1; i < _n_serve; i++) {                 // insertion sort by mid
    uint8_t v = order[i]; int j = (int)i - 1;
    while (j >= 0 && memcmp(_serve[order[j]].mid, _serve[v].mid, 4) > 0) { order[j+1] = order[j]; j--; }
    order[j+1] = v;
  }
  uint8_t cat[OTA_MAX_SERVE * 4];
  for (uint8_t i = 0; i < _n_serve; i++) memcpy(cat + (uint32_t)i * 4, _serve[order[i]].mid, 4);
  mh4(out, cat, (size_t)_n_serve * 4);
}

void OtaManager::announce() {       // tiny per-node beacon (constant size, independent of how many mOTAs)
  AdvMsg a;
  memcpy(a.seeder_id, _seeder_id, 4);
  a.n_motas = _n_serve;
  setDigest(a.set_digest);
  uint8_t b[16];
  emit(b, encode_adv(b, sizeof(b), a), true);
}

// OTA_QUERY: two roles. (1) OVERHEAR-SUPPRESSION - any node that has a pending query for the same
// {source,digest} cancels it (someone else already asked; the broadcast HAVE is coming). (2) If the query
// is addressed to US, reply with our catalog (broadcast, tagged with our digest so every overhearer caches
// it). All served mOTAs matching filter_target are returned, fragmented if they exceed one packet.
void OtaManager::handleQuery(const uint8_t* m, uint16_t n) {
  QueryMsg q;
  if (!decode_query(m, n, q)) return;
  // Suppress only a pending query whose scope is covered by the overheard request. A target-filtered or
  // fragment-only query cannot stand in for our unfiltered initial request.
  for (uint8_t i = 0; i < _n_src; i++) {
    Source& s = _sources[i];
    if (!s.query_pending || memcmp(s.seeder, q.seeder_id, 4) != 0 ||
        memcmp(s.digest, q.set_digest, 4) != 0 || q.filter_target != 0) continue;
    uint32_t missing = 0;
    if (s.have_total) {
      uint32_t full = s.have_total >= 32 ? UINT32_MAX : ((1UL << s.have_total) - 1);
      missing = full & ~s.have_mask;
    }
    if (q.want_fragments == 0 || (missing != 0 && (q.want_fragments & missing) == missing)) {
      s.query_pending = false;
      s.query_retry_at = _now_ms + OTA_CATALOG_RETRY_MS;
    }
  }
  if (_n_serve == 0 || memcmp(q.seeder_id, _seeder_id, 4) != 0) return;   // (2) only WE answer queries to us
  uint8_t dg[4]; setDigest(dg);
  const uint8_t per = (uint8_t)((MAX_PACKET_PAYLOAD - 12) / OTA_HAVE_ROW_BYTES);  // rows per HAVE fragment
  uint8_t ftotal = (uint8_t)((_n_serve + per - 1) / per); if (ftotal == 0) ftotal = 1;
  if (ftotal > OTA_HAVE_MAX_FRAGMENTS) return;             // QueryMsg's recovery bitmap cannot represent it

  // Fragment positions are canonical across rescans: digest-stable sets sort into the same pages even if
  // FAT/folder enumeration order changed. filter_target removes rows from those pages but never renumbers
  // fragments, so filtered and unfiltered HAVE floods cannot corrupt each other's completion bitmap.
  uint8_t order[OTA_MAX_SERVE];
  for (uint8_t i = 0; i < _n_serve; i++) order[i] = i;
  for (uint8_t i = 1; i < _n_serve; i++) {
    uint8_t v = order[i]; int j = (int)i - 1;
    while (j >= 0 && memcmp(_serve[order[j]].mid, _serve[v].mid, 4) > 0) {
      order[j + 1] = order[j]; j--;
    }
    order[j + 1] = v;
  }
  for (uint8_t fi = 0; fi < ftotal; fi++) {
    if (q.want_fragments != 0 && !(q.want_fragments & (1UL << fi))) continue;
    const uint8_t first = (uint8_t)(fi * per);
    uint8_t rowbuf[MAX_PACKET_PAYLOAD];
    uint8_t cnt = 0;
    uint16_t end = (uint16_t)first + per; if (end > _n_serve) end = _n_serve;
    for (uint16_t pos = first; pos < end; pos++) {
      const ServeEntry& e = _serve[order[pos]];
      if (q.filter_target != 0 && q.filter_target != e.target_id) continue;
      uint8_t* row = rowbuf + (uint32_t)cnt * OTA_HAVE_ROW_BYTES;
      memcpy(row, e.mid, 4);
      wr_u32le(row + 4, e.target_id); wr_u32le(row + 8, e.fw_version);
      row[12] = e.codec_id; row[13] = e.flags;
      uint32_t hc = e.have_count > 0xFFFFu ? 0xFFFFu : e.have_count;
      row[14] = (uint8_t)(hc & 0xFF); row[15] = (uint8_t)(hc >> 8);
      cnt++;
    }
    HaveMsg hv; memcpy(hv.seeder_id, _seeder_id, 4); memcpy(hv.set_digest, dg, 4);
    hv.frag_idx = fi; hv.frag_total = ftotal; hv.n_rows = cnt;
    hv.rows = cnt ? rowbuf : nullptr;
    uint8_t b[MAX_PACKET_PAYLOAD];
    emit(b, encode_have(b, sizeof(b), hv), true);            // broadcast: all neighbours cache it
  }
}

bool OtaManager::handleGetManifest(const uint8_t* m, uint16_t n) {
  GetManifestMsg gm;
  if (!decode_get_manifest(m, n, gm)) return false;
  ServeView* v = resolve(gm.manifest_id);
  if (!v) return false;
  OTA_DBG("OTA: GET_MANIFEST want=%04x pending=%u\n",
          (unsigned)gm.want_mask, (unsigned)_n_manifest_jobs);
  // Retain the request and let serviceEgress admit its fragments one at a time. In particular, a deployed
  // receiver with the legacy 32-second SF5 flood delay must receive both fragments from the first request;
  // serializing fragment 1 behind the receiver's next GET would exhaust its manifest retry window.
  const bool queued = queueManifestJob(v->m.merkle_root, gm.want_mask);
  if (!queued) {
    OTA_DBG("OTA: GET_MANIFEST response queue full\n");
  }
  // A node that successfully queued the requested MID is the terminal source. Do not also re-flood the
  // request; that echo competes with the manifest on the same temporary-radio channel. If the bounded queue
  // is full, return false so another source or a later retry can service it.
  return queued;
}

// Serve the target's merkle leaves[] in fragments (for a motatool folder-capture warm-start). Only the
// fragments set in want_mask are emitted, so a want_mask retry re-sends just the holes - never a full burst
// (same anti-deadlock rationale as OTA_MANIFEST). This is the only leaf-diff piece that runs on every node.
void OtaManager::handleGetLeaves(const uint8_t* m, uint16_t n) {
  GetLeavesMsg gl;
  if (!decode_get_leaves(m, n, gl)) return;
  ServeView* v = resolve(gl.manifest_id);
  if (!v || !v->m.leaves) return;
  uint32_t leaves_len = v->m.block_count * 4;
  uint8_t ftotal = (uint8_t)((leaves_len + OTA_LEAVES_FRAG - 1) / OTA_LEAVES_FRAG); if (ftotal == 0) ftotal = 1;
  for (uint8_t fi = 0; fi < ftotal; fi++) {
    if (!(gl.want_mask & (1u << fi))) continue;      // fetcher didn't ask for this leaves fragment
    uint32_t off = (uint32_t)fi * OTA_LEAVES_FRAG;
    uint32_t fl = leaves_len - off; if (fl > OTA_LEAVES_FRAG) fl = OTA_LEAVES_FRAG;
    LeavesMsg lm;
    memcpy(lm.manifest_id, v->m.merkle_root, 4);
    lm.frag_idx = fi; lm.frag_total = ftotal;
    lm.bytes = v->m.leaves + off; lm.len = (uint16_t)fl;
    uint8_t b[MAX_PACKET_PAYLOAD];
    emit(b, encode_leaves(b, sizeof(b), lm), false);
  }
}

// Smallest mask covering `nf` fragments: bit k set for k in [0, nf). Caps at 16 (matches each pipeline
// slot mask and the manifest reassembly), which bounds a block at 16 fragments - our 1 KB blocks are 7.
static inline uint16_t frag_full_mask(uint32_t nf) {
  return (nf >= 16) ? 0xFFFFu : (uint16_t)((1u << nf) - 1);
}

// Retain a bounded descriptor instead of allocating every DATA packet inline. Repeated requests that arrive
// while a block is already queued merge only fragments not yet admitted to the radio queue; a later retry can
// enqueue the block again if one of those admitted packets was actually lost over the air.
bool OtaManager::queueServeJob(const uint8_t* mid, uint16_t block, uint16_t want_mask) {
  for (uint8_t i = 0; i < _n_serve_jobs; i++) {
    ServeJob& job = _serve_jobs[i];
    if (job.block != block || memcmp(job.mid, mid, 4) != 0) continue;
    job.pending_mask |= (uint16_t)(want_mask & ~job.emitted_mask);
    if (want_mask == 0) job.proof_requested = true;
    return true;
  }
  if (_n_serve_jobs >= OTA_SERVE_QUEUE) return false;
  ServeJob& job = _serve_jobs[_n_serve_jobs++];
  memcpy(job.mid, mid, 4);
  job.block = block;
  job.pending_mask = want_mask;
  job.emitted_mask = 0;
  job.proof_ready_at = 0;
  job.proof_requested = want_mask == 0;
  return true;
}

bool OtaManager::queueManifestJob(const uint8_t* mid, uint16_t want_mask) {
  for (uint8_t i = 0; i < _n_manifest_jobs; i++) {
    ManifestServeJob& job = _manifest_jobs[i];
    if (memcmp(job.mid, mid, 4) != 0) continue;
    job.pending_mask |= (uint16_t)(want_mask & ~job.emitted_mask);
    return true;
  }
  if (_n_manifest_jobs >= OTA_MANIFEST_SERVE_QUEUE) return false;
  ManifestServeJob& job = _manifest_jobs[_n_manifest_jobs++];
  memcpy(job.mid, mid, 4);
  job.pending_mask = want_mask;
  job.emitted_mask = 0;
  job.ready_at = _now_ms + manifestEgressGapMs();
  return true;
}

uint32_t OtaManager::manifestEgressGapMs() const {
  if (_radio_packet_airtime_ms == 0) return OTA_MANIFEST_EGRESS_MIN_GAP_MS;
  uint64_t gap = (uint64_t)_radio_packet_airtime_ms * _tx_spacing_permille;
  gap = (gap + 999u) / 1000u;
  if (gap < OTA_MANIFEST_EGRESS_MIN_GAP_MS) gap = OTA_MANIFEST_EGRESS_MIN_GAP_MS;
  if (gap > OTA_MANIFEST_EGRESS_MAX_GAP_MS) gap = OTA_MANIFEST_EGRESS_MAX_GAP_MS;
  return (uint32_t)gap;
}

uint32_t OtaManager::proofEgressGapMs() const {
  if (_radio_packet_airtime_ms == 0) return OTA_PROOF_EGRESS_MIN_GAP_MS;
  // The adapter may have one response transmitting and two more admitted. Start this timer when the final
  // DATA is accepted, so cover all three packet-service intervals before opening the legacy-request turn.
  uint64_t gap = (uint64_t)_radio_packet_airtime_ms * _tx_spacing_permille
      * OTA_PROOF_EGRESS_DRAIN_PACKETS;
  gap = (gap + 999u) / 1000u;
  if (gap < OTA_PROOF_EGRESS_MIN_GAP_MS) gap = OTA_PROOF_EGRESS_MIN_GAP_MS;
  if (gap > OTA_PROOF_EGRESS_MAX_GAP_MS) gap = OTA_PROOF_EGRESS_MAX_GAP_MS;
  return (uint32_t)gap;
}

void OtaManager::clearPendingEgress() {
  _n_manifest_jobs = 0;
  _n_serve_jobs = 0;
  _serve_block_len = 0;
  _serve_block_loaded = false;
}

void OtaManager::popManifestJob() {
  if (_n_manifest_jobs == 0) return;
  for (uint8_t i = 1; i < _n_manifest_jobs; i++) _manifest_jobs[i - 1] = _manifest_jobs[i];
  _n_manifest_jobs--;
}

bool OtaManager::serviceManifestEgress() {
  if (_n_manifest_jobs == 0) return false;
  ManifestServeJob& job = _manifest_jobs[0];
  if ((int32_t)(_now_ms - job.ready_at) < 0) {
    return true;
  }
  ServeView* v = resolve(job.mid);
  if (!v) {
    popManifestJob();
    return true;
  }

  const uint32_t mfl = v->mfl;
  uint8_t ftotal = (uint8_t)((mfl + OTA_MF_FRAG - 1) / OTA_MF_FRAG);
  if (ftotal == 0) ftotal = 1;
  const uint16_t valid_mask = frag_full_mask(ftotal);
  job.pending_mask &= valid_mask;
  if (job.pending_mask == 0) {
    popManifestJob();
    return true;
  }

  uint8_t fragment = 0;
  while (fragment < 16 && !(job.pending_mask & (1u << fragment))) fragment++;
  if (fragment >= ftotal) {
    popManifestJob();
    return true;
  }

  const uint32_t off = (uint32_t)fragment * OTA_MF_FRAG;
  uint32_t len = mfl - off;
  if (len > OTA_MF_FRAG) len = OTA_MF_FRAG;
  ManifestMsg message;
  memcpy(message.manifest_id, v->m.merkle_root, 4);
  message.frag_idx = fragment;
  message.frag_total = ftotal;
  message.bytes = v->m.manifest_start + off;
  message.len = (uint16_t)len;
  uint8_t wire[MAX_PACKET_PAYLOAD];
  const uint16_t wire_len = encode_manifest(wire, sizeof(wire), message);
  if (emit(wire, wire_len, false)) {
    OTA_DBG("OTA: MANIFEST tx frag=%u/%u len=%u\n",
            (unsigned)fragment, (unsigned)ftotal, (unsigned)len);
    const uint16_t bit = (uint16_t)(1u << fragment);
    job.pending_mask &= (uint16_t)~bit;
    job.emitted_mask |= bit;
    if (job.pending_mask == 0) {
      popManifestJob();
    } else {
      // Follow the active packet airtime and dispatcher duty spacing while
      // retaining a floor for fast radios' TX-to-RX turnaround.
      job.ready_at = _now_ms + manifestEgressGapMs();
    }
  }
  return true;
}

void OtaManager::popServeJob() {
  if (_n_serve_jobs == 0) return;
  for (uint8_t i = 1; i < _n_serve_jobs; i++) _serve_jobs[i - 1] = _serve_jobs[i];
  _n_serve_jobs--;
  _serve_block_len = 0;
  _serve_block_loaded = false;
}

bool OtaManager::loadActiveServeBlock() {
  if (_n_serve_jobs == 0) return false;
  ServeJob& job = _serve_jobs[0];
  ServeView* v = resolve(job.mid);
  if (!v || job.block >= v->m.block_count) return false;
  uint32_t bs = v->m.block_size();
  if (bs == 0 || bs > OTA_MAX_BLOCK) return false;
  uint32_t off = (uint32_t)job.block * bs;
  uint32_t blen = (off + bs <= v->m.payload_size) ? bs : (v->m.payload_size - off);
  if (v->read) {
    if (!v->read(v->read_ctx, off, _serve_block, blen)) return false;
  } else {
    memcpy(_serve_block, v->m.payload + off, blen);
  }
  _serve_block_len = (uint16_t)blen;
  _serve_block_loaded = true;
  return true;
}

bool OtaManager::handleReq(const uint8_t* m, uint16_t n) {
  ReqWindowMsg rq;
  if (!decode_req_window(m, n, rq)) return false;
  ServeView* v = resolve(rq.manifest_id);
  if (!v) return false;
  bool accepted = false;
  const uint32_t block_size = v->m.block_size();
  for (uint8_t i = 0; i < rq.n_items; i++) {
    const uint32_t idx = rq.items[i].block_idx;
    if (idx >= v->m.block_count) continue;
    uint32_t blen = block_size;
    const uint32_t off = idx * block_size;
    if (off + blen > v->m.payload_size) blen = v->m.payload_size - off;
    const uint16_t valid_mask = frag_full_mask((blen + OTA_FRAG_DATA - 1) / OTA_FRAG_DATA);
    const uint16_t want = (uint16_t)(rq.items[i].want_mask & valid_mask);
    if (want != 0) accepted |= queueServeJob(v->m.merkle_root, (uint16_t)idx, want);
  }
  return accepted;
}

bool OtaManager::handleReqProof(const uint8_t* m, uint16_t n) {
  ReqProofMsg rp;
  if (!decode_req_proof(m, n, rp)) return false;
  ServeView* v = resolve(rp.manifest_id);
  if (!v || rp.block_idx >= v->m.block_count) return false;
  if ((uint64_t)v->m.block_count * 4 > v->scratch_sz) return false;
  return queueServeJob(v->m.merkle_root, rp.block_idx, 0); // proof-only, or merge with queued DATA
}

void OtaManager::serviceEgress() {
  // Metadata comes first so an older receiver gets every manifest fragment before its retry horizon. The
  // send callback supplies radio-queue backpressure, and a rejected fragment remains in this descriptor.
  if (serviceManifestEgress()) return;

  // A proactive proof normally follows the final DATA fragment. If it was lost, or the source is older and
  // never sent one, issue the legacy proof request after a short grace. Stay RX-silent while another slot
  // still expects DATA, and restart the grace on every new packet, so this fallback cannot collide with a
  // legitimate multi-block response train. Admit at most one fallback per call.
  if (_fstate == FETCHING && !flightHasPendingData()
      && (uint32_t)(_now_ms - _fetch_wait_since_ms) >= proofGraceMs()) {
    for (uint8_t i = 0; i < OTA_FETCH_PIPELINE; i++) {
      ReassemblySlot& slot = _reasm[i];
      if (!slot.awaiting_proof || slot.proof_request_at == 0
          || (int32_t)(_now_ms - slot.proof_request_at) < 0) continue;
      if (requestSlot(i)) return;
      break;
    }
  }

  if (_n_serve_jobs == 0) return;
  ServeJob& job = _serve_jobs[0];
  ServeView* v = resolve(job.mid);
  if (!v || job.block >= v->m.block_count
      || (uint64_t)v->m.block_count * 4 > v->scratch_sz) {
    popServeJob();
    return;
  }

  if (job.pending_mask != 0) {
    if (!_serve_block_loaded && !loadActiveServeBlock()) {
      popServeJob();
      return;
    }
    uint8_t fragment = 0;
    while (fragment < 16 && !(job.pending_mask & (1u << fragment))) fragment++;
    uint32_t frag_off = (uint32_t)fragment * OTA_FRAG_DATA;
    if (fragment >= 16 || frag_off >= _serve_block_len) {
      job.pending_mask = 0;
      return;
    }
    uint32_t frag_len = _serve_block_len - frag_off;
    if (frag_len > OTA_FRAG_DATA) frag_len = OTA_FRAG_DATA;
    DataMsg dm;
    memcpy(dm.manifest_id, job.mid, 4);
    dm.block_idx = job.block;
    dm.frag_off = (uint16_t)frag_off;
    dm.data = _serve_block + frag_off;
    dm.data_len = (uint16_t)frag_len;
    uint8_t b[MAX_PACKET_PAYLOAD];
    if (emit(b, encode_data(b, sizeof(b), dm), false)) {
      const uint16_t bit = (uint16_t)(1u << fragment);
      job.pending_mask &= (uint16_t)~bit;
      job.emitted_mask |= bit;
      if (job.pending_mask == 0) {
        // Legacy receivers transmit REQ_PROOF as soon as the last DATA fragment arrives. Do not admit our
        // proactive proof into the radio queue at the same instant: the two half-duplex transmissions would
        // collide and force the receiver onto its multi-second retry tick. Include every response that the
        // adapter can already have admitted ahead of this proof, then retain a bounded fast-link floor.
        job.proof_ready_at = _now_ms + proofEgressGapMs();
      }
    }
    return;
  }

  // An explicit legacy REQ_PROOF proves that the source has returned to RX and bypasses the proactive delay.
  // A newer receiver sends no request unless the proof was lost, so its normal proactive path waits for the
  // radio-aware gap and remains compatible with the existing proof-grace deadline.
  if (!job.proof_requested && job.proof_ready_at != 0
      && (int32_t)(_now_ms - job.proof_ready_at) < 0) return;

  uint8_t proof[32 * 4];
  uint8_t np = merkle_gen_proof(v->m.leaves, v->m.block_count, job.block, v->scratch, proof);
  ProofMsg pm;
  memcpy(pm.manifest_id, job.mid, 4);
  pm.block_idx = job.block;
  pm.n_proof = np;
  pm.proof = proof;
  uint8_t b[MAX_PACKET_PAYLOAD];
  if (emit(b, encode_proof(b, sizeof(b), pm), false)) popServeJob();
}

// ---------------- fetch ----------------

// A source's set digest is the lifetime of its catalog rows. When that digest changes (or the source table
// evicts the source), remove only that seeder's association from every row and recompute aggregate progress.
// Rows with no remaining source disappear, so numeric/MID selection cannot target firmware nobody offers.
void OtaManager::invalidateCatalogSeeder(const uint8_t* seeder) {
  if (!seeder) return;
  CatRow* catalog = catalogData();
  for (uint16_t i = 0; i < _n_cat; ) {
    CatRow& row = catalog[i];
    int found = -1;
    for (uint8_t k = 0; k < row.n_seeders; k++) {
      if (memcmp(row.seeders[k], seeder, 4) == 0) { found = k; break; }
    }
    if (found < 0) { i++; continue; }
    for (uint8_t k = (uint8_t)found + 1; k < row.n_seeders; k++) {
      memcpy(row.seeders[k - 1], row.seeders[k], 4);
      row.seeder_have[k - 1] = row.seeder_have[k];
      row.seeder_last_ms[k - 1] = row.seeder_last_ms[k];
    }
    row.n_seeders--;
    if (row.n_seeders == 0) {
      for (uint16_t j = i + 1; j < _n_cat; j++) catalog[j - 1] = catalog[j];
      _n_cat--;
      continue;
    }
    row.have_max = 0;
    row.last_ms = 0;
    for (uint8_t k = 0; k < row.n_seeders; k++) {
      if (row.seeder_have[k] > row.have_max) row.have_max = row.seeder_have[k];
      if (row.seeder_last_ms[k] > row.last_ms) row.last_ms = row.seeder_last_ms[k];
    }
    i++;
  }
}

// A tiny per-node BEACON: record the source; ask it for its catalog (OTA_QUERY) only when we're
// interested AND its set-digest is one we haven't catalogued yet (so a stable mesh is query-free).
void OtaManager::handleAdv(const uint8_t* m, uint16_t n) {
  AdvMsg a;
  if (!decode_adv(m, n, a)) return;
  bool have_sid = (_seeder_id[0] | _seeder_id[1] | _seeder_id[2] | _seeder_id[3]) != 0;
  if (have_sid && memcmp(a.seeder_id, _seeder_id, 4) == 0) return;   // our own beacon, re-flooded
  if (a.n_motas == 0) {                                             // source explicitly withdrew its set
    invalidateCatalogSeeder(a.seeder_id);
    for (uint8_t i = 0; i < _n_src; i++) {
      if (memcmp(_sources[i].seeder, a.seeder_id, 4) != 0) continue;
      for (uint8_t j = i + 1; j < _n_src; j++) _sources[j - 1] = _sources[j];
      _n_src--;
      break;
    }
    return;
  }

  int slot = -1, lru = 0;                                            // find/insert the source (LRU evict)
  for (int i = 0; i < _n_src; i++) {
    if (memcmp(_sources[i].seeder, a.seeder_id, 4) == 0) { slot = i; break; }
    if (_sources[i].last_ms < _sources[lru].last_ms) lru = i;
  }
  bool fresh = (slot < 0);
  if (fresh) {
    // Passive HAVE traffic can leave rows for a source before its beacon is retained. Start its advertised
    // digest with a clean association, and purge the evicted source when the fixed source table is full.
    invalidateCatalogSeeder(a.seeder_id);
    if (_n_src < OTA_MAX_SOURCES) slot = _n_src++;
    else {
      invalidateCatalogSeeder(_sources[lru].seeder);
      slot = lru;
    }
    _sources[slot] = Source{};
  }
  Source& s = _sources[slot];
  bool changed = fresh || memcmp(s.digest, a.set_digest, 4) != 0;
  if (changed && !fresh) invalidateCatalogSeeder(s.seeder);
  memcpy(s.seeder, a.seeder_id, 4); memcpy(s.digest, a.set_digest, 4);
  s.n_motas = a.n_motas; s.last_ms = _now_ms;
  if (changed) {
    s.have_catalog = false;
    s.have_total = 0;
    s.have_mask = 0;
    s.query_pending = false;
    s.query_owned = false;
    s.query_retries = 0;
    s.query_retry_at = 0;
  }

  // interested = auto-fetch enabled, or a manual pull/want is pending. (Browsing queries via queryAll().)
  bool interested = _archive_interest || (_autofetch != AUTOFETCH_OFF) ||
                    _have_desired_mid || _desired_target;
  if (interested && !s.have_catalog) {
    // A fresh ADV is also a new opportunity after the bounded retry series was exhausted.
    if (s.query_retries >= OTA_CATALOG_MAX_RETRY) {
      s.query_retries = 0;
      s.query_retry_at = 0;
      s.query_owned = false;
    }
    scheduleQuery(a.seeder_id, a.set_digest);  // jittered + suppressible
  }
}

// Schedule a catalog query after a random jitter (id +/ digest, so neighbours pick different delays). The
// node with the shortest jitter sends; the rest overhear that QUERY (or the broadcast HAVE) and suppress.
void OtaManager::scheduleQuery(const uint8_t* seeder, const uint8_t* digest) {
  for (uint8_t i = 0; i < _n_src; i++) {
    Source& s = _sources[i];
    if (memcmp(s.seeder, seeder, 4) != 0 || memcmp(s.digest, digest, 4) != 0) continue;
    if (s.query_pending || s.have_catalog ||
        (s.query_owned && s.query_retry_at && (int32_t)(_now_ms - s.query_retry_at) < 0)) return;
    uint32_t j = (rd_u32le(seeder) ^ rd_u32le(digest) ^ rd_u32le(_seeder_id)) % OTA_QUERY_SPREAD_MS;
    s.query_at = _now_ms + OTA_QUERY_MIN_MS + j;
    s.query_pending = true;
    s.query_owned = true;
    return;
  }
}

void OtaManager::sendQuery(const uint8_t* seeder, const uint8_t* digest, uint32_t filter_target,
                           uint32_t want_fragments) {
  QueryMsg q;
  memcpy(q.seeder_id, seeder, 4); memcpy(q.set_digest, digest, 4);
  q.filter_target = filter_target; q.want_fragments = want_fragments;
  uint8_t b[24];
  emit(b, encode_query(b, sizeof(b), q), true);     // FLOODED so neighbours overhear it and suppress
}

// User-initiated browse (`ota neighbors`): immediately ask every incomplete/changed source. A complete
// digest-tagged catalog is already current, so paging through a 255-row list must not re-flood it each time.
void OtaManager::queryAll() {
  for (uint8_t i = 0; i < _n_src; i++) {
    Source& s = _sources[i];
    if (s.have_catalog) continue;
    s.query_pending = false;
    s.query_owned = true;
    s.query_retries = 0;
    s.query_retry_at = _now_ms + OTA_CATALOG_RETRY_MS;
    uint32_t want = 0;
    if (s.have_total) {
      uint32_t full = s.have_total >= 32 ? UINT32_MAX : ((1UL << s.have_total) - 1);
      want = full & ~s.have_mask;
    }
    sendQuery(s.seeder, s.digest, 0, want);
  }
}

// A catalog reply: record each mOTA (deduped by mid; distinct-source count for the UI), and if a row
// matches our fetch interest (auto-fetch own-target, or a pending pull/want), begin fetching it.
void OtaManager::handleHave(const uint8_t* m, uint16_t n) {
  HaveMsg hv;
  if (!decode_have(m, n, hv)) return;
  if (hv.frag_total == 0 || hv.frag_total > OTA_HAVE_MAX_FRAGMENTS || hv.frag_idx >= hv.frag_total) return;
  bool have_sid = (_seeder_id[0] | _seeder_id[1] | _seeder_id[2] | _seeder_id[3]) != 0;
  if (have_sid && memcmp(hv.seeder_id, _seeder_id, 4) == 0) return;   // our own catalog
  // Once a newer beacon changed this source's digest, delayed HAVE fragments from its old set must not
  // resurrect rows we just invalidated. Unknown sources remain cacheable because HAVE is broadcast/passive.
  for (uint8_t i = 0; i < _n_src; i++) {
    if (memcmp(_sources[i].seeder, hv.seeder_id, 4) != 0) continue;
    if (memcmp(_sources[i].digest, hv.set_digest, 4) != 0) return;
    break;
  }
  // PASSIVE: every node caches rows it overhears. A source is catalogued only after EVERY advertised
  // fragment arrived; otherwise a timed recovery QUERY asks for just the missing bitmap.
  for (uint8_t i = 0; i < _n_src; i++) {
    Source& s = _sources[i];
    if (memcmp(s.seeder, hv.seeder_id, 4) != 0 || memcmp(s.digest, hv.set_digest, 4) != 0) continue;
    if (s.have_total != hv.frag_total) {
      s.have_total = hv.frag_total;
      s.have_mask = 0;
      s.have_catalog = false;
    }
    s.have_mask |= 1UL << hv.frag_idx;
    uint32_t full = hv.frag_total >= 32 ? UINT32_MAX : ((1UL << hv.frag_total) - 1);
    s.have_catalog = s.have_mask == full;
    if (s.have_catalog) {
      s.query_pending = false;
      s.query_owned = false;
      s.query_retries = 0;
      s.query_retry_at = 0;
    } else {
      s.query_retry_at = _now_ms + OTA_CATALOG_RETRY_MS;
    }
  }
  for (uint8_t r = 0; r < hv.n_rows && hv.rows; r++) {
    const uint8_t* row = hv.rows + (uint32_t)r * OTA_HAVE_ROW_BYTES;
    const uint8_t* mid = row;
    uint32_t target = rd_u32le(row + 4), fwver = rd_u32le(row + 8);
    uint8_t codec = row[12], flags = row[13];
    uint32_t have_count = rd_u16le(row + 14);   // this source's progress
    CatRow* catalog = catalogData();
    int slot = -1, lru = 0;                                           // upsert into the catalog (dedup by mid)
    for (int i = 0; i < _n_cat; i++) {
      if (memcmp(catalog[i].mid, mid, 4) == 0) { slot = i; break; }
      if (catalog[i].last_ms < catalog[lru].last_ms) lru = i;
    }
    if (slot < 0) {
      if (_n_cat >= catalogCapacity() && expandCatalog()) catalog = catalogData();
      slot = (_n_cat < catalogCapacity()) ? _n_cat++ : lru;
      catalog[slot] = CatRow{};
      memcpy(catalog[slot].mid, mid, 4);
      memcpy(catalog[slot].seeders[0], hv.seeder_id, 4);
      catalog[slot].seeder_have[0] = (uint16_t)have_count;
      catalog[slot].seeder_last_ms[0] = _now_ms;
      catalog[slot].n_seeders = 1;
    } else {
      CatRow& cc = catalog[slot];                                    // count DISTINCT sources (no double-count)
      int known = -1;
      for (uint8_t k = 0; k < cc.n_seeders; k++)
        if (memcmp(cc.seeders[k], hv.seeder_id, 4) == 0) { known = k; break; }
      if (known < 0 && cc.n_seeders < OTA_CAT_SEEDERS) {
        known = cc.n_seeders++;
        memcpy(cc.seeders[known], hv.seeder_id, 4);
      }
      if (known >= 0) {
        cc.seeder_have[known] = (uint16_t)have_count;
        cc.seeder_last_ms[known] = _now_ms;
      }
    }
    CatRow& c = catalog[slot];
    c.target_id = target; c.fw_version = fwver; c.codec = codec; c.flags = flags;
    c.have_max = 0; c.last_ms = 0;
    for (uint8_t k = 0; k < c.n_seeders; k++) {
      if (c.seeder_have[k] > c.have_max) c.have_max = c.seeder_have[k];
      if (c.seeder_last_ms[k] > c.last_ms) c.last_ms = c.seeder_last_ms[k];
    }
    if (wantRow(mid, target, codec, flags)) startFetch(mid, target);
  }
}

void OtaManager::deferCatalog(const uint8_t mid[4], uint32_t until_ms) {
  if (!mid) return;
  CatRow* catalog = catalogData();
  for (uint8_t i = 0; i < _n_cat; i++) {
    if (memcmp(catalog[i].mid, mid, 4) == 0) {
      catalog[i].retry_after_ms = until_ms;
      return;
    }
  }
}

bool OtaManager::wantRow(const uint8_t* mid, uint32_t target, uint8_t codec, uint8_t flags) const {
  if (!_fetch || _fstate == FETCHING || _fstate == WANT_MANIFEST
      || _fstate == WANT_LEAVES || _fstate == VERIFYING_STAGED
      || _fstate == PAUSED) return false;  // busy
  if (_fstate == COMPLETE && memcmp(mid, _fid, 4) == 0) return false;             // already have it
  if (!_archive_fetch && !codecOk(codec)) return false;                           // can't apply this codec
  if (_have_desired_mid)                                                          // manual pull of a specific mid
    return memcmp(mid, _desired_mid, 4) == 0 && (_desired_target == 0 || target == _desired_target);
  if (_desired_target) return target == _desired_target;                          // cross-target want (role switch)
  if (_autofetch == AUTOFETCH_OFF) return false;                                  // discover only
  if (target != _target) return false;                                            // auto-fetch = our own target
  if (_autofetch == AUTOFETCH_SIGNED && !(flags & MFLAG_SIGNED)) return false;    // signed-only policy
  return true;
}

void OtaManager::clearReassemblySlot(uint8_t slot) {
  if (slot >= OTA_FETCH_PIPELINE) return;
  _reasm[slot].block = NO_BLOCK;
  _reasm[slot].mask = 0;
  _reasm[slot].need = 0;
  _reasm[slot].awaiting_proof = false;
  _reasm[slot].proof_request_at = 0;
}

// Forget all blocks currently being reassembled or awaiting proofs.
void OtaManager::clearReassembly() {
  for (uint8_t slot = 0; slot < OTA_FETCH_PIPELINE; slot++) clearReassemblySlot(slot);
  _retry_slot = 0;
  _req_count = 0;
  _pipeline_width = OTA_FETCH_PIPELINE_INITIAL;
  _flight_dirty = false;
  noteFetchActivity();
}

int OtaManager::findReassemblySlot(uint32_t block) const {
  for (uint8_t slot = 0; slot < OTA_FETCH_PIPELINE; slot++) {
    if (_reasm[slot].block == block) return slot;
  }
  return -1;
}

int OtaManager::findEmptyReassemblySlot() const {
  for (uint8_t slot = 0; slot < _pipeline_width; slot++) {
    if (_reasm[slot].block == NO_BLOCK) return slot;
  }
  return -1;
}

uint8_t OtaManager::activePipelineSlots() const {
  uint8_t active = 0;
  for (uint8_t slot = 0; slot < OTA_FETCH_PIPELINE; slot++) {
    if (_reasm[slot].block != NO_BLOCK) active++;
  }
  return active;
}

bool OtaManager::flightHasPendingData() const {
  for (uint8_t slot = 0; slot < OTA_FETCH_PIPELINE; slot++) {
    if (_reasm[slot].block != NO_BLOCK && !_reasm[slot].awaiting_proof) return true;
  }
  return false;
}

static uint8_t count_mask_bits(uint16_t mask) {
  uint8_t count = 0;
  while (mask) { count += (uint8_t)(mask & 1u); mask >>= 1; }
  return count;
}

uint32_t OtaManager::fetchRetryTimeoutMs() const {
  if (_radio_packet_airtime_ms == 0) return OTA_FETCH_RETRY_FALLBACK_MS;

  // Estimate every packet still owed in the current flight, plus the recovery request itself. DATA uses
  // the measured maximum packet airtime; smaller REQ/PROOF packets are deliberately overestimated. Each
  // packet must traverse the source+relay path and each transmitter is paced by its configured duty budget.
  uint32_t packets = 1;
  uint8_t active = 0;
  for (uint8_t slot_index = 0; slot_index < OTA_FETCH_PIPELINE; slot_index++) {
    const ReassemblySlot& slot = _reasm[slot_index];
    if (slot.block == NO_BLOCK) continue;
    active++;
    packets += slot.awaiting_proof ? 1u
        : (uint32_t)count_mask_bits((uint16_t)(slot.need & ~slot.mask)) + 1u;
  }
  if (active == 0) {
    const uint32_t fragments = (_fbs + OTA_FRAG_DATA - 1) / OTA_FRAG_DATA;
    packets += (fragments + 1) * _pipeline_width;
  }
  uint32_t path_transmissions = _observed_path_transmissions;
  if (path_transmissions == 0) path_transmissions = (uint32_t)_max_hops + 1u;
  uint64_t service = (uint64_t)packets * _radio_packet_airtime_ms * path_transmissions;
  service = (service * _tx_spacing_permille + 999u) / 1000u;
  service = (service * 5u + 3u) / 4u;                    // 25% CAD/relay contention allowance
  service += OTA_FETCH_RETRY_GUARD_MS;
  if (service < OTA_FETCH_RETRY_MIN_MS) service = OTA_FETCH_RETRY_MIN_MS;
  if (service > OTA_FETCH_RETRY_MAX_MS) service = OTA_FETCH_RETRY_MAX_MS;
  return (uint32_t)service;
}

uint32_t OtaManager::proofGraceMs() const {
  if (_radio_packet_airtime_ms == 0) return OTA_PROOF_GRACE_MS;
  uint32_t path_transmissions = _observed_path_transmissions;
  if (path_transmissions == 0) path_transmissions = (uint32_t)_max_hops + 1u;
  uint64_t grace = (uint64_t)_radio_packet_airtime_ms * path_transmissions;
  grace = (grace * _tx_spacing_permille + 999u) / 1000u;
  grace += 100u;
  if (grace < OTA_PROOF_GRACE_MS) grace = OTA_PROOF_GRACE_MS;
  if (grace > OTA_FETCH_RETRY_MAX_MS) grace = OTA_FETCH_RETRY_MAX_MS;
  return (uint32_t)grace;
}

void OtaManager::finishFlight() {
  const uint8_t old_width = _pipeline_width;
  if (_flight_dirty) {
    if (_pipeline_width > OTA_FETCH_PIPELINE_INITIAL) {
      _pipeline_width = (uint8_t)((_pipeline_width + 1u) / 2u);
      if (_pipeline_width < OTA_FETCH_PIPELINE_INITIAL) _pipeline_width = OTA_FETCH_PIPELINE_INITIAL;
    }
  } else if (_pipeline_width < OTA_FETCH_PIPELINE) {
    _pipeline_width++;
  }
  if (_pipeline_width != old_width) {
    OTA_DBG("OTA: adaptive flight %s to %u/%u\n", _flight_dirty ? "shrunk" : "grew",
            (unsigned)_pipeline_width, (unsigned)OTA_FETCH_PIPELINE);
  }
  _flight_dirty = false;
}

void OtaManager::notePipelineStall() {
  if (!_flight_dirty) {
    OTA_DBG("OTA: adaptive flight recovery at width %u/%u\n",
            (unsigned)_pipeline_width, (unsigned)OTA_FETCH_PIPELINE);
  }
  _flight_dirty = true;
}

bool OtaManager::blockInPipeline(uint32_t block) const {
  return findReassemblySlot(block) >= 0;
}

bool OtaManager::fetchActive() const {
  return _fstate == FETCHING || _fstate == WANT_MANIFEST || _fstate == WANT_LEAVES
      || _fstate == VERIFYING_STAGED || _fstate == PAUSED;
}

void OtaManager::clearFetchIntent() {
  _desired_target = 0;
  _have_desired_mid = false;
  memset(_desired_mid, 0, sizeof(_desired_mid));
}

void OtaManager::failFetch(FetchError error) {
  _fetch_error = error;
  _fstate = FAILED;
  clearReassembly();
  freeLeaves();
  _validate = false;
  _archive_fetch = false;
  clearFetchIntent();
}

void OtaManager::completeFetch() {
  _fetch_error = FETCH_ERROR_NONE;
  _fstate = COMPLETE;
  _validate = false;
  _archive_fetch = false;
  clearFetchIntent();
}

OtaManager::PullResult OtaManager::pull(const uint8_t* mid, uint32_t target, bool validate) {
  if (!mid) return PULL_BAD_MID;
  if (!_fetch) return PULL_NO_STORE;
  if (fetchActive()) return PULL_BUSY;
  _archive_fetch = false;
  _desired_target = target;
  memcpy(_desired_mid, mid, sizeof(_desired_mid));
  _have_desired_mid = true;
  reDiscover();
  PullResult result = startFetch(mid, target, validate);
  if (result != PULL_STARTED && result != PULL_RESUMED) clearFetchIntent();
  return result;
}

OtaManager::PullResult OtaManager::pull_archive(const uint8_t* mid, uint32_t target, bool validate) {
  if (!mid) return PULL_BAD_MID;
  if (!_fetch) return PULL_NO_STORE;
  if (fetchActive()) return PULL_BUSY;
  _archive_fetch = true;
  _desired_target = target;
  memcpy(_desired_mid, mid, sizeof(_desired_mid));
  _have_desired_mid = true;
  reDiscover();
  PullResult result = startFetch(mid, target, validate);
  if (result != PULL_STARTED && result != PULL_RESUMED) {
    _archive_fetch = false;
    clearFetchIntent();
  }
  return result;
}

// Begin (or resume) fetching a chosen mid: try a staged-partial resume first, else request the manifest.
OtaManager::PullResult OtaManager::startFetch(const uint8_t* mid, uint32_t target, bool validate) {
  (void)target;
  if (!mid) return PULL_BAD_MID;
  if (!_fetch) return PULL_NO_STORE;
  if (fetchActive()) return PULL_BUSY;
  _fetch_error = FETCH_ERROR_NONE;
  _validate = validate;                          // motatool folder-capture warm-start (seed leaf-diff)
  // A validate pull is a FRESH seed capture, not a resume: the store already holds the seed's payload (not a
  // real partial), so never adopt it via resumeStaged - always re-begin and run the manifest->leaves->diff.
  if (!validate && resumeStaged(mid)) return PULL_RESUMED; // adopt a partial container left in the store
  memcpy(_fid, mid, 4);
  _have = 0; _fbc = 0; _ftotal = 0; _fflags = 0;
  _observed_path_transmissions = 0;              // learn the actual source/relay path from this fetch's replies
  _fstate = WANT_MANIFEST;
  _mf_total = 0; _mf_mask = 0; _mf_len = 0; _mf_retries = 0; _loop_last_mfmask = 0;   // fresh manifest reassembly
  memset(_mf_buf, 0, sizeof(_mf_buf));
  GetManifestMsg gm; memcpy(gm.manifest_id, _fid, 4); gm.want_mask = 0xFFFF;   // first ask: send all fragments
  uint8_t b[16];
  emit(b, encode_get_manifest(b, sizeof(b), gm), false);
  return PULL_STARTED;
}

void OtaManager::handleManifest(const uint8_t* m, uint16_t n) {
  ManifestMsg mm;
  if (!decode_manifest(m, n, mm) || !_fetch) return;
  if (_fstate != WANT_MANIFEST || memcmp(mm.manifest_id, _fid, 4) != 0) return;
  if (mm.frag_total == 0 || mm.frag_total > OTA_MF_MAXFRAG || mm.frag_idx >= mm.frag_total) return;

  const uint8_t expected_total = (uint8_t)((MOTA_MFL + OTA_MF_FRAG - 1) / OTA_MF_FRAG);
  const uint16_t expected_len = mm.frag_idx + 1 == expected_total
      ? (uint16_t)(MOTA_MFL - (uint32_t)mm.frag_idx * OTA_MF_FRAG)
      : (uint16_t)OTA_MF_FRAG;
  if (mm.frag_total != expected_total || mm.len != expected_len) return;

  // reassemble the (possibly multi-fragment) manifest into _mf_buf; place fragment frag_idx at its offset
  uint32_t foff = (uint32_t)mm.frag_idx * OTA_MF_FRAG;
  if (foff + mm.len > sizeof(_mf_buf)) return;
  if (mm.frag_total != _mf_total) {
    _mf_total = mm.frag_total;
    _mf_mask = 0;
    _mf_len = 0;
    memset(_mf_buf, 0, sizeof(_mf_buf));
  }
  memcpy(_mf_buf + foff, mm.bytes, mm.len);
  _mf_mask |= (uint16_t)(1u << mm.frag_idx);
  if (mm.frag_idx == mm.frag_total - 1) _mf_len = foff + mm.len;     // last fragment fixes the length
  uint16_t full = (mm.frag_total >= 16) ? 0xFFFF : (uint16_t)((1u << mm.frag_total) - 1);
  if (_mf_mask != full || _mf_len == 0) return;                     // wait until every fragment is in

  const uint8_t* mf = _mf_buf;                   // fully reassembled manifest-minus-leaves
  uint32_t mfl = _mf_len;
  if (mfl != MOTA_MFL) { failFetch(FETCH_ERROR_MANIFEST); return; } // fixed 197-byte manifest
  if (mf[2] != HASH_ALGO_SHA256) { failFetch(FETCH_ERROR_HASH_ALGO); return; }
  if (!_archive_fetch && !codecOk(mf[56])) { failFetch(FETCH_ERROR_CODEC); return; }
  uint32_t payload_size = rd_u32le(mf + 15);
  uint8_t  bsl = mf[19];
  if (bsl >= 32) { failFetch(FETCH_ERROR_GEOMETRY); return; }
  uint32_t bs = 1u << bsl;
  // a block must fit our reassembly buffer (and be non-empty) - reject an oversized block_size up front
  if (bs == 0 || bs > OTA_MAX_BLOCK || payload_size == 0) { failFetch(FETCH_ERROR_GEOMETRY); return; }
  uint32_t bc = (payload_size + bs - 1) / bs;
  if (bc > 0xFFFFu) { failFetch(FETCH_ERROR_TOO_LARGE); return; } // uint16 block index on the wire
  if (_archive_fetch && (uint64_t)bc * 4 > OTA_PROOFGEN_SCRATCH) {
    failFetch(FETCH_ERROR_TOO_LARGE); return;       // retaining an image we cannot seed is useless
  }
  memcpy(_froot, mf + 20, 4);

  uint32_t leaves_off = 8 + mfl;
  uint32_t payload_off = leaves_off + bc * 4;
  uint64_t total64 = (uint64_t)payload_off + payload_size + 5;
  if (total64 > UINT32_MAX) { failFetch(FETCH_ERROR_TOO_LARGE); return; }
  uint32_t total = (uint32_t)total64;

  // Hand the store the parsed layout BEFORE begin(), so a partition-backed store (ESP32) can choose
  // placement and refuse an unfittable fetch up front: a FULL payload streams to the inactive slot,
  // a delta's whole container is staged together. (image_size at mf+11, is_full from flags at mf+1.)
  bool is_full = (mf[1] & MFLAG_FULL) != 0;
  if (!_fetch->plan_layout(is_full, rd_u32le(mf + 11), payload_off, payload_size)) {
    failFetch(FETCH_ERROR_STORAGE); return;
  }
  if (!_fetch->begin(total)) { failFetch(FETCH_ERROR_STORAGE); return; }
  // declare the metadata extent so a flash store can pin it (leaves are written all transfer long)
  if (!_fetch->set_meta_size(payload_off)) { failFetch(FETCH_ERROR_STORAGE); return; }
  uint8_t hdr[8];
  memcpy(hdr, MOTA_MAGIC, 4);
  wr_u32le(hdr + 4, total);
  if (!_fetch->write(0, hdr, 8) ||
      !_fetch->write(8, mf, mfl) ||
      !_fetch->write(total - 5, MOTA_TRAILER, 5)) { failFetch(FETCH_ERROR_STORAGE); return; }

  _fflags = mf[1];   // manifest flags (FULL/SIGNED) of the fetch in progress (auto-install gate)
  _fpoff = payload_off; _floff = leaves_off; _fpsize = payload_size; _fbc = bc; _fbs = bs;
  _ftotal = total; _have = 0;
  clearReassembly();                             // fresh transfer: drop any prior per-block state
  // Warm-start (folder-capture): if requested and the image is small enough for the fixed leaves bitmap,
  // bulk-fetch the target leaves and diff the seed already staged in the store; else just transfer normally.
  if (_validate && beginLeafDiff()) {
    OTA_DBG("OTA: WANT_LEAVES bc=%u (seed leaf-diff)\n", (unsigned)bc);
    return;
  }
  _fstate = FETCHING;
  OTA_DBG("OTA: FETCHING bc=%u bs=%u total=%u\n", (unsigned)bc, (unsigned)bs, (unsigned)total);
  requestMissing();                              // final manifest fragment arrived: open flight 1 immediately
}

// --- leaf-diff warm-start (fetcher side; motatool folder-capture only) -----------------------------

void OtaManager::freeLeaves() {
  if (_leaves_buf) { free(_leaves_buf); _leaves_buf = nullptr; }
  _lv_total = 0; _lv_mask = 0; _diffing = false; _diff_idx = 0;
}

// Enter WANT_LEAVES: allocate the target-leaves buffer and request them (bitmap-fragmented). Returns false
// - caller falls back to a normal full fetch - if the image is too big for the fixed uint16 leaves bitmap or
// the heap allocation fails. Called from handleManifest once geometry is known.
bool OtaManager::beginLeafDiff() {
  freeLeaves();
  if (_fbc == 0 || _fbc > OTA_DIFF_MAX_BLOCKS) return false;   // must fit the fixed want_mask (16 fragments)
  _leaves_buf = (uint8_t*)malloc((size_t)_fbc * 4);
  if (!_leaves_buf) return false;
  _lv_total = 0; _lv_mask = 0; _lv_retries = 0; _loop_last_lvmask = 0;
  _fstate = WANT_LEAVES;
  GetLeavesMsg gl; memcpy(gl.manifest_id, _fid, 4); gl.want_mask = 0xFFFF;   // first ask: all fragments
  uint8_t b[16];
  emit(b, encode_get_leaves(b, sizeof(b), gl), false);
  return true;
}

// Reassemble the target leaves[] (like the manifest), authenticate them against _froot, then diff the seed.
void OtaManager::handleLeaves(const uint8_t* m, uint16_t n) {
  LeavesMsg lv;
  if (!decode_leaves(m, n, lv) || !_fetch || !_leaves_buf) return;
  if (_fstate != WANT_LEAVES || memcmp(lv.manifest_id, _fid, 4) != 0) return;
  uint32_t leaves_len = _fbc * 4;
  uint8_t ftotal = (uint8_t)((leaves_len + OTA_LEAVES_FRAG - 1) / OTA_LEAVES_FRAG); if (ftotal == 0) ftotal = 1;
  if (lv.frag_total != ftotal || lv.frag_idx >= ftotal) return;
  uint32_t foff = (uint32_t)lv.frag_idx * OTA_LEAVES_FRAG;
  uint32_t expected_len = leaves_len - foff;
  if (expected_len > OTA_LEAVES_FRAG) expected_len = OTA_LEAVES_FRAG;
  if (lv.len != expected_len) return;                            // reject short/oversized slices
  if (lv.frag_total != _lv_total) { _lv_total = lv.frag_total; _lv_mask = 0; }   // (re)start
  memcpy(_leaves_buf + foff, lv.bytes, lv.len);
  _lv_mask |= (uint16_t)(1u << lv.frag_idx);
  OTA_DBG("OTA: LEAVES rx frag=%u/%u len=%u mask=%04x\n",
          (unsigned)lv.frag_idx, (unsigned)lv.frag_total, (unsigned)lv.len, (unsigned)_lv_mask);
  uint16_t full = (ftotal >= 16) ? 0xFFFF : (uint16_t)((1u << ftotal) - 1);
  if (_lv_mask != full) return;                                 // wait for every fragment
  uint8_t root[4]; merkle_root(root, _leaves_buf, _fbc);        // authenticate the leaves against the manifest root
  if (memcmp(root, _froot, 4) != 0) {                           // can't trust them -> just fetch normally
    OTA_DBG("OTA: leaves root mismatch -> full fetch\n");
    freeLeaves(); _fstate = FETCHING; requestMissing(); return;
  }
  // leaves authenticated -> diff the seed against them a batch at a time in loop() (559 store reads must not
  // block the mesh loop / starve the radio / trip the watchdog). Stay WANT_LEAVES; diffStep() drives it.
  _diffing = true; _diff_idx = 0;
  OTA_DBG("OTA: leaves ok (root match); diffing seed...\n");
}

// Diff up to OTA_DIFF_BATCH seed blocks per call: every block whose seed bytes (already staged in the store
// payload) hash to the authenticated target leaf is marked present; the rest stay missing and are pulled over
// LoRa. When all blocks are diffed, continue as a normal FETCHING of the holes (or finalize if none differ).
void OtaManager::diffStep() {
  uint8_t blk[OTA_MAX_BLOCK];
  for (uint32_t k = 0; k < OTA_DIFF_BATCH && _diff_idx < _fbc; k++, _diff_idx++) {
    uint32_t blen = blockLen(_diff_idx);
    if (!_fetch->read(_fpoff + _diff_idx * _fbs, blk, blen)) continue;      // read fail -> leave missing
    uint8_t leaf[4]; merkle_leaf(leaf, blk, blen);
    if (memcmp(leaf, _leaves_buf + (size_t)_diff_idx * 4, 4) == 0 &&        // seed block == target block
        _fetch->write(_floff + _diff_idx * 4, _leaves_buf + (size_t)_diff_idx * 4, 4))
      _have++;                                                             // mark present (payload already seeded)
  }
  if (_diff_idx < _fbc) return;                                            // more to diff on the next tick
  OTA_DBG("OTA: leaf-diff %u/%u already valid; fetching the rest\n", (unsigned)_have, (unsigned)_fbc);
  freeLeaves();                                                            // clears _diffing + frees the buffer
  if (_have >= _fbc) {                                                      // seed covered the whole image
    if (!storedLeavesRootMatches()) failFetch(FETCH_ERROR_INTEGRITY);
    else if (!_fetch->finalize()) failFetch(FETCH_ERROR_STORAGE);
    else completeFetch();
    return;
  }
  _fstate = FETCHING; requestMissing();
}

bool OtaManager::resumeStaged(const uint8_t* want_mid) {
  if (!_fetch || _fstate == FETCHING || _fstate == WANT_MANIFEST
      || _fstate == WANT_LEAVES || _fstate == VERIFYING_STAGED) {
    return false;
  }
  if (!_fetch->reopen()) return false;                  // nothing persisted in the store
  uint32_t total = _fetch->staged_size();
  uint8_t hdr[8];
  if (total < 13 || !_fetch->read(0, hdr, 8) || memcmp(hdr, MOTA_MAGIC, 4) != 0) return false;
  // read + parse the stored manifest (everything before leaves[]) to recompute the geometry
  uint8_t mbuf[256];
  uint32_t mread = total - 8; if (mread > sizeof(mbuf)) mread = sizeof(mbuf);
  MotaManifest m;
  if (!_fetch->read(8, mbuf, mread) || !mota_parse_manifest(mbuf, mread, m)) return false;
  if (want_mid && memcmp(m.merkle_root, want_mid, 4) != 0) return false;   // a different fw is staged
  if (m.hash_algo != HASH_ALGO_SHA256) return false;
  if (!_archive_fetch && !codecOk(m.codec_id)) return false;
  uint32_t mfl = (uint32_t)(m.approval - m.manifest_start) + 4;            // manifest-minus-leaves length
  uint32_t bs = m.block_size();
  if (bs == 0 || bs > OTA_MAX_BLOCK) return false;
  uint32_t bc = m.block_count;
  if (bc == 0 || bc > 0xFFFFu) return false;
  if (_archive_fetch && (uint64_t)bc * 4 > OTA_PROOFGEN_SCRATCH) return false;
  uint32_t leaves_off = 8 + mfl;
  uint32_t payload_off = leaves_off + bc * 4;
  if ((uint64_t)payload_off + m.payload_size + 5 != total) return false;   // geometry must match the header

  memcpy(_fid, m.merkle_root, 4);
  memcpy(_froot, m.merkle_root, 4);
  _fflags = m.flags;
  _fpoff = payload_off; _floff = leaves_off; _fpsize = m.payload_size; _fbc = bc; _fbs = bs;
  _ftotal = total;
  clearReassembly();
  _observed_path_transmissions = 0;
  _fetch_error = FETCH_ERROR_NONE;
  beginStagedVerification();
  OTA_DBG("OTA: RESUME verifying %u blocks total=%u\n",
          (unsigned)bc, (unsigned)total);
  return true;
}

uint32_t OtaManager::blockLen(uint32_t i) const {
  uint32_t off = i * _fbs;
  return (off + _fbs <= _fpsize) ? _fbs : (_fpsize - off);
}

bool OtaManager::blockPresent(uint32_t i) const {
  uint8_t leaf[4];
  if (!_fetch->read(_floff + i * 4, leaf, 4)) return false;
  return !(leaf[0]==0xFF && leaf[1]==0xFF && leaf[2]==0xFF && leaf[3]==0xFF);
}

bool OtaManager::storedLeavesRootMatches() const {
  if (!_fetch || _fbc == 0) return false;
  MerkleAccumulator accumulator;
  for (uint32_t i = 0; i < _fbc; ++i) {
    uint8_t leaf[4];
    if (!_fetch->read(_floff + i * 4, leaf, sizeof(leaf))
        || (leaf[0] == 0xFF && leaf[1] == 0xFF
            && leaf[2] == 0xFF && leaf[3] == 0xFF)
        || !accumulator.add(leaf)) {
      return false;
    }
  }
  uint8_t root[4];
  return accumulator.finish(root) && memcmp(root, _froot, 4) == 0;
}

void OtaManager::beginStagedVerification() {
  _have = 0;
  _resume_verify_idx = 0;
  _resume_invalidated = false;
  _resume_merkle.reset();
  _fstate = VERIFYING_STAGED;
}

void OtaManager::verifyStagedStep() {
  if (_fstate != VERIFYING_STAGED || !_fetch) return;
  static const uint8_t missing_leaf[4] = {0xFF, 0xFF, 0xFF, 0xFF};

  for (uint32_t checked = 0;
       checked < OTA_DIFF_BATCH && _resume_verify_idx < _fbc;
       ++checked, ++_resume_verify_idx) {
    uint8_t stored_leaf[4];
    const uint32_t index = _resume_verify_idx;
    if (!_fetch->read(_floff + index * 4,
                      stored_leaf, sizeof(stored_leaf))) {
      failFetch(FETCH_ERROR_STORAGE);
      return;
    }
    if (memcmp(stored_leaf, missing_leaf, sizeof(stored_leaf)) == 0) {
      continue;
    }

    const uint32_t length = blockLen(index);
    if (!_fetch->read(_fpoff + index * _fbs, _reasm[0].buf, length)) {
      failFetch(FETCH_ERROR_STORAGE);
      return;
    }
    uint8_t computed_leaf[4];
    merkle_leaf(computed_leaf, _reasm[0].buf, length);
    if (memcmp(computed_leaf, stored_leaf, sizeof(stored_leaf)) != 0) {
      // Payload and marker disagree. Clear the marker so the normal fetch path
      // requests this block again; a stale marker must never bless bad bytes.
      if (!_fetch->write(_floff + index * 4,
                         missing_leaf, sizeof(missing_leaf))) {
        failFetch(FETCH_ERROR_STORAGE);
        return;
      }
      _resume_invalidated = true;
      continue;
    }
    if (!_resume_merkle.add(computed_leaf)) {
      failFetch(FETCH_ERROR_INTEGRITY);
      return;
    }
    _have++;
  }

  if (_resume_verify_idx < _fbc) return;
  clearReassembly();
  if (_resume_invalidated) _fetch->checkpoint();

  if (_have == _fbc) {
    uint8_t root[4];
    if (!_resume_merkle.finish(root) || memcmp(root, _froot, sizeof(root)) != 0) {
      failFetch(FETCH_ERROR_INTEGRITY);
    } else if (!_fetch->finalize()) {
      failFetch(FETCH_ERROR_STORAGE);
    } else {
      completeFetch();
    }
  } else {
    _fstate = FETCHING;
    requestMissing();
  }
  _resume_merkle.reset();
}

bool OtaManager::handleData(const uint8_t* m, uint16_t n) {
  DataMsg dm;
  if (!decode_data(m, n, dm) || !_fetch) return false;
  if (_fstate != FETCHING || memcmp(dm.manifest_id, _fid, 4) != 0) return false;
  if (dm.block_idx >= _fbc) return false;
  if (blockPresent(dm.block_idx)) return true;              // late duplicate for this terminal
  int slot_index = findReassemblySlot(dm.block_idx);
  if (slot_index < 0) return false;                         // not one of this node's requested blocks
  ReassemblySlot& slot = _reasm[slot_index];
  uint32_t blen = blockLen(dm.block_idx);
  if (dm.frag_off % OTA_FRAG_DATA != 0) return false;       // canonical FRAG_DATA-aligned slices only
  if ((uint32_t)dm.frag_off + dm.data_len > blen) return false; // slice out of the block
  uint32_t expected_len = blen - dm.frag_off;
  if (expected_len > OTA_FRAG_DATA) expected_len = OTA_FRAG_DATA;
  if (dm.data_len != expected_len) return false;            // do not mark a short slice as complete
  uint32_t kf = dm.frag_off / OTA_FRAG_DATA;
  if (kf >= 16) return false;
  const uint16_t bit = (uint16_t)(1u << kf);
  if (slot.mask & bit) return true;                         // duplicate flood/retry copy is terminal too
  memcpy(slot.buf + dm.frag_off, dm.data, dm.data_len);
  slot.mask |= bit;
  noteFetchActivity();
  if (slot.mask != slot.need || slot.awaiting_proof) return true;
  // The paced server sends a proof immediately after this block's requested DATA. Wait briefly so that proof
  // can arrive without another request/response turn. An older server, or a lost proof, falls back through
  // serviceEgress() to the existing REQ_PROOF wire message.
  slot.awaiting_proof = true;
  slot.proof_request_at = _now_ms + proofGraceMs();
  return true;
}

bool OtaManager::handleProof(const uint8_t* m, uint16_t n) {
  ProofMsg pm;
  if (!decode_proof(m, n, pm) || !_fetch) return false;
  if (_fstate != FETCHING || memcmp(pm.manifest_id, _fid, 4) != 0) return false;
  if (pm.block_idx >= _fbc) return false;
  int slot_index = findReassemblySlot(pm.block_idx);
  if (slot_index < 0 || !_reasm[slot_index].awaiting_proof) return blockPresent(pm.block_idx);
  ReassemblySlot& slot = _reasm[slot_index];
  uint32_t block = slot.block;
  uint32_t blen = blockLen(block);
  noteFetchActivity();
  if (!merkle_verify(slot.buf, blen, block, pm.proof, pm.n_proof, _froot, _fbc)) {
    notePipelineStall();
    clearReassemblySlot((uint8_t)slot_index);                   // bad -> drop and re-fetch only this block
    if (activePipelineSlots() == 0) { finishFlight(); requestMissing(); }
    return true;
  }
  // verified -> commit the payload block, then its leaf (the present marker). A write failure here means a
  // FOLDER destination's seeder link dropped mid-transfer: PAUSE (hold progress on the host, stop
  // requesting, do NOT fall back to RAM/flash). The block is left uncommitted (its leaf stays 0xFF), so on
  // reconnect resumeStaged() re-requests exactly it. Flash failures also pause instead of being ignored.
  uint8_t leaf[4]; merkle_leaf(leaf, slot.buf, blen);
  if (!_fetch->write(_fpoff + block * _fbs, slot.buf, blen) ||
      !_fetch->write(_floff + block * 4, leaf, 4)) {
    _fetch_error = FETCH_ERROR_STORAGE;
    _fstate = PAUSED; clearReassembly(); return true;
  }
  _have++;
  OTA_DBG("OTA: block %u OK  have=%u/%u\n", (unsigned)block, (unsigned)_have, (unsigned)_fbc);
  clearReassemblySlot((uint8_t)slot_index);
  // periodically persist progress (meta/leaf page + open payload) so a reboot can resume (no-op for RAM);
  // cadence is runtime-tunable via `ota config checkpoint <N>` (0 = never)
  if (_checkpoint_blocks && _have % _checkpoint_blocks == 0) _fetch->checkpoint();
  if (_have < _fbc) {
    // Do not continuously refill a partially completed flight: the remaining response packets are already
    // queued at the source/relays. Staying silent here is what prevents the old threefold request amplification.
    if (activePipelineSlots() == 0) { finishFlight(); requestMissing(); }
    return true;
  }
  // Every leaf must be readable and collectively match the manifest root.
  // A scratch allocation/read failure is an integrity failure, never success.
  clearReassembly();
  if (!storedLeavesRootMatches()) failFetch(FETCH_ERROR_INTEGRITY);
  else if (!_fetch->finalize()) failFetch(FETCH_ERROR_STORAGE);
  else completeFetch();
  OTA_DBG("OTA: transfer %s\n", _fstate == COMPLETE ? "COMPLETE" : "FAILED(integrity/storage)");
  return true;
}

bool OtaManager::requestSlot(uint8_t slot_index) {
  if (_fstate != FETCHING || slot_index >= OTA_FETCH_PIPELINE) return false;
  ReassemblySlot& slot = _reasm[slot_index];
  if (slot.block >= _fbc) return false;
  _req_start = slot.block;
  _req_count = activePipelineSlots();
  if (slot.awaiting_proof) {
    ReqProofMsg rp; memcpy(rp.manifest_id, _fid, 4); rp.block_idx = (uint16_t)slot.block;
    uint8_t b[16];
    bool sent = emit(b, encode_req_proof(b, sizeof(b), rp), false);
    if (sent) {
      slot.proof_request_at = 0;
      noteFetchActivity();
      OTA_DBG("OTA: REQ_PROOF fallback block=%u (have=%u/%u window=%u)\n",
              (unsigned)slot.block, (unsigned)_have, (unsigned)_fbc, (unsigned)_req_count);
    }
    return sent;
  }
  uint16_t want = (uint16_t)(slot.need & ~slot.mask);
  if (want == 0) want = slot.need;                  // safety: never send an empty request
  ReqMsg rq; memcpy(rq.manifest_id, _fid, 4);
  rq.block_idx = (uint16_t)slot.block; rq.want_mask = want;
  uint8_t b[16];
  OTA_DBG("OTA: REQ block=%u want=%04x (have=%u/%u mask=%04x window=%u)\n",
          (unsigned)slot.block, (unsigned)want, (unsigned)_have, (unsigned)_fbc,
          (unsigned)slot.mask, (unsigned)_req_count);
  bool sent = emit(b, encode_req(b, sizeof(b), rq), false);
  if (sent) noteFetchActivity();
  return sent;
}

bool OtaManager::requestFlight() {
  if (_fstate != FETCHING) return false;
  ReqWindowMsg request = {};
  memcpy(request.manifest_id, _fid, 4);
  for (uint8_t slot_index = 0; slot_index < OTA_FETCH_PIPELINE; slot_index++) {
    const ReassemblySlot& slot = _reasm[slot_index];
    if (slot.block == NO_BLOCK || slot.awaiting_proof) continue;
    ReqItem& item = request.items[request.n_items++];
    item.block_idx = (uint16_t)slot.block;
    item.want_mask = (uint16_t)(slot.need & ~slot.mask);
    if (item.want_mask == 0) item.want_mask = slot.need;
  }
  if (request.n_items == 0) return false;
  _req_start = request.items[0].block_idx;
  _req_count = request.n_items;
  uint8_t b[5 + OTA_REQ_MAX_ITEMS * 4];
  OTA_DBG("OTA: REQ flight first=%u blocks=%u width=%u/%u\n",
          (unsigned)_req_start, (unsigned)_req_count,
          (unsigned)_pipeline_width, (unsigned)OTA_FETCH_PIPELINE);
  bool sent = emit(b, encode_req_window(b, sizeof(b), request), false);
  if (sent) noteFetchActivity();
  return sent;
}

bool OtaManager::fillPipeline() {
  if (activePipelineSlots() != 0) return false;             // flights never overlap/refill
  uint8_t assigned = 0;
  while (true) {
    int slot_index = findEmptyReassemblySlot();
    if (slot_index < 0) break;
    uint32_t block = pickMissingBlock();
    if (block >= _fbc) break;
    ReassemblySlot& slot = _reasm[slot_index];
    slot.block = block;
    slot.mask = 0;
    slot.need = frag_full_mask((blockLen(block) + OTA_FRAG_DATA - 1) / OTA_FRAG_DATA);
    slot.awaiting_proof = false;
    slot.proof_request_at = 0;
    assigned++;
  }
  if (assigned == 0) return false;
  _flight_dirty = false;
  requestFlight();
  return true;
}

void OtaManager::requestMissing() {
  if (_fstate != FETCHING) return;
  if (activePipelineSlots() == 0) { fillPipeline(); return; }

  // The airtime-aware deadline expired. Retry one slot's holes per deadline, round-robin, so a weak block
  // cannot trigger a simultaneous multi-block re-burst on a half-duplex channel. Recovery dirties the
  // flight; its successor is reduced (4->2, 3->2, 2->1) after all current slots finish.
  notePipelineStall();
  for (uint8_t offset = 0; offset < OTA_FETCH_PIPELINE; offset++) {
    uint8_t slot = (uint8_t)((_retry_slot + offset) % OTA_FETCH_PIPELINE);
    if (_reasm[slot].block == NO_BLOCK) continue;
    if (requestSlot(slot)) _retry_slot = (uint8_t)((slot + 1) % OTA_FETCH_PIPELINE);
    return;
  }
}

// Walk missing blocks in serial order, excluding blocks already assigned to a pipeline slot.
uint32_t OtaManager::pickMissingBlock() {
  if (_fbc == 0) return _fbc;
  for (uint32_t i = 0; i < _fbc; i++) {
    if (!blockInPipeline(i) && !blockPresent(i)) return i;
  }
  return _fbc;
}

void OtaManager::loop() {
  // Each source owns its own pending/retry state, so a burst of advertisements cannot overwrite another
  // seeder's query. Initial requests ask for all; recovery asks only for missing HAVE fragments.
  for (uint8_t i = 0; i < _n_src; i++) {
    Source& s = _sources[i];
    if (s.query_pending && (int32_t)(_now_ms - s.query_at) >= 0) {
      s.query_pending = false;
      uint32_t want = 0;
      if (s.have_total) {
        uint32_t full = s.have_total >= 32 ? UINT32_MAX : ((1UL << s.have_total) - 1);
        want = full & ~s.have_mask;
      }
      sendQuery(s.seeder, s.digest, 0, want);
      s.query_retry_at = _now_ms + OTA_CATALOG_RETRY_MS;
    } else if (s.query_owned && !s.have_catalog && !s.query_pending && s.query_retry_at &&
               (int32_t)(_now_ms - s.query_retry_at) >= 0 && s.query_retries < OTA_CATALOG_MAX_RETRY) {
      uint32_t want = 0;
      if (s.have_total) {
        uint32_t full = s.have_total >= 32 ? UINT32_MAX : ((1UL << s.have_total) - 1);
        want = full & ~s.have_mask;
      }
      s.query_retries++;
      sendQuery(s.seeder, s.digest, 0, want);
      s.query_retry_at = _now_ms + OTA_CATALOG_RETRY_MS;
    }
  }
  if (_fstate == VERIFYING_STAGED) {
    verifyStagedStep();
    return;
  }
  if (_fstate == WANT_MANIFEST) {
    // Retry GET_MANIFEST ONLY when a tick passed with no new fragment - re-bursting every tick would congest
    // the link and burn the retry cap while fragments are still arriving (mirrors FETCHING + WANT_LEAVES).
    // Give up after a cap of stalled retries so an unreachable mid doesn't pin the single fetch slot forever.
    if (_mf_mask == _loop_last_mfmask) {
      if (++_mf_retries > OTA_MANIFEST_MAX_RETRY) { failFetch(FETCH_ERROR_MANIFEST_TIMEOUT); return; }
      GetManifestMsg gm; memcpy(gm.manifest_id, _fid, 4);
      // request only the manifest fragments still missing; 0xFFFF ("send all") until we know frag_total
      gm.want_mask = (_mf_total > 0) ? (uint16_t)(frag_full_mask(_mf_total) & ~_mf_mask) : 0xFFFF;
      if (gm.want_mask == 0) gm.want_mask = 0xFFFF;    // safety: never send an empty request
      uint8_t b[16];
      emit(b, encode_get_manifest(b, sizeof(b), gm), false);
    }
    _loop_last_mfmask = _mf_mask;
    return;
  }
  if (_fstate == WANT_LEAVES) {
    if (_diffing) { diffStep(); return; }   // leaves in - diff the seed a batch at a time (non-blocking)
    // Re-request ONLY when a whole tick passed with no new fragment - otherwise re-bursting the holes every
    // tick congests the link (and burns the retry cap) while fragments are still streaming in. On a stall,
    // ask for just the missing bitmap (anti-burst); give up (FAILED) after a cap of stalled retries.
    if (_lv_mask == _loop_last_lvmask) {
      if (++_lv_retries > OTA_LEAVES_MAX_RETRY) { failFetch(FETCH_ERROR_LEAVES_TIMEOUT); return; }
      GetLeavesMsg gl; memcpy(gl.manifest_id, _fid, 4);
      gl.want_mask = (_lv_total > 0) ? (uint16_t)(frag_full_mask(_lv_total) & ~_lv_mask) : 0xFFFF;
      if (gl.want_mask == 0) gl.want_mask = 0xFFFF;    // safety: never send an empty request
      OTA_DBG("OTA: GET_LEAVES retry=%u want=%04x (mask=%04x/%u)\n",
              (unsigned)_lv_retries, (unsigned)gl.want_mask, (unsigned)_lv_mask, (unsigned)_lv_total);
      uint8_t b[16];
      emit(b, encode_get_leaves(b, sizeof(b), gl), false);
    }
    _loop_last_lvmask = _lv_mask;
    return;
  }
  if (_fstate != FETCHING) return;
  if (activePipelineSlots() == 0) {
    requestMissing();
    return;
  }
  // The main loop may still tick each second for discovery, but block recovery waits for the calculated
  // response train: current SF/BW airtime * outstanding packets * source/relay transmissions * duty pacing.
  // Every newly accepted fragment/proof restarts the deadline; duplicate flood copies do not.
  if ((uint32_t)(_now_ms - _fetch_wait_since_ms) >= fetchRetryTimeoutMs()) requestMissing();
}

// ---------------- dispatch ----------------

bool OtaManager::on_message(const uint8_t* msg, uint16_t len) {
  switch (ota_msg_type(msg, len)) {
    case OTA_ADV:          handleAdv(msg, len); return false;
    case OTA_QUERY:        handleQuery(msg, len); return false;
    case OTA_HAVE:         handleHave(msg, len); return false;
    case OTA_GET_MANIFEST: return handleGetManifest(msg, len);
    case OTA_MANIFEST:     handleManifest(msg, len); return false;
    case OTA_GET_LEAVES:   handleGetLeaves(msg, len); return false;
    case OTA_LEAVES:       handleLeaves(msg, len); return false;
    case OTA_REQ:          return handleReq(msg, len);
    case OTA_DATA:         return handleData(msg, len);
    case OTA_REQ_PROOF:    return handleReqProof(msg, len);
    case OTA_PROOF:        return handleProof(msg, len);
    default: return false;
  }
}

} // namespace ota
} // namespace mesh
