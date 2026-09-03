#include "OtaDeflate.h"

#if defined(ENABLE_OTA) || defined(OTA_TRANSPORT_DEFLATE_TEST)

#include "Multihash.h"
#include <string.h>

extern "C" {
#include "tinf/tinf.h"
}

namespace mesh {
namespace ota {

static_assert(OTA_MAX_BLOCK <= OTA_DATA_V2_MAX_ENCODED,
              "transport descriptor must represent every OTA logical block");
static_assert(OTA_FETCH_PIPELINE == 1,
              "historical transport shim requires OTA_FETCH_PIPELINE=1");

static uint16_t transport_fragment_mask(uint16_t bytes, uint16_t fragment_bytes) {
  const uint16_t count = (uint16_t)((bytes + fragment_bytes - 1u) / fragment_bytes);
  return count >= 16 ? 0xFFFFu : (uint16_t)((1u << count) - 1u);
}

bool ota_transport_inflate(void* context, const uint8_t* src, uint16_t src_len,
                           uint8_t* dst, uint16_t dst_cap, uint16_t* dst_len) {
  (void)context;
  if (dst_len) *dst_len = 0;
  if (!src || src_len == 0 || !dst || dst_cap == 0 || !dst_len) return false;

  unsigned int produced = dst_cap;
  const int result = tinf_uncompress_exact(dst, &produced, src, src_len);
  if (result != TINF_OK || produced != dst_cap) return false;
  *dst_len = (uint16_t)produced;
  return true;
}

void OtaTransportInflateReceiver::begin(OtaManager& manager, uint32_t target_id,
                                        OtaSend send_fn, void* send_ctx) {
  _manager = &manager;
  _send = send_fn;
  _send_ctx = send_ctx;
  reset_session();
  manager.begin(target_id, send_trampoline, this);
}

void OtaTransportInflateReceiver::reset_representation() {
  _encoded_len = 0;
  _mask = 0;
  _need = 0;
  _partial_retries = 0;
  _request_sent = false;
  _complete = false;
  _deflated = false;
  memset(_stream_id, 0, sizeof(_stream_id));
}

void OtaTransportInflateReceiver::reset_session() {
  reset_representation();
  _active = false;
  _block = 0;
  memset(_mid, 0, sizeof(_mid));
  _allow_deflate = true;
  _legacy_session = false;
}

bool OtaTransportInflateReceiver::is_active(const uint8_t* mid, uint16_t block) const {
  return _active && mid && block == _block && memcmp(mid, _mid, sizeof(_mid)) == 0;
}

void OtaTransportInflateReceiver::send_trampoline(void* ctx, const uint8_t* msg,
                                                  uint16_t len, bool flood) {
  if (ctx) static_cast<OtaTransportInflateReceiver*>(ctx)->send(msg, len, flood);
}

void OtaTransportInflateReceiver::send(const uint8_t* msg, uint16_t len, bool flood) {
  if (!_send || !msg || len == 0) return;

  // A new manifest request starts a new negotiation. A retry while still waiting for that manifest is also
  // safe to reset because no payload block can yet be active.
  if (ota_msg_type(msg, len) == OTA_GET_MANIFEST) reset_session();

  ReqMsg request;
  if (ota_msg_type(msg, len) == OTA_REQ && decode_req(msg, len, request)) {
    if (!is_active(request.manifest_id, request.block_idx)) {
      // resumeStaged() can enter FETCHING without sending GET_MANIFEST first. A different MID is therefore
      // an independent negotiation even when this shim retained conservative fallback state from an earlier
      // transfer in the same boot. Preserve that state only while moving between blocks of the same MID.
      if (!_active || memcmp(request.manifest_id, _mid, sizeof(_mid)) != 0) reset_session();
      else reset_representation();
      _active = true;
      _block = request.block_idx;
      memcpy(_mid, request.manifest_id, sizeof(_mid));
    } else if (_complete) {
      // The unchanged manager asks for this block again only after discarding it (for example, a bad proof).
      reset_representation();
    }

    if (!_legacy_session) {
      uint16_t fragments = request.want_mask;
      if (_encoded_len != 0 && _mask != _need) {
        // Preserve one sparse retry for a partially received representation. A second retry without a new
        // fragment falls back to the universally deployed profile so an old source can take over.
        if (++_partial_retries >= 2) {
          _legacy_session = true;
          reset_representation();
        } else {
          fragments = (uint16_t)(_need & ~_mask);
        }
      } else if (_request_sent && _encoded_len == 0) {
        // No valid v2 DATA answered the first request. Retry in the literal legacy profile; this also covers
        // sources which reject, rather than mask, unknown want_mask bits.
        _legacy_session = true;
        reset_representation();
      }

      if (!_legacy_session) {
        request.want_mask = ota_req_make_v2(fragments, _allow_deflate);
        uint8_t wire[16];
        const uint16_t wire_len = encode_req(wire, sizeof(wire), request);
        if (wire_len != 0) {
          _request_sent = true;
          _send(_send_ctx, wire, wire_len, flood);
          return;
        }
      }
    }
  }

  _send(_send_ctx, msg, len, flood);
}

void OtaTransportInflateReceiver::on_message(const uint8_t* msg, uint16_t len) {
  if (!_manager || !msg || len == 0) return;

  DataMsg data;
  if (ota_msg_type(msg, len) == OTA_DATA && decode_data(msg, len, data)) {
    if ((data.frag_off & OTA_DATA_V2_MARK) != 0) {
      (void)handle_v2_data(msg, len);                       // malformed/unsolicited v2 DATA fails closed
      return;
    }

    // A fully canonical legacy fragment is an actual old-source response, not a reason to discard a valid
    // v2 representation merely because an attacker sent a malformed matching-MID packet.
    const uint16_t expected = _manager->requestedBlockLength(data.manifest_id, data.block_idx);
    if (expected != 0 && is_active(data.manifest_id, data.block_idx) &&
        data.frag_off % OTA_FRAG_DATA == 0 && data.frag_off < expected) {
      uint16_t expected_slice = (uint16_t)(expected - data.frag_off);
      if (expected_slice > OTA_FRAG_DATA) expected_slice = OTA_FRAG_DATA;
      if (data.data_len == expected_slice) {
        _legacy_session = true;
        reset_representation();
      }
    }
  }

  _manager->on_message(msg, len);
}

bool OtaTransportInflateReceiver::handle_v2_data(const uint8_t* msg, uint16_t len) {
  DataMsg data;
  if (!decode_data(msg, len, data) || !is_active(data.manifest_id, data.block_idx) ||
      data.data_len <= OTA_DATA_V2_STREAM_ID_BYTES || _legacy_session) return false;

  const uint16_t block_len = _manager->requestedBlockLength(data.manifest_id, data.block_idx);
  if (block_len == 0) return false;

  uint8_t fragment = 0;
  uint16_t encoded_len = 0;
  bool deflated = false;
  if (!ota_data_v2_unpack(data.frag_off, fragment, encoded_len, deflated)) return false;
  if (encoded_len > block_len || (deflated ? encoded_len >= block_len : encoded_len != block_len) ||
      (deflated && !_allow_deflate)) return false;

  const uint16_t fragment_len = (uint16_t)(data.data_len - OTA_DATA_V2_STREAM_ID_BYTES);
  const uint32_t fragment_off = (uint32_t)fragment * OTA_FRAG_DATA_V2;
  if (fragment_off >= encoded_len || fragment_off + fragment_len > encoded_len) return false;
  uint16_t expected_slice = (uint16_t)(encoded_len - fragment_off);
  if (expected_slice > OTA_FRAG_DATA_V2) expected_slice = OTA_FRAG_DATA_V2;
  if (fragment_len != expected_slice) return false;

  const uint8_t* stream_id = data.data;
  if (_encoded_len == 0) {
    _encoded_len = encoded_len;
    _deflated = deflated;
    memcpy(_stream_id, stream_id, sizeof(_stream_id));
    _mask = 0;
    _need = transport_fragment_mask(encoded_len, OTA_FRAG_DATA_V2);
  } else if (_encoded_len != encoded_len || _deflated != deflated ||
             memcmp(_stream_id, stream_id, sizeof(_stream_id)) != 0) {
    return false;                                           // never mix different wire representations
  }

  const uint16_t bit = (uint16_t)(1u << fragment);
  if (!(_need & bit)) return false;
  if (!(_mask & bit)) {
    memcpy(_encoded + fragment_off, data.data + OTA_DATA_V2_STREAM_ID_BYTES, fragment_len);
    _mask |= bit;
    _partial_retries = 0;
  }
  if (_mask != _need) return true;

  uint8_t actual_id[4];
  mh4(actual_id, _encoded, _encoded_len);
  if (memcmp(actual_id, _stream_id, sizeof(actual_id)) != 0) {
    reset_representation();
    return true;
  }

  const uint8_t* logical = _encoded;
  if (_deflated) {
    uint16_t decoded_len = 0;
    if (!ota_transport_inflate(nullptr, _encoded, _encoded_len,
                               _decoded, block_len, &decoded_len) || decoded_len != block_len) {
      // Keep the 171-byte profile but stop offering DEFLATE for this session. The next manager retry asks the
      // source for raw v2 DATA, which still stages the exact same authenticated `.mota` block.
      _allow_deflate = false;
      reset_representation();
      return true;
    }
    logical = _decoded;
  }

  // Feed the historical manager only the canonical representation it already knows. Its existing final
  // fragment transition requests/accepts the ordinary proof; Merkle verification happens before any write.
  for (uint16_t off = 0; off < block_len; off = (uint16_t)(off + OTA_FRAG_DATA)) {
    uint16_t slice = (uint16_t)(block_len - off);
    if (slice > OTA_FRAG_DATA) slice = OTA_FRAG_DATA;
    DataMsg canonical;
    memcpy(canonical.manifest_id, _mid, sizeof(canonical.manifest_id));
    canonical.block_idx = _block;
    canonical.frag_off = off;
    canonical.data = logical + off;
    canonical.data_len = slice;
    uint8_t wire[9 + OTA_FRAG_DATA];
    const uint16_t wire_len = encode_data(wire, sizeof(wire), canonical);
    if (wire_len == 0) {
      reset_representation();
      return false;
    }
    _manager->on_message(wire, wire_len);
  }
  _complete = true;
  return true;
}

} // namespace ota
} // namespace mesh

#endif // ENABLE_OTA || OTA_TRANSPORT_DEFLATE_TEST
