#pragma once

#include "MeshtextChat.h"
#include <string.h>

namespace mesh { namespace mt {

// Three retained chats and one metadata slot. No heap, packet-pool entries,
// keys, positions or forwarding state are allocated by the delivery policy.
struct PendingFrame {
  uint8_t bytes[MaxFrameSize] = {};
  uint8_t fingerprint[8] = {};
  uint32_t generation = 0, due = 0;
  uint16_t length = 0;
  uint8_t channel = 0, retries = 0;
  bool nodeinfo = false, sent = false, heard = false, cancelled = false;

  void clear() { length = 0; }
  bool matchesEcho(const uint8_t* raw, size_t size) const {
    // The sender/id/channel/ciphertext are immutable. Only remaining hops
    // and next-hop/relay bytes may change. A copy at the original hop limit
    // is not evidence that another node forwarded the transmission.
    return length && sent && raw && size == length && size > HeaderSize
        && memcmp(raw, bytes, 12) == 0 && raw[13] == bytes[13]
        && (raw[12] & 0xf8) == (bytes[12] & 0xf8)
        && (raw[12] & 7) < (bytes[12] & 7)
        && memcmp(raw + HeaderSize, bytes + HeaderSize, size - HeaderSize) == 0;
  }
};

static constexpr unsigned DeliverySlots = 4;
static constexpr unsigned ChatDeliverySlots = DeliverySlots - 1;

} } // namespace mesh::mt
