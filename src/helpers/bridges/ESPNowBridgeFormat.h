#pragma once

#include <stddef.h>
#include <stdint.h>
#include <string.h>
#include <helpers/ESPNowRawFragmentation.h>

namespace mesh {
namespace bridge {

// Keep wrapped as zero so every existing preference image and a freshly
// zero-initialized NodePrefs retains the original bridge wire format.
static constexpr uint8_t ESPNOW_FORMAT_WRAPPED = 0;
static constexpr uint8_t ESPNOW_FORMAT_RAW = 1;

static constexpr size_t ESPNOW_MAX_FRAME_SIZE = 250;
static constexpr size_t ESPNOW_WRAPPED_OVERHEAD = 4;

static inline bool isValidEspNowFormat(uint8_t format) {
  return format == ESPNOW_FORMAT_WRAPPED || format == ESPNOW_FORMAT_RAW;
}

static inline bool parseEspNowFormat(const char* text, uint8_t& format) {
  if (text == nullptr) return false;
  if (strcmp(text, "wrapped") == 0) {
    format = ESPNOW_FORMAT_WRAPPED;
    return true;
  }
  if (strcmp(text, "raw") == 0) {
    format = ESPNOW_FORMAT_RAW;
    return true;
  }
  return false;
}

static inline const char* espNowFormatName(uint8_t format) {
  return format == ESPNOW_FORMAT_RAW ? "raw" : "wrapped";
}

static inline size_t espNowMaxMeshPacketSize(uint8_t format) {
  return format == ESPNOW_FORMAT_RAW
      ? mesh::espnow::ESPNOW_RAW_MAX_PACKET_SIZE
      : ESPNOW_MAX_FRAME_SIZE - ESPNOW_WRAPPED_OVERHEAD;
}

static inline uint8_t espNowMaxChannel(uint8_t format) {
  (void)format;
  return 13;
}

static inline bool isValidEspNowBridgeChannel(uint8_t channel,
                                               uint8_t format) {
  return channel >= 1 && channel <= espNowMaxChannel(format);
}

// Accept surrounding spaces/tabs, but no signs, separators, suffixes, or
// decimal notation. Leave the caller's output unchanged on every failure.
static inline bool parseEspNowBridgeChannel(const char* text, uint8_t format,
                                             uint8_t& channel) {
  if (text == nullptr) return false;
  while (*text == ' ' || *text == '\t') text++;
  if (*text < '0' || *text > '9') return false;

  uint16_t parsed = 0;
  while (*text >= '0' && *text <= '9') {
    parsed = (uint16_t)(parsed * 10 + (uint8_t)(*text - '0'));
    if (parsed > 13) return false;
    text++;
  }
  while (*text == ' ' || *text == '\t') text++;
  if (*text != '\0' || !isValidEspNowBridgeChannel((uint8_t)parsed, format)) {
    return false;
  }

  channel = (uint8_t)parsed;
  return true;
}

}  // namespace bridge
}  // namespace mesh
