#pragma once

#include <stddef.h>
#include <stdint.h>
#include <string.h>

namespace mesh {

// These two full-log opcodes are internal queue state. Transports expand
// radio2 to the compatibility marker followed by a legacy 0x88 log.
constexpr uint8_t COMPANION_LEGACY_RX_LOG = 0x88;
constexpr uint8_t COMPANION_RADIO2_LOG = 0x92;
constexpr uint8_t COMPANION_RADIO2_LOG_PENDING = 0x93;
constexpr uint8_t COMPANION_RADIO2_LOG_MARKER[] = {0x92, 0xA1};

inline bool companionRadioLogMarkerPending(const uint8_t* frame, size_t len) {
  return frame != nullptr && len >= 3 && frame[0] == COMPANION_RADIO2_LOG;
}

inline bool companionRadioLogDataPending(const uint8_t* frame, size_t len) {
  return frame != nullptr && len >= 3 && frame[0] == COMPANION_RADIO2_LOG_PENDING;
}

inline bool isCompanionRadio2Log(const uint8_t* frame, size_t len) {
  return frame != nullptr && len >= 3
      && (frame[0] & 0xFE) == COMPANION_RADIO2_LOG;
}

// Callers validate their queued frame and provide len + 8 bytes of output.
// Share wire framing across USB/UART and TCP without enlarging queue entries.
#if defined(__GNUC__) && !defined(__clang__)
__attribute__((noinline, noclone))
#elif defined(__GNUC__) || defined(__clang__)
__attribute__((noinline))
#endif
inline size_t buildCompanionWireFrame(uint8_t* output, const uint8_t* frame,
                                     size_t len) {
  const bool radio2_log = isCompanionRadio2Log(frame, len);
  const size_t prefix = radio2_log ? 5U : 0U;
  if (radio2_log) {
    const uint8_t marker[] = {'>', 2, 0, COMPANION_RADIO2_LOG, 0xA1};
    memcpy(output, marker, sizeof(marker));
  }
  output[prefix] = '>';
  output[prefix + 1] = uint8_t(len);
  output[prefix + 2] = uint8_t(len >> 8);
  memcpy(output + prefix + 3, frame, len);
  if (radio2_log) output[prefix + 3] = COMPANION_LEGACY_RX_LOG;
  return prefix + 3U + len;
}

}  // namespace mesh
