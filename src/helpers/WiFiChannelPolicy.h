#pragma once

#include <stdint.h>

namespace mesh {
namespace wifi {

// ESP-NOW primary-radio targets use the 2.4 GHz channel range that can also
// carry ordinary b/g/n WiFi. Channel 14 is deliberately excluded: it is
// country-specific and restricted to 802.11b, so it cannot satisfy the shared
// b/g/n+LR coexistence policy used by Full builds.
static constexpr uint8_t kEspNowChannelMin = 1;
static constexpr uint8_t kEspNowChannelMax = 13;

inline bool isValidEspNowChannel(unsigned long channel) {
  return channel >= kEspNowChannelMin && channel <= kEspNowChannelMax;
}

// Strict whole-value parser for CLI and WebConfig input. Whitespace around the
// decimal channel is accepted; signs, decimal points, and trailing text are
// rejected so a malformed request cannot silently select a different channel.
inline bool parseEspNowChannel(const char* value, uint8_t& channel) {
  if (value == nullptr) return false;
  while (*value == ' ' || *value == '\t') value++;
  if (*value < '0' || *value > '9') return false;

  unsigned long parsed = 0;
  do {
    parsed = parsed * 10UL + static_cast<unsigned long>(*value - '0');
    if (parsed > kEspNowChannelMax) return false;
    value++;
  } while (*value >= '0' && *value <= '9');

  while (*value == ' ' || *value == '\t') value++;
  if (*value != 0 || !isValidEspNowChannel(parsed)) return false;
  channel = static_cast<uint8_t>(parsed);
  return true;
}

inline uint8_t validEspNowChannelOrDefault(uint8_t stored,
                                           uint8_t fallback) {
  return isValidEspNowChannel(stored) ? stored : fallback;
}

// Rewriting an ESP32's current WiFi channel is not a harmless no-op. The
// driver briefly reconfigures the shared RF path, which can interrupt SoftAP
// beacons and ESP-NOW receive when a caller repeats the write from a fast main
// loop. Only request a restore when the primary channel actually moved.
inline bool espNowChannelRestoreRequired(uint8_t current, uint8_t target) {
  return current != target;
}

} // namespace wifi
} // namespace mesh
