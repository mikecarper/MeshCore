#pragma once

#include <stddef.h>
#include <stdint.h>

namespace mesh {

// A repeater/room-server status response starts with the reflected four-byte
// request tag. The app currently parses 48 bytes of status fields after that
// tag; newer firmware may append additional counters.
static constexpr size_t COMPANION_STATUS_RESPONSE_TAG_SIZE = 4;
static constexpr size_t COMPANION_MIN_STATUS_DATA_SIZE = 48;
static constexpr size_t COMPANION_MIN_STATUS_RESPONSE_SIZE =
    COMPANION_STATUS_RESPONSE_TAG_SIZE + COMPANION_MIN_STATUS_DATA_SIZE;

inline bool companionStatusTagMatches(uint32_t pending_tag,
                                      uint32_t response_tag) {
  return pending_tag != 0 && response_tag == pending_tag;
}

inline bool companionStatusResponseIsLongEnough(size_t response_len) {
  return response_len >= COMPANION_MIN_STATUS_RESPONSE_SIZE;
}

}  // namespace mesh
