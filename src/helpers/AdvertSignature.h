#pragma once

#include <stdint.h>
#include <string.h>
#include <MeshCore.h>

// Signed-message layout of an ADVERT payload, mirroring Mesh::onRecvPacket:
// pub_key(32) + timestamp(4) + signature(64) + app_data. Oversized app_data is
// rejected, like Mesh::onRecvPacket: truncation would accept an unsigned tail.
namespace AdvertSignature {

static const int kHeaderLen = PUB_KEY_SIZE + 4 + SIGNATURE_SIZE;
static const int kMaxMessageLen = PUB_KEY_SIZE + 4 + MAX_ADVERT_DATA_SIZE;

// Builds the signed message into `out` (kMaxMessageLen bytes) and points `sig`
// at the signature. Returns the message length, or -1 for invalid arguments or
// a payload outside the complete signed advert layout. Clears sig on failure.
static inline int buildMessage(const uint8_t* payload, int payload_len,
                               uint8_t* out, const uint8_t** sig) {
  if (sig == nullptr) return -1;
  *sig = nullptr;
  if (payload == nullptr || out == nullptr || payload_len < kHeaderLen ||
      payload_len > kHeaderLen + MAX_ADVERT_DATA_SIZE) return -1;
  int app_data_len = payload_len - kHeaderLen;
  memcpy(out, payload, PUB_KEY_SIZE + 4);
  memcpy(out + PUB_KEY_SIZE + 4, payload + kHeaderLen, app_data_len);
  *sig = payload + PUB_KEY_SIZE + 4;
  return PUB_KEY_SIZE + 4 + app_data_len;
}

}  // namespace AdvertSignature
