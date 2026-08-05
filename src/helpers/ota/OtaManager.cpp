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
  _n_serve = 0; _n_src_obj = 0; _view0.valid = false; _srcv.valid = false;
  _n_src = 0; _n_cat = 0;
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
  registerSelfEntry();
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
  registerSelfEntry();
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
  _src_list[_n_src_obj++] = src;
  refresh_sources();
  return true;
}

bool OtaManager::remove_source(MotaSource* src) {
  if (!src) return false;
  for (uint8_t i = 0; i < _n_src_obj; i++) {
    if (_src_list[i] != src) continue;
    for (uint8_t j = i + 1; j < _n_src_obj; j++) _src_list[j - 1] = _src_list[j];
    _src_list[--_n_src_obj] = nullptr;
    refresh_sources();
    return true;
  }
  return false;
}

void OtaManager::refresh_sources() {
  _n_serve = 0;
  if (_view0.valid) registerSelfEntry();
  for (uint8_t s = 0; s < _n_src_obj; s++) {
    MotaSource* src = _src_list[s];
    if (!src) continue;
    uint8_t cnt = src->count();
    for (uint8_t i = 0; i < cnt && _n_serve < OTA_MAX_SERVE; i++) {
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
    }
  }
  _srcv.valid = false;                        // a loaded source view may now be stale; reloads on demand
}

void OtaManager::clear_sources() {
  _n_src_obj = 0; _srcv.valid = false;
  _n_serve = 0;
  if (_view0.valid) registerSelfEntry();
}

