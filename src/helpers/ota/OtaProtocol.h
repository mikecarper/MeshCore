#pragma once

#include <stdint.h>
#include <stddef.h>
#include "OtaFormat.h"

// Encode/decode for the OTA LoRa messages (docs/ota_protocol.md Section 8). Each message is a packet payload:
// [0]=ota_msg_type, then a fixed body. Portable + allocation-free; unit-tested on the host.
//
// manifest_id == the manifest's merkle_root (4 bytes), a compact content id.

namespace mesh {
namespace ota {

// ---- OTA_ADV: tiny per-NODE beacon (flood, periodic). CONSTANT size regardless of how many mOTAs a
// node serves - it just says "I'm a source, here's how many + a digest of my set". A peer that's
// interested asks for the catalog via OTA_QUERY. (Replaces the old per-mOTA advert so a folder node with
// N images costs one 10-byte beacon, not N adverts.) ----
struct AdvMsg {
  uint8_t  seeder_id[4];     // advertiser's node id = pubkey[0:4]; the QUERY address + distinct-source id
  uint8_t  n_motas;          // # of complete, servable mOTAs (saturates at 255)
  uint8_t  set_digest[4];    // sha2-256:4 over the sorted set of served mids; "did my offering change?"
};

// ---- OTA_QUERY: "list what you serve" - addressed to a source by seeder_id, FLOODED so neighbours
// overhear it (storm suppression). set_digest identifies the offering being asked about (so an overhearer
// can suppress its own pending query for the same {source,digest}). filter_target=0 = everything.
// want_fragments=0 asks for every fragment; otherwise bit k asks only for HAVE fragment k. The latter
// lets a receiver recover catalog packet loss without causing another large all-fragment burst. ----
struct QueryMsg {
  uint8_t  seeder_id[4];     // which source this query is for (the source matches its own id)
  uint8_t  set_digest[4];    // the offering digest we're asking about (for overhear-suppression)
  uint32_t filter_target;    // 0 = all (the scalable default); else only mOTAs for this target_id
  uint32_t want_fragments;   // 0 = all; otherwise bit k requests OTA_HAVE fragment k
};

// ---- OTA_HAVE: the compact catalog (source -> mesh), FLOODED + tagged with set_digest so EVERY node
// that overhears it caches the rows (passive, no query needed). Fragmented. ----
// body: seeder_id(4) set_digest(4) frag_idx(1) frag_total(1) n_rows(1) rows[ mid(4) target(4) fwver(4) codec(1) flags(1) ]
struct HaveRow { uint8_t mid[4]; uint32_t target_id; uint32_t fw_version; uint8_t codec_id; uint8_t flags;
                 uint16_t have_count; };   // blocks the advertiser holds (== block_count if complete; less => partial source)
struct HaveMsg {
  uint8_t  seeder_id[4];
  uint8_t  set_digest[4];    // the offering this catalog describes (overhearers cache by it)
  uint8_t  frag_idx, frag_total;
  uint8_t  n_rows;           // rows in THIS fragment
  const uint8_t* rows;       // points into buf: n_rows * OTA_HAVE_ROW_BYTES
};
static const uint8_t OTA_HAVE_ROW_BYTES = 16;   // mid4 + target4 + fwver4 + codec1 + flags1 + have_count2
static const uint8_t OTA_HAVE_MAX_FRAGMENTS = 32;  // QueryMsg::want_fragments bitmap width

// ---- OTA_GET_MANIFEST: request the manifest for a content id (direct) ----
// body: manifest_id(4) want_mask(2). want_mask bit k = "send manifest fragment k"; 0xFFFF = "send all"
// (used on the first ask, before frag_total is known). A retry requests only the still-missing fragments,
// so a lost manifest fragment doesn't force the whole manifest to be re-bursted (same rationale as OTA_REQ).
struct GetManifestMsg { uint8_t manifest_id[4]; uint16_t want_mask; };

// ---- OTA_MANIFEST: the manifest-minus-leaves[], fragmented (direct) ----
// body: manifest_id(4) frag_idx(1) frag_total(1) bytes[]
struct ManifestMsg {
  uint8_t  manifest_id[4];
  uint8_t  frag_idx, frag_total;
  const uint8_t* bytes; uint16_t len;
};

// ---- OTA_REQ: request specific fragments of one or more blocks (direct) ----
// body: manifest_id(4) { block_idx(2) want_mask(2) }[1..4]. The first row is exactly the original
// single-block OTA_REQ body, so an older source safely serves that row and ignores appended rows. A newer
// source queues every row. This lets a fetcher open one adaptive flight with one packet, then remain silent
// while the source and relays return the requested DATA/PROOF train.
//
// Legacy want_mask bit k = "send fragment k" (the slice at byte offset k*OTA_FRAG_DATA). A recovery request
// uses only the missing bits. New receivers reserve bits 15..14 as an in-band request profile: bit 15 asks
// for the v2 171-byte geometry and bit 14 permits per-block raw DEFLATE. An old source masks those bits away
// and returns the ordinary 160-byte DATA train, so mixed-version fleets downgrade without getting stranded.
struct ReqMsg { uint8_t manifest_id[4]; uint16_t block_idx; uint16_t want_mask; };
struct ReqItem { uint16_t block_idx; uint16_t want_mask; };
#ifndef OTA_REQ_MAX_ITEMS
#define OTA_REQ_MAX_ITEMS 4
#endif
struct ReqWindowMsg {
  uint8_t manifest_id[4];
  uint8_t n_items;
  ReqItem items[OTA_REQ_MAX_ITEMS];
};

// ---- OTA_DATA: one self-describing fragment of a block's data (proof stays in a separate packet) ----
// body: manifest_id(4) block_idx(2) frag_off(2) data[]
// Legacy `frag_off` is the byte offset of `data` within block `block_idx`. In negotiated v2 its high bit is
// set and the field packs the wire encoding, fragment index, and total encoded size; data[] starts with
// SHA-256:4 of the complete encoded representation, followed by up to 171 bytes. Repeating that id prevents
// different seeders' valid-but-different DEFLATE streams from being mixed. The message type stays OTA_DATA,
// so deployed opaque relays retain the same priority and forwarding behavior.
struct DataMsg {
  uint8_t  manifest_id[4];
  uint16_t block_idx;
  uint16_t frag_off;
  const uint8_t* data; uint16_t data_len;
};

// OTA_REQ want_mask extension. These bits sit outside every valid fragment bit for <=1 KiB blocks. A first
// v2 request deliberately includes all seven legacy fragment bits so an old source can answer it completely.
static const uint16_t OTA_REQ_V2_MARK             = 0x8000u;
static const uint16_t OTA_REQ_V2_ALLOW_DEFLATE    = 0x4000u;
static const uint16_t OTA_REQ_V2_RESERVED         = 0x2000u;
static const uint16_t OTA_REQ_V2_FRAGMENT_MASK    = 0x1FFFu;

// OTA_DATA v2 frag_off packing:
//   bit 15      v2 marker
//   bit 14      data[] is one raw-RFC1951-DEFLATE stream fragment (clear = raw block bytes)
//   bits 13..10 fragment index (0..15; byte offset = index * OTA_FRAG_DATA_V2)
//   bits  9..0  total encoded block length minus one (1..1024 bytes)
static const uint16_t OTA_DATA_V2_MARK             = 0x8000u;
static const uint16_t OTA_DATA_V2_DEFLATED         = 0x4000u;
static const uint16_t OTA_DATA_V2_FRAGMENT_BITS    = 0x3C00u;
static const uint16_t OTA_DATA_V2_LENGTH_BITS      = 0x03FFu;
static const uint8_t  OTA_DATA_V2_FRAGMENT_SHIFT   = 10;
static const uint16_t OTA_DATA_V2_MAX_ENCODED      = 1024;
static const uint8_t  OTA_DATA_V2_STREAM_ID_BYTES  = 4;

inline bool ota_req_is_v2(uint16_t want_mask) {
  return (want_mask & OTA_REQ_V2_MARK) != 0 && (want_mask & OTA_REQ_V2_RESERVED) == 0;
}

inline uint16_t ota_req_v2_fragments(uint16_t want_mask) {
  return (uint16_t)(want_mask & OTA_REQ_V2_FRAGMENT_MASK);
}

inline uint16_t ota_req_make_v2(uint16_t fragments, bool allow_deflate) {
  return (uint16_t)((fragments & OTA_REQ_V2_FRAGMENT_MASK) | OTA_REQ_V2_MARK |
                    (allow_deflate ? OTA_REQ_V2_ALLOW_DEFLATE : 0));
}

inline bool ota_data_v2_pack(uint8_t fragment, uint16_t encoded_len, bool deflated,
                             uint16_t& packed) {
  if (fragment >= 16 || encoded_len == 0 || encoded_len > OTA_DATA_V2_MAX_ENCODED) return false;
  packed = (uint16_t)(OTA_DATA_V2_MARK |
      (deflated ? OTA_DATA_V2_DEFLATED : 0) |
      ((uint16_t)fragment << OTA_DATA_V2_FRAGMENT_SHIFT) |
      (encoded_len - 1u));
  return true;
}

inline bool ota_data_v2_unpack(uint16_t packed, uint8_t& fragment, uint16_t& encoded_len,
                               bool& deflated) {
  if ((packed & OTA_DATA_V2_MARK) == 0) return false;
  fragment = (uint8_t)((packed & OTA_DATA_V2_FRAGMENT_BITS) >> OTA_DATA_V2_FRAGMENT_SHIFT);
  encoded_len = (uint16_t)((packed & OTA_DATA_V2_LENGTH_BITS) + 1u);
  deflated = (packed & OTA_DATA_V2_DEFLATED) != 0;
  return true;
}

// ---- OTA_GET_LEAVES: request the target's merkle leaves[] in bulk (direct; motatool warm-start only) ----
// body: manifest_id(4) want_mask(2). want_mask bit k = "send leaves fragment k"; 0xFFFF = "send all" on the
// first ask (before frag_total is known); a retry requests only the still-missing fragments (anti-burst,
// same rationale as OTA_GET_MANIFEST). Lets a folder-capture fetcher diff a seed build's blocks against the
// target's authenticated leaves and pull DATA only for the ones that differ.
struct GetLeavesMsg { uint8_t manifest_id[4]; uint16_t want_mask; };

// ---- OTA_LEAVES: one fragment of the leaves[] array (block_count*4 bytes total), direct ----
// body: manifest_id(4) frag_idx(1) frag_total(1) bytes[]   (up to OTA_LEAVES_FRAG leaf bytes per fragment)
struct LeavesMsg {
  uint8_t  manifest_id[4];
  uint8_t  frag_idx, frag_total;
  const uint8_t* bytes; uint16_t len;
};

// ---- OTA_REQ_PROOF: proof fallback for an older source or a lost proactive proof (direct) ----
struct ReqProofMsg { uint8_t manifest_id[4]; uint16_t block_idx; };

// ---- OTA_PROOF: the merkle proof (ordered sibling digests) for one block (direct) ----
struct ProofMsg { uint8_t manifest_id[4]; uint16_t block_idx; uint8_t n_proof; const uint8_t* proof; };

// Each encode_* returns the total payload length (incl. the leading msg-type byte), 0 on overflow.
// Each decode_* returns true on success (and points struct fields into `buf`).

uint16_t encode_adv(uint8_t* buf, uint16_t cap, const AdvMsg& m);
bool     decode_adv(const uint8_t* buf, uint16_t len, AdvMsg& m);

uint16_t encode_query(uint8_t* buf, uint16_t cap, const QueryMsg& m);
bool     decode_query(const uint8_t* buf, uint16_t len, QueryMsg& m);

uint16_t encode_have(uint8_t* buf, uint16_t cap, const HaveMsg& m);
bool     decode_have(const uint8_t* buf, uint16_t len, HaveMsg& m);

uint16_t encode_get_manifest(uint8_t* buf, uint16_t cap, const GetManifestMsg& m);
bool     decode_get_manifest(const uint8_t* buf, uint16_t len, GetManifestMsg& m);

uint16_t encode_manifest(uint8_t* buf, uint16_t cap, const ManifestMsg& m);
bool     decode_manifest(const uint8_t* buf, uint16_t len, ManifestMsg& m);

uint16_t encode_req(uint8_t* buf, uint16_t cap, const ReqMsg& m);
bool     decode_req(const uint8_t* buf, uint16_t len, ReqMsg& m);
uint16_t encode_req_window(uint8_t* buf, uint16_t cap, const ReqWindowMsg& m);
bool     decode_req_window(const uint8_t* buf, uint16_t len, ReqWindowMsg& m);

uint16_t encode_data(uint8_t* buf, uint16_t cap, const DataMsg& m);
bool     decode_data(const uint8_t* buf, uint16_t len, DataMsg& m);

uint16_t encode_get_leaves(uint8_t* buf, uint16_t cap, const GetLeavesMsg& m);
bool     decode_get_leaves(const uint8_t* buf, uint16_t len, GetLeavesMsg& m);

uint16_t encode_leaves(uint8_t* buf, uint16_t cap, const LeavesMsg& m);
bool     decode_leaves(const uint8_t* buf, uint16_t len, LeavesMsg& m);

uint16_t encode_req_proof(uint8_t* buf, uint16_t cap, const ReqProofMsg& m);
bool     decode_req_proof(const uint8_t* buf, uint16_t len, ReqProofMsg& m);

uint16_t encode_proof(uint8_t* buf, uint16_t cap, const ProofMsg& m);
bool     decode_proof(const uint8_t* buf, uint16_t len, ProofMsg& m);

inline uint8_t ota_msg_type(const uint8_t* buf, uint16_t len) { return len ? buf[0] : 0xFF; }

} // namespace ota
} // namespace mesh
