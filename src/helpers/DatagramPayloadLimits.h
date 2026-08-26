#pragma once

#include <stddef.h>

namespace DatagramPayloadLimits {

// Encrypted datagrams append a MAC and round the plaintext up to a whole
// cipher block. Reserving block_size - 1 bytes covers the worst-case padding.
static constexpr size_t maxPlaintext(size_t packet_payload_size,
                                     size_t mac_size,
                                     size_t block_size) {
  return block_size > 0 &&
                 packet_payload_size >= mac_size + block_size - 1
             ? packet_payload_size - mac_size - (block_size - 1)
             : 0;
}

}  // namespace DatagramPayloadLimits