void OtaManager::clear_primary() {
  _view0.valid = false;
  if (_n_serve > 0 && _serve[0].is_self) {
    for (uint8_t i = 1; i < _n_serve; i++) {
      _serve[i - 1] = _serve[i];
    }
    _n_serve--;
  }
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

void OtaManager::handleGetManifest(const uint8_t* m, uint16_t n) {
  GetManifestMsg gm;
  if (!decode_get_manifest(m, n, gm)) return;
  ServeView* v = resolve(gm.manifest_id);
  if (!v) return;
  // A signed v2 manifest (with hw_id[32]) exceeds one LoRa packet, so send it as fragments. Each carries
  // up to OTA_MF_FRAG manifest bytes; the client reassembles by frag_idx. Only the fragments requested in
  // want_mask are sent (0xFFFF = all), so a retry re-sends just the holes, not the whole manifest.
  uint32_t mfl = v->mfl;
  const uint8_t* src = v->m.manifest_start;
  uint8_t ftotal = (uint8_t)((mfl + OTA_MF_FRAG - 1) / OTA_MF_FRAG); if (ftotal == 0) ftotal = 1;
  for (uint8_t fi = 0; fi < ftotal; fi++) {
    if (!(gm.want_mask & (1u << fi))) continue;      // fetcher didn't ask for this manifest fragment
    uint32_t off = (uint32_t)fi * OTA_MF_FRAG;
    uint32_t fl = mfl - off; if (fl > OTA_MF_FRAG) fl = OTA_MF_FRAG;
    ManifestMsg mm;
    memcpy(mm.manifest_id, v->m.merkle_root, 4);
    mm.frag_idx = fi; mm.frag_total = ftotal;
    mm.bytes = src + off; mm.len = (uint16_t)fl;
    uint8_t b[MAX_PACKET_PAYLOAD];
    emit(b, encode_manifest(b, sizeof(b), mm), false);
  }
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

// Smallest mask covering `nf` fragments: bit k set for k in [0, nf). Caps at 16 (matches _reasm_mask and
// the manifest reassembly), which bounds a block at 16 fragments - our 1 KB blocks are 7.
static inline uint16_t frag_full_mask(uint32_t nf) {
  return (nf >= 16) ? 0xFFFFu : (uint16_t)((1u << nf) - 1);
}

// Emit one block's data as self-describing DATA fragments (frag_off); only the fragments whose bit is set
// in `want_mask` are sent (bit k = the slice at k*OTA_FRAG_DATA). The proof is fetched separately.
void OtaManager::emitBlockData(const uint8_t* mid, uint32_t idx, const uint8_t* data, uint32_t blen,
                               uint16_t want_mask) {
  uint32_t k = 0;
  for (uint32_t fo = 0; fo < blen; fo += OTA_FRAG_DATA, k++) {
    if (!(want_mask & (1u << k))) continue;         // fetcher didn't ask for this fragment
    uint32_t fl = (fo + OTA_FRAG_DATA <= blen) ? OTA_FRAG_DATA : (blen - fo);
    DataMsg dm;
    memcpy(dm.manifest_id, mid, 4);
    dm.block_idx = (uint16_t)idx; dm.frag_off = (uint16_t)fo;
    dm.data = data + fo; dm.data_len = (uint16_t)fl;
    uint8_t b[MAX_PACKET_PAYLOAD];
    emit(b, encode_data(b, sizeof(b), dm), false);
  }
}

void OtaManager::handleReq(const uint8_t* m, uint16_t n) {
  ReqMsg rq;
  if (!decode_req(m, n, rq)) return;
  uint32_t idx = rq.block_idx;
  ServeView* v = resolve(rq.manifest_id);
  if (v) {                                          // serve a fully-held mota (own fw or attached folder)
    if (idx >= v->m.block_count) return;
    uint32_t bs = v->m.block_size();
    uint32_t off = idx * bs;
    uint32_t blen = (off + bs <= v->m.payload_size) ? bs : (v->m.payload_size - off);
    uint8_t blk[OTA_MAX_BLOCK];
    const uint8_t* data;
    if (v->read) { if (!v->read(v->read_ctx, off, blk, blen)) return; data = blk; }
    else         { data = v->m.payload + off; }
    emitBlockData(v->m.merkle_root, idx, data, blen, rq.want_mask);
  }
}

void OtaManager::handleReqProof(const uint8_t* m, uint16_t n) {
  ReqProofMsg rp;
  if (!decode_req_proof(m, n, rp)) return;
  ServeView* v = resolve(rp.manifest_id);
  if (!v) return;
  if (rp.block_idx >= v->m.block_count) return;
  if ((uint64_t)v->m.block_count * 4 > v->scratch_sz) return;     // proof-gen needs block_count*4 scratch
  uint8_t proof[32 * 4];
  uint8_t np = merkle_gen_proof(v->m.leaves, v->m.block_count, rp.block_idx, v->scratch, proof);
  ProofMsg pm;
  memcpy(pm.manifest_id, v->m.merkle_root, 4);
  pm.block_idx = rp.block_idx; pm.n_proof = np; pm.proof = proof;
  uint8_t b[MAX_PACKET_PAYLOAD];
  emit(b, encode_proof(b, sizeof(b), pm), false);
}

// ---------------- fetch ----------------

// A tiny per-node BEACON: record the source; ask it for its catalog (OTA_QUERY) only when we're
// interested AND its set-digest is one we haven't catalogued yet (so a stable mesh is query-free).
void OtaManager::handleAdv(const uint8_t* m, uint16_t n) {
  AdvMsg a;
  if (!decode_adv(m, n, a)) return;
  bool have_sid = (_seeder_id[0] | _seeder_id[1] | _seeder_id[2] | _seeder_id[3]) != 0;
  if (have_sid && memcmp(a.seeder_id, _seeder_id, 4) == 0) return;   // our own beacon, re-flooded
  if (a.n_motas == 0) return;                                        // source offers nothing

  int slot = -1, lru = 0;                                            // find/insert the source (LRU evict)
  for (int i = 0; i < _n_src; i++) {
    if (memcmp(_sources[i].seeder, a.seeder_id, 4) == 0) { slot = i; break; }
    if (_sources[i].last_ms < _sources[lru].last_ms) lru = i;
  }
  bool fresh = (slot < 0);
  if (fresh) { slot = (_n_src < OTA_MAX_SOURCES) ? _n_src++ : lru; _sources[slot] = Source{}; }
  Source& s = _sources[slot];
  bool changed = fresh || memcmp(s.digest, a.set_digest, 4) != 0;
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
      catalog[slot].n_seeders = 1;
    } else {
      CatRow& cc = catalog[slot];                                    // count DISTINCT sources (no double-count)
      bool known = false;
      for (uint8_t k = 0; k < cc.n_seeders; k++)
        if (memcmp(cc.seeders[k], hv.seeder_id, 4) == 0) { known = true; break; }
      if (!known && cc.n_seeders < OTA_CAT_SEEDERS) memcpy(cc.seeders[cc.n_seeders++], hv.seeder_id, 4);
    }
    CatRow& c = catalog[slot];
    c.target_id = target; c.fw_version = fwver; c.codec = codec; c.flags = flags; c.last_ms = _now_ms;
    if (have_count > c.have_max) c.have_max = have_count;             // best-known progress among sources
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
  if (!_fetch || _fstate == FETCHING || _fstate == WANT_MANIFEST || _fstate == PAUSED) return false;  // busy
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

// Forget the block currently being reassembled / awaited (back to NO_BLOCK). Safe to call between blocks:
// the next DATA fragment re-derives the slice mask for whatever block it belongs to.
void OtaManager::clearReassembly() {
  _reasm_block = NO_BLOCK; _reasm_mask = 0; _reasm_need = 0; _awaiting_proof = false;
}

// Begin (or resume) fetching a chosen mid: try a staged-partial resume first, else request the manifest.
void OtaManager::startFetch(const uint8_t* mid, uint32_t target, bool validate) {
  (void)target;
  if (!_fetch || _fstate == FETCHING || _fstate == WANT_MANIFEST || _fstate == WANT_LEAVES || _fstate == PAUSED) return;
  _validate = validate;                          // motatool folder-capture warm-start (seed leaf-diff)
  // A validate pull is a FRESH seed capture, not a resume: the store already holds the seed's payload (not a
  // real partial), so never adopt it via resumeStaged - always re-begin and run the manifest->leaves->diff.
  if (!validate && resumeStaged(mid)) return;    // (non-validate) resume a partial container left in flash
  memcpy(_fid, mid, 4);
  _fstate = WANT_MANIFEST;
  _mf_total = 0; _mf_mask = 0; _mf_len = 0; _mf_retries = 0; _loop_last_mfmask = 0;   // fresh manifest reassembly
  memset(_mf_buf, 0, sizeof(_mf_buf));
  GetManifestMsg gm; memcpy(gm.manifest_id, _fid, 4); gm.want_mask = 0xFFFF;   // first ask: send all fragments
  uint8_t b[16];
  emit(b, encode_get_manifest(b, sizeof(b), gm), false);
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
  if (mfl != MOTA_MFL) { _fstate = FAILED; return; }   // manifest-minus-leaves is a fixed 197 bytes
  if (mf[2] != HASH_ALGO_SHA256) { _fstate = FAILED; return; }  // this implementation supports sha2-256 only
  if (!_archive_fetch && !codecOk(mf[56])) { _fstate = IDLE; return; }  // incompatible install codec
  uint32_t payload_size = rd_u32le(mf + 15);
  uint8_t  bsl = mf[19];
  if (bsl >= 32) { _fstate = FAILED; return; }
  uint32_t bs = 1u << bsl;
  // a block must fit our reassembly buffer (and be non-empty) - reject an oversized block_size up front
  if (bs == 0 || bs > OTA_MAX_BLOCK || payload_size == 0) { _fstate = FAILED; return; }
  uint32_t bc = (payload_size + bs - 1) / bs;
  if (bc > 0xFFFFu) { _fstate = FAILED; return; }   // block_idx is uint16 on the wire - can't address more
  if (_archive_fetch && (uint64_t)bc * 4 > OTA_PROOFGEN_SCRATCH) {
    _fstate = FAILED; return;                       // retaining an image we cannot subsequently seed is useless
  }
  memcpy(_froot, mf + 20, 4);

  uint32_t leaves_off = 8 + mfl;
  uint32_t payload_off = leaves_off + bc * 4;
  uint64_t total64 = (uint64_t)payload_off + payload_size + 5;
  if (total64 > UINT32_MAX) { _fstate = FAILED; return; }
  uint32_t total = (uint32_t)total64;

  // Hand the store the parsed layout BEFORE begin(), so a partition-backed store (ESP32) can choose
  // placement and refuse an unfittable fetch up front: a FULL payload streams to the inactive slot,
  // a delta's whole container is staged together. (image_size at mf+11, is_full from flags at mf+1.)
  bool is_full = (mf[1] & MFLAG_FULL) != 0;
  if (!_fetch->plan_layout(is_full, rd_u32le(mf + 11), payload_off, payload_size)) { _fstate = FAILED; return; }
  if (!_fetch->begin(total)) { _fstate = FAILED; return; }
  // declare the metadata extent so a flash store can pin it (leaves are written all transfer long)
  if (!_fetch->set_meta_size(payload_off)) { _fstate = FAILED; return; }
  uint8_t hdr[8];
  memcpy(hdr, MOTA_MAGIC, 4);
  wr_u32le(hdr + 4, total);
  if (!_fetch->write(0, hdr, 8) ||
      !_fetch->write(8, mf, mfl) ||
      !_fetch->write(total - 5, MOTA_TRAILER, 5)) { _fstate = FAILED; return; }

  _fflags = mf[1];   // manifest flags (FULL/SIGNED) of the fetch in progress (auto-install gate)
  _fpoff = payload_off; _floff = leaves_off; _fpsize = payload_size; _fbc = bc; _fbs = bs;
  _ftotal = total; _have = 0;
  clearReassembly();                             // fresh transfer: drop any prior per-block state
  _loop_last_have = 0; _loop_last_mask = 0;
  // Warm-start (folder-capture): if requested and the image is small enough for the fixed leaves bitmap,
  // bulk-fetch the target leaves and diff the seed already staged in the store; else just transfer normally.
  if (_validate && beginLeafDiff()) {
    OTA_DBG("OTA: WANT_LEAVES bc=%u (seed leaf-diff)\n", (unsigned)bc);
    return;
  }
  _fstate = FETCHING;
  OTA_DBG("OTA: FETCHING bc=%u bs=%u total=%u\n", (unsigned)bc, (unsigned)bs, (unsigned)total);
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
    _fstate = _fetch->finalize() ? COMPLETE : FAILED;
    return;
  }
  _fstate = FETCHING; requestMissing();
}

bool OtaManager::resumeStaged(const uint8_t* want_mid) {
  if (!_fetch || _fstate == FETCHING || _fstate == WANT_MANIFEST) return false;
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
  if (_archive_fetch && (uint64_t)bc * 4 > OTA_PROOFGEN_SCRATCH) return false;
  uint32_t leaves_off = 8 + mfl;
  uint32_t payload_off = leaves_off + bc * 4;
  if ((uint64_t)payload_off + m.payload_size + 5 != total) return false;   // geometry must match the header

  memcpy(_fid, m.merkle_root, 4);
  memcpy(_froot, m.merkle_root, 4);
  _fflags = m.flags;
  _fpoff = payload_off; _floff = leaves_off; _fpsize = m.payload_size; _fbc = bc; _fbs = bs;
  _ftotal = total;
  _have = 0;
  for (uint32_t i = 0; i < bc; i++) if (blockPresent(i)) _have++;   // count blocks whose leaf survived
  clearReassembly();
  _loop_last_have = 0; _loop_last_mask = 0;
  OTA_DBG("OTA: RESUME have=%u/%u total=%u\n", (unsigned)_have, (unsigned)bc, (unsigned)total);

  if (_have >= bc) {                                  // already complete -> verify root + finalize
    uint8_t* scratch = bc * 4 <= OTA_PROOFGEN_SCRATCH ? ensureScratch() : nullptr;
    if (scratch && _fetch->read(_floff, scratch, bc * 4)) {
      uint8_t root[4]; merkle_root(root, scratch, bc);
      _fstate = (memcmp(root, _froot, 4) == 0) ? COMPLETE : FAILED;
    } else {
      _fstate = COMPLETE;
    }
    if (_fstate == COMPLETE && !_fetch->finalize()) _fstate = FAILED;
    return true;
  }
  _fstate = FETCHING;                                 // resume fetching the holes
  _loop_last_have = _have; _loop_last_mask = _reasm_mask;
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

void OtaManager::handleData(const uint8_t* m, uint16_t n) {
  DataMsg dm;
  if (!decode_data(m, n, dm) || !_fetch) return;
  if (_fstate != FETCHING || memcmp(dm.manifest_id, _fid, 4) != 0) return;
  if (dm.block_idx >= _fbc) return;
  if (blockPresent(dm.block_idx)) return;                   // already stored + verified
  uint32_t blen = blockLen(dm.block_idx);
  if (dm.frag_off % OTA_FRAG_DATA != 0) return;             // canonical FRAG_DATA-aligned slices only
  if ((uint32_t)dm.frag_off + dm.data_len > blen) return;   // slice out of the block
  if (dm.block_idx != _reasm_block) {                       // (re)start reassembly for this block
    _reasm_block = dm.block_idx; _reasm_mask = 0; _awaiting_proof = false;
    uint32_t nf = (blen + OTA_FRAG_DATA - 1) / OTA_FRAG_DATA;
    _reasm_need = (nf >= 16) ? 0xFFFF : (uint16_t)((1u << nf) - 1);
  }
  uint32_t kf = dm.frag_off / OTA_FRAG_DATA;
  if (kf >= 16) return;
  memcpy(_reasm_buf + dm.frag_off, dm.data, dm.data_len);
  _reasm_mask |= (uint16_t)(1u << kf);
  if (_reasm_mask != _reasm_need || _awaiting_proof) return;  // wait for all slices (or proof already asked)
  // block fully reassembled -> request its proof (data + proof are fetched separately)
  _awaiting_proof = true;
  ReqProofMsg rp; memcpy(rp.manifest_id, _fid, 4); rp.block_idx = (uint16_t)_reasm_block;
  uint8_t b[16]; emit(b, encode_req_proof(b, sizeof(b), rp), false);
}

void OtaManager::handleProof(const uint8_t* m, uint16_t n) {
  ProofMsg pm;
  if (!decode_proof(m, n, pm) || !_fetch) return;
  if (_fstate != FETCHING || memcmp(pm.manifest_id, _fid, 4) != 0) return;
  if (!_awaiting_proof || pm.block_idx != _reasm_block) return;   // not the block we're verifying
  uint32_t blen = blockLen(_reasm_block);
  if (!merkle_verify(_reasm_buf, blen, _reasm_block, pm.proof, pm.n_proof, _froot, _fbc)) {
    clearReassembly();                                                      // bad -> drop, re-fetch the block
    return;
  }
  // verified -> commit the payload block, then its leaf (the present marker). A write failure here means a
  // FOLDER destination's seeder link dropped mid-transfer: PAUSE (hold progress on the host, stop
  // requesting, do NOT fall back to RAM/flash). The block is left uncommitted (its leaf stays 0xFF), so on
  // reconnect resumeStaged() re-requests exactly it. Flash failures also pause instead of being ignored.
  uint8_t leaf[4]; merkle_leaf(leaf, _reasm_buf, blen);
  if (!_fetch->write(_fpoff + (uint32_t)_reasm_block * _fbs, _reasm_buf, blen) ||
      !_fetch->write(_floff + (uint32_t)_reasm_block * 4, leaf, 4)) {
    _fstate = PAUSED; clearReassembly(); return;
  }
  _have++;
  OTA_DBG("OTA: block %u OK  have=%u/%u\n", (unsigned)_reasm_block, (unsigned)_have, (unsigned)_fbc);
  clearReassembly();
  // periodically persist progress (meta/leaf page + open payload) so a reboot can resume (no-op for RAM);
  // cadence is runtime-tunable via `ota config checkpoint <N>` (0 = never)
  if (_checkpoint_blocks && _have % _checkpoint_blocks == 0) _fetch->checkpoint();
  if (_have < _fbc) { requestMissing(); return; }            // next block
  // all blocks present -> final root cross-check + finalize
  uint8_t* scratch = _fbc * 4 <= OTA_PROOFGEN_SCRATCH ? ensureScratch() : nullptr;
  if (scratch && _fetch->read(_floff, scratch, _fbc * 4)) {
    uint8_t root[4]; merkle_root(root, scratch, _fbc);
    _fstate = (memcmp(root, _froot, 4) == 0) ? COMPLETE : FAILED;
  } else {
    _fstate = COMPLETE;   // per-block proofs already guaranteed integrity vs the root
  }
  if (_fstate == COMPLETE && !_fetch->finalize()) _fstate = FAILED;
  OTA_DBG("OTA: transfer %s\n", _fstate == COMPLETE ? "COMPLETE" : "FAILED(integrity/storage)");
}

void OtaManager::requestMissing() {
  if (_fstate != FETCHING) return;
  // Per-block serial flow (split data/proof). If the current block's data is fully reassembled and we
  // are waiting on its proof, (re)send the proof request rather than re-fetching the data - this also
  // recovers from a lost PROOF reply.
  if (_awaiting_proof && _reasm_block != NO_BLOCK) {
    ReqProofMsg rp; memcpy(rp.manifest_id, _fid, 4); rp.block_idx = (uint16_t)_reasm_block;
    uint8_t b[16]; emit(b, encode_req_proof(b, sizeof(b), rp), false);
    OTA_DBG("OTA: REQ_PROOF block=%u (have=%u/%u)\n",
            (unsigned)_reasm_block, (unsigned)_have, (unsigned)_fbc);
    return;
  }
  // Otherwise request the DATA fragments of the next missing block. One block at a time keeps the
  // server's TX queue tiny so OTA never floods the mesh (docs/ota_protocol.md Section 8); a block's fragments
  // are self-describing (frag_off) so missing slices can be retried without re-sending a full block.
  uint32_t start = pickMissingBlock();
  if (start >= _fbc) return;
  _req_start = start; _req_count = 1;
  // Ask only for fragments we still lack: the holes of the in-flight partial block (_reasm_need & ~mask),
  // or all fragments of a fresh block. A lost fragment then costs one fragment to re-fetch, not the whole
  // block - and the re-REQ can't collide with a multi-fragment burst (half-duplex) as before.
  uint32_t nf = (blockLen(start) + OTA_FRAG_DATA - 1) / OTA_FRAG_DATA;
  uint16_t need = frag_full_mask(nf);
  uint16_t have = (start == _reasm_block) ? _reasm_mask : 0;
  uint16_t want = (uint16_t)(need & ~have);
  if (want == 0) want = need;                       // safety: never send an empty request
  ReqMsg rq; memcpy(rq.manifest_id, _fid, 4);
  rq.block_idx = (uint16_t)start; rq.want_mask = want;
  uint8_t b[16];
  OTA_DBG("OTA: REQ block=%u want=%04x (have=%u/%u mask=%04x)\n",
          (unsigned)start, (unsigned)want, (unsigned)_have, (unsigned)_fbc, (unsigned)_reasm_mask);
  emit(b, encode_req(b, sizeof(b), rq), false);
}

// One receiver walks missing blocks in serial order. A partially-reassembled block is always finished first.
uint32_t OtaManager::pickMissingBlock() {
  if (_fbc == 0) return _fbc;
  if (_reasm_block < _fbc && !blockPresent(_reasm_block) && _reasm_mask != 0) return _reasm_block;
  for (uint32_t i = 0; i < _fbc; i++) if (!blockPresent(i)) return i;
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
  if (_fstate == WANT_MANIFEST) {
    // Retry GET_MANIFEST ONLY when a tick passed with no new fragment - re-bursting every tick would congest
    // the link and burn the retry cap while fragments are still arriving (mirrors FETCHING + WANT_LEAVES).
    // Give up after a cap of stalled retries so an unreachable mid doesn't pin the single fetch slot forever.
    if (_mf_mask == _loop_last_mfmask) {
      if (++_mf_retries > OTA_MANIFEST_MAX_RETRY) { _fstate = FAILED; return; }
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
      if (++_lv_retries > OTA_LEAVES_MAX_RETRY) { freeLeaves(); _fstate = FAILED; return; }
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
  // retry only when a whole tick passed with NO progress - neither a committed block nor a new fragment
  // of the in-flight block. This avoids re-request spam while a block's fragments are still streaming in.
  if (_have == _loop_last_have && _reasm_mask == _loop_last_mask) requestMissing();
  _loop_last_have = _have;
  _loop_last_mask = _reasm_mask;
}

// ---------------- dispatch ----------------

void OtaManager::on_message(const uint8_t* msg, uint16_t len) {
  switch (ota_msg_type(msg, len)) {
    case OTA_ADV:          handleAdv(msg, len); break;
    case OTA_QUERY:        handleQuery(msg, len); break;
    case OTA_HAVE:         handleHave(msg, len); break;
    case OTA_GET_MANIFEST: handleGetManifest(msg, len); break;
    case OTA_MANIFEST:     handleManifest(msg, len); break;
    case OTA_GET_LEAVES:   handleGetLeaves(msg, len); break;
    case OTA_LEAVES:       handleLeaves(msg, len); break;
    case OTA_REQ:          handleReq(msg, len); break;
    case OTA_DATA:         handleData(msg, len); break;
    case OTA_REQ_PROOF:    handleReqProof(msg, len); break;
    case OTA_PROOF:        handleProof(msg, len); break;
    default: break;
  }
}

} // namespace ota
} // namespace mesh
