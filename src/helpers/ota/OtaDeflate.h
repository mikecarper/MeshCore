#pragma once

#include <stdint.h>
#include "OtaManager.h"
#include "OtaProtocol.h"

namespace mesh {
namespace ota {

// Full raw RFC1951 transport decoder (stored, fixed-Huffman, and dynamic-Huffman blocks).
// `dst_cap` is the exact expected logical block length, not merely spare capacity. Success
// requires exact output and exact whole-byte input consumption; malformed/truncated streams,
// output overflow, and trailing bytes fail closed.
bool ota_transport_inflate(void* context, const uint8_t* src, uint16_t src_len,
                           uint8_t* dst, uint16_t dst_cap, uint16_t* dst_len);

// Receive-only compatibility shim for historical OTA managers. The wrapped manager continues to reassemble
// canonical 160-byte DATA, verify the Merkle proof, and stage the original `.mota`. This shim only negotiates
// the v2 wire profile, locks and reassembles one encoded representation, exact-inflates it, and injects the
// resulting logical block back into that unchanged path. Bridge builds use OTA_FETCH_PIPELINE=1, so one fixed
// encoded block and one fixed decoded block cover every in-flight request without heap allocation.
class OtaTransportInflateReceiver {
public:
  void begin(OtaManager& manager, uint32_t target_id, OtaSend send, void* send_ctx);
  void on_message(const uint8_t* msg, uint16_t len);
  static void send_trampoline(void* ctx, const uint8_t* msg, uint16_t len, bool flood);

private:
  void send(const uint8_t* msg, uint16_t len, bool flood);
  bool handle_v2_data(const uint8_t* msg, uint16_t len);
  void reset_session();
  void reset_representation();
  bool is_active(const uint8_t* mid, uint16_t block) const;

  OtaManager* _manager = nullptr;
  OtaSend _send = nullptr;
  void* _send_ctx = nullptr;
  uint8_t _mid[4] = {0};
  uint8_t _stream_id[4] = {0};
  uint16_t _block = 0;
  uint16_t _encoded_len = 0;
  uint16_t _mask = 0;
  uint16_t _need = 0;
  uint8_t _partial_retries = 0;
  bool _active = false;
  bool _request_sent = false;
  bool _complete = false;
  bool _deflated = false;
  bool _allow_deflate = true;
  bool _legacy_session = false;
  uint8_t _encoded[OTA_MAX_BLOCK];
  uint8_t _decoded[OTA_MAX_BLOCK];
};

} // namespace ota
} // namespace mesh
