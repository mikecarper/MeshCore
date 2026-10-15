#pragma once

#include <stddef.h>
#include <stdint.h>
#include <string.h>
#include "OtaFormat.h"

namespace mesh { namespace ota {

// KISS DATA carries these radio bytes unchanged. KISS escaping stays on the
// host cable, never on RF. The reserved MeshCore version makes old endpoints
// reject the frame before interpreting any OTA bytes; it is always zero-hop.
// This bypasses Packet's routed 184-byte payload, without enlarging its pool.
static constexpr uint8_t OTA_DIRECT_HEADER = 0xF2; // v4, OTA, DIRECT
static constexpr uint16_t OTA_DIRECT_RF_MAX = 255;
static constexpr uint16_t OTA_DIRECT_HEADER_BYTES = 2;
static constexpr uint16_t OTA_DIRECT_MESSAGE_MAX =
    OTA_DIRECT_RF_MAX - OTA_DIRECT_HEADER_BYTES;
static constexpr uint16_t OTA_DIRECT_FRAG_DATA =
    OTA_DIRECT_MESSAGE_MAX - 9 - 4; // OTA_DATA framing + v2 stream identity

inline bool ota_direct_wire_marker(const uint8_t* bytes, size_t length) {
  return bytes && length && bytes[0] == OTA_DIRECT_HEADER;
}

inline bool ota_direct_wire_decode(const uint8_t* bytes, size_t length,
                                  const uint8_t*& message, uint16_t& message_len) {
  message = nullptr;
  message_len = 0;
  if (!ota_direct_wire_marker(bytes, length) || length < 3
      || length > OTA_DIRECT_RF_MAX || bytes[1] != 0
      || !ota_is_transfer_message(bytes[2])) return false;
  message = bytes + OTA_DIRECT_HEADER_BYTES;
  message_len = static_cast<uint16_t>(length - OTA_DIRECT_HEADER_BYTES);
  return true;
}

inline uint16_t ota_direct_wire_encode(uint8_t* output, size_t capacity,
                                      const uint8_t* message, uint16_t length) {
  if (!output || !message || !length || length > OTA_DIRECT_MESSAGE_MAX
      || capacity < size_t(length) + OTA_DIRECT_HEADER_BYTES
      || !ota_is_transfer_message(message[0])) return 0;
  output[0] = OTA_DIRECT_HEADER;
  output[1] = 0; // no path, transport codes, forwarding or Ethernet overhead
  memcpy(output + OTA_DIRECT_HEADER_BYTES, message, length);
  return static_cast<uint16_t>(length + OTA_DIRECT_HEADER_BYTES);
}

static_assert(OTA_DIRECT_FRAG_DATA == 240, "direct KISS OTA payload budget");

} }
