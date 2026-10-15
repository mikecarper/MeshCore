#pragma once

// Explicit lab images only. Normal release builds expose no echo command,
// allocate no benchmark state and retain their existing command behavior.
#if defined(MESH_HIL_BLE_BENCH) && MESH_HIL_BLE_BENCH
#if MESH_HIL_BLE_BENCH != 1
#error "MESH_HIL_BLE_BENCH must be explicitly 0 or 1"
#endif

#include <stdint.h>
#include <stddef.h>
#include <string.h>
#include "CompanionFrameLimits.h"

namespace mesh {
namespace companion {

// 0x78..0x7F already have legacy hardware aliases. 0x70 is lab-only and is
// distinct from normal responses/pushes; nonce and sequence belong to the host.
static constexpr uint8_t kBleBenchCommand = 0x70;
static constexpr size_t kBleBenchHeaderBytes = 15;

// Wire: 70, "BLE1", runIdLE32, sequenceLE32, payloadLengthLE16, payload.
// Echo the entire request exactly once, without generic OK or radio traffic.
// The host also limits frame size to its negotiated ATT MTU minus three.
inline size_t buildBleBenchEcho(const uint8_t* request, size_t length,
                               uint8_t* response, size_t capacity) {
  if (!request || !response || length < kBleBenchHeaderBytes
      || length > MAX_FRAME_SIZE || length > capacity
      || request[0] != kBleBenchCommand || memcmp(request + 1, "BLE1", 4)) return 0;
  const size_t payload_length = size_t(request[13]) | (size_t(request[14]) << 8);
  if (payload_length != length - kBleBenchHeaderBytes) return 0;
  memcpy(response, request, length);
  return length;
}

}  // namespace companion
}  // namespace mesh
#endif
