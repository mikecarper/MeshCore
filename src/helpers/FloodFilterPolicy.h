#pragma once

#include <stddef.h>
#include <stdint.h>
#include <string.h>

#include <Packet.h>

namespace FloodFilterPolicy {

static constexpr uint8_t BLACKLIST_ID_SIZE = 3;
static constexpr uint8_t SLOW_SCOPE_FLAG = 0x80;
static constexpr uint8_t SLOW_SCOPE_TX_DELAY_FACTOR = 2;
static constexpr float SLOW_SCOPE_RX_DELAY_MIN = 2.0f;
static constexpr uint32_t MAX_DISPATCH_DELAY = 0xFFFFFF;

enum ChannelScopeGate {
  CHANNEL_SCOPE_USE_GLOBAL,
  CHANNEL_SCOPE_BYPASS,
  CHANNEL_SCOPE_REQUIRED_ALLOWED,
  CHANNEL_SCOPE_REQUIRED_REJECTED,
};

inline ChannelScopeGate channelScopeGate(bool table_active,
                                         bool is_group_channel_packet,
                                         bool channel_requires_scope,
                                         bool incoming_is_scoped,
                                         bool incoming_region_allowed) {
  if (!table_active || !is_group_channel_packet) {
    return CHANNEL_SCOPE_USE_GLOBAL;
  }
  if (!channel_requires_scope) return CHANNEL_SCOPE_BYPASS;
  return incoming_is_scoped && incoming_region_allowed
      ? CHANNEL_SCOPE_REQUIRED_ALLOWED
      : CHANNEL_SCOPE_REQUIRED_REJECTED;
}

inline uint8_t encodeScopeSelector(uint8_t selector, bool slow) {
  return slow ? (uint8_t)(selector | SLOW_SCOPE_FLAG) : selector;
}

inline uint8_t scopeSelectorValue(uint8_t stored_selector) {
  return stored_selector & (uint8_t)~SLOW_SCOPE_FLAG;
}

inline bool scopeUsesSlowTiming(uint8_t stored_selector) {
  return (stored_selector & SLOW_SCOPE_FLAG) != 0;
}

inline bool fastTrackScopeChange(bool scope_changed, bool slow) {
  return scope_changed && !slow;
}

inline float slowScopeRxDelayBase(float configured_base) {
  float doubled = configured_base * 2.0f;
  return doubled < SLOW_SCOPE_RX_DELAY_MIN
      ? SLOW_SCOPE_RX_DELAY_MIN : doubled;
}

inline uint32_t slowScopeMaxDelay(uint32_t airtime_millis) {
  const uint64_t delay = (uint64_t)airtime_millis
      * SLOW_SCOPE_TX_DELAY_FACTOR * 5U;
  return delay > MAX_DISPATCH_DELAY
      ? MAX_DISPATCH_DELAY : (uint32_t)delay;
}

inline uint8_t blacklistMatchThreshold(uint8_t path_hash_size) {
  if (path_hash_size == BLACKLIST_ID_SIZE) return 1;
  if (path_hash_size == 2) return 2;
  return 0;
}

inline bool pathMatchesBlacklist(const mesh::Packet* packet,
                                 const uint8_t* blacklist,
                                 uint8_t blacklist_count) {
  if (packet == NULL || blacklist == NULL || blacklist_count == 0) return false;

  const uint8_t hash_size = packet->getPathHashSize();
  const uint8_t required = blacklistMatchThreshold(hash_size);
  if (required == 0) return false;

  uint8_t matches = 0;
  const uint8_t path_hops = packet->getPathHashCount();
  for (uint8_t hop = 0; hop < path_hops; hop++) {
    const uint8_t* path_id = &packet->path[hop * hash_size];
    for (uint8_t i = 0; i < blacklist_count; i++) {
      const uint8_t* listed_id = &blacklist[i * BLACKLIST_ID_SIZE];
      if (memcmp(path_id, listed_id, hash_size) == 0) {
        matches++;
        if (matches >= required) return true;
        break;
      }
    }
  }
  return false;
}

inline bool scopeRuleAllowed(bool requires_region_match,
                             bool incoming_region_allowed) {
  return !requires_region_match || incoming_region_allowed;
}

inline bool setTransportScope(mesh::Packet* packet, uint16_t transport_code) {
  if (packet == NULL || !packet->isRouteFlood()) return false;

  const bool changed = packet->getRouteType() != ROUTE_TYPE_TRANSPORT_FLOOD
      || packet->transport_codes[0] != transport_code
      || packet->transport_codes[1] != 0;
  if (!changed) return false;

  packet->header =
      (packet->header & (uint8_t)~PH_ROUTE_MASK) | ROUTE_TYPE_TRANSPORT_FLOOD;
  packet->transport_codes[0] = transport_code;
  packet->transport_codes[1] = 0;
  return true;
}

}  // namespace FloodFilterPolicy
