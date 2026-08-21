#pragma once

#include <stddef.h>
#include <stdint.h>
#include <string.h>

#include <Packet.h>

namespace FloodFilterPolicy {

static constexpr uint8_t BLACKLIST_ID_SIZE = 3;
static constexpr uint8_t SCOPE_PATH_LOW_MASK = 0x0C;
static constexpr uint8_t SCOPE_PATH_HIGH_FLAG = 0x40;
static constexpr uint8_t SCOPE_PATH_MASK =
    SCOPE_PATH_LOW_MASK | SCOPE_PATH_HIGH_FLAG;
static constexpr uint8_t SLOW_SCOPE_FLAG = 0x80;
static constexpr uint8_t SLOW_SCOPE_TX_DELAY_FACTOR = 2;
static constexpr float SLOW_SCOPE_RX_DELAY_MIN = 2.0f;
static constexpr uint32_t MAX_DISPATCH_DELAY = 0xFFFFFF;
static constexpr uint8_t SCOPE_PATH_NONE = 0;
static constexpr uint8_t SCOPE_PATH_BLACKLIST = 1;
static constexpr uint8_t SCOPE_PATH_BRIDGE_BUCKET_BASE = 2;
static constexpr uint8_t SCOPE_PATH_BRIDGE_BUCKET_COUNT = 6;
static constexpr uint8_t SCOPE_PATH_INVALID_BUCKET = 0xFF;

enum RuleIncomingScope : uint8_t {
  RULE_IN_ANY = 0,
  RULE_IN_NONE = 1,
  RULE_IN_SCOPED = 2,
  RULE_IN_ALLOWED = 3,
  RULE_IN_UNKNOWN = 4,
  RULE_IN_SCOPE = 5,
  RULE_IN_REGION = 6,
};

enum ChannelScopeGate {
  CHANNEL_SCOPE_USE_GLOBAL,
  CHANNEL_SCOPE_BYPASS,
  CHANNEL_SCOPE_REQUIRED_ALLOWED,
  CHANNEL_SCOPE_REQUIRED_REJECTED,
};

// Persist only through the highest occupied forward-rule slot.  The on-disk
// formats are dense, so inactive holes before that slot must remain present,
// while the usually-large inactive tail need not consume LittleFS space.
// `empty_forward_phase` deliberately wins over the live table for atomic
// phase-clearing transactions.
template<typename Entry>
inline uint8_t forwardPersistenceCount(const Entry entries[],
                                       uint8_t slot_count,
                                       bool empty_forward_phase) {
  if (empty_forward_phase || entries == NULL) return 0;
  while (slot_count > 0 && !entries[slot_count - 1].active) slot_count--;
  return slot_count;
}

inline bool forwardPersistenceCountSupported(uint8_t count,
                                             uint8_t slot_count) {
  return count <= slot_count;
}

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

inline uint8_t encodeScopeSelector(uint8_t selector, bool slow,
                                   uint8_t path_selector = SCOPE_PATH_NONE) {
  uint8_t stored = selector;
  if (slow) stored |= SLOW_SCOPE_FLAG;
  if (path_selector == SCOPE_PATH_BLACKLIST) {
    stored |= SCOPE_PATH_HIGH_FLAG;
  } else if (path_selector >= SCOPE_PATH_BRIDGE_BUCKET_BASE
      && path_selector < SCOPE_PATH_BRIDGE_BUCKET_BASE
          + SCOPE_PATH_BRIDGE_BUCKET_COUNT) {
    uint8_t bucket =
        (uint8_t)(path_selector - SCOPE_PATH_BRIDGE_BUCKET_BASE);
    stored |= (uint8_t)(((bucket % 3U) + 1U) << 2);
    if (bucket >= 3U) stored |= SCOPE_PATH_HIGH_FLAG;
  }
  return stored;
}

inline uint8_t scopeSelectorValue(uint8_t stored_selector) {
  return stored_selector
      & (uint8_t)~(SLOW_SCOPE_FLAG | SCOPE_PATH_MASK);
}

// Regionless channel-scope rows use alternate base selector values so the
// existing 16-bit target field can hold a one-based direct-scope table index.
// Region-backed rows retain their original on-disk selector values.
static constexpr uint8_t CHANNEL_SCOPE_DIRECT_KEY_128 = 17;
static constexpr uint8_t CHANNEL_SCOPE_DIRECT_KEY_256 = 18;
static constexpr uint8_t CHANNEL_SCOPE_DIRECT_TXT_ANY = 33;
static constexpr uint8_t CHANNEL_SCOPE_DIRECT_LOGIN_ANY = 34;
static constexpr uint8_t CHANNEL_SCOPE_DIRECT_OTHER_ANY = 35;
static constexpr uint8_t CHANNEL_SCOPE_INVALID_SELECTOR = 0xFF;

inline uint8_t encodeChannelScopeTargetSelector(uint8_t match_selector,
                                                bool direct_target) {
  if (!direct_target) return match_selector;
  if (match_selector <= 2) {
    return (uint8_t)(CHANNEL_SCOPE_DIRECT_TXT_ANY + match_selector);
  }
  if (match_selector == 16 || match_selector == 32) {
    return (uint8_t)(16 + match_selector / 16);
  }
  return CHANNEL_SCOPE_INVALID_SELECTOR;
}

inline bool channelScopeUsesDirectTarget(uint8_t stored_selector) {
  uint8_t selector = scopeSelectorValue(stored_selector);
  return (selector >= CHANNEL_SCOPE_DIRECT_KEY_128
          && selector <= CHANNEL_SCOPE_DIRECT_KEY_256)
      || (selector >= CHANNEL_SCOPE_DIRECT_TXT_ANY
          && selector <= CHANNEL_SCOPE_DIRECT_OTHER_ANY);
}

inline uint8_t channelScopeMatchSelectorValue(uint8_t stored_selector) {
  uint8_t selector = scopeSelectorValue(stored_selector);
  if (selector >= CHANNEL_SCOPE_DIRECT_KEY_128
      && selector <= CHANNEL_SCOPE_DIRECT_KEY_256) {
    return (uint8_t)((selector - 16) * 16);
  }
  if (selector >= CHANNEL_SCOPE_DIRECT_TXT_ANY
      && selector <= CHANNEL_SCOPE_DIRECT_OTHER_ANY) {
    return (uint8_t)(selector - CHANNEL_SCOPE_DIRECT_TXT_ANY);
  }
  return selector;
}

inline bool scopeUsesSlowTiming(uint8_t stored_selector) {
  return (stored_selector & SLOW_SCOPE_FLAG) != 0;
}

inline uint8_t scopePathSelectorValue(uint8_t stored_selector) {
  uint8_t low = (uint8_t)((stored_selector & SCOPE_PATH_LOW_MASK) >> 2);
  bool high = (stored_selector & SCOPE_PATH_HIGH_FLAG) != 0;
  if (low == 0) return high ? SCOPE_PATH_BLACKLIST : SCOPE_PATH_NONE;
  uint8_t bucket = (uint8_t)(low - 1U + (high ? 3U : 0U));
  return (uint8_t)(SCOPE_PATH_BRIDGE_BUCKET_BASE + bucket);
}

inline bool scopeRequiresPath(uint8_t stored_selector) {
  return scopePathSelectorValue(stored_selector) != SCOPE_PATH_NONE;
}

inline bool scopeRequiresBlacklistPath(uint8_t stored_selector) {
  return scopePathSelectorValue(stored_selector) == SCOPE_PATH_BLACKLIST;
}

inline uint8_t scopeBridgeBucketIndex(uint8_t stored_selector) {
  uint8_t path_selector = scopePathSelectorValue(stored_selector);
  if (path_selector < SCOPE_PATH_BRIDGE_BUCKET_BASE
      || path_selector >= SCOPE_PATH_BRIDGE_BUCKET_BASE
          + SCOPE_PATH_BRIDGE_BUCKET_COUNT) {
    return SCOPE_PATH_INVALID_BUCKET;
  }
  return (uint8_t)(path_selector - SCOPE_PATH_BRIDGE_BUCKET_BASE);
}

inline bool scopePathQualifierMatches(uint8_t stored_selector,
                                      bool path_matches) {
  return !scopeRequiresPath(stored_selector)
      || path_matches;
}

inline bool scopePathPassMatches(uint8_t stored_selector,
                                 bool qualified_pass,
                                 bool path_matches) {
  return scopeRequiresPath(stored_selector) == qualified_pass
      && scopePathQualifierMatches(stored_selector, path_matches);
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

inline uint8_t configuredIdCount(const uint8_t* ids,
                                 uint8_t maximum_count) {
  if (ids == NULL) return 0;
  uint8_t count = 0;
  while (count < maximum_count) {
    const uint8_t* id = &ids[count * BLACKLIST_ID_SIZE];
    if (id[0] == 0 && id[1] == 0 && id[2] == 0) break;
    count++;
  }
  return count;
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

inline bool pathMatchesConfiguredIds(const mesh::Packet* packet,
                                     const uint8_t* ids,
                                     uint8_t maximum_count) {
  return pathMatchesBlacklist(
      packet, ids, configuredIdCount(ids, maximum_count));
}

inline bool pathStartsWith(const mesh::Packet* packet,
                           uint8_t hash_size,
                           uint8_t prefix_hops,
                           const uint8_t* prefix) {
  if (prefix_hops == 0) return true;
  if (packet == NULL || prefix == NULL || hash_size < 1 || hash_size > 3
      || packet->getPathHashSize() != hash_size
      || packet->getPathHashCount() < prefix_hops) {
    return false;
  }
  return memcmp(packet->path, prefix, hash_size * prefix_hops) == 0;
}

inline bool ruleIncomingScopeMatches(uint8_t kind,
                                     bool incoming_is_scoped,
                                     uint16_t incoming_transport_code,
                                     bool incoming_region_allowed,
                                     uint16_t wanted_transport_code) {
  switch (kind) {
    case RULE_IN_ANY:
      return true;
    case RULE_IN_NONE:
      return !incoming_is_scoped;
    case RULE_IN_SCOPED:
      return incoming_is_scoped;
    case RULE_IN_ALLOWED:
      return incoming_region_allowed;
    case RULE_IN_UNKNOWN:
      return incoming_is_scoped && !incoming_region_allowed;
    case RULE_IN_SCOPE:
      return incoming_is_scoped
          && incoming_transport_code == wanted_transport_code;
    case RULE_IN_REGION:
      // Region rules are name-bound and must be evaluated by the caller with
      // RegionNameUtils. Numeric region IDs are deliberately not rule identity.
      return false;
    default:
      return false;
  }
}

inline bool rateLimitReached(bool window_active, uint32_t now,
                             uint32_t window_started, uint16_t window_count,
                             uint16_t rate_per_minute) {
  uint16_t effective_count = (!window_active
          || now - window_started >= 60000UL)
      ? 0 : window_count;
  return effective_count >= rate_per_minute;
}

inline bool sameChannelKey(uint8_t left_len, const uint8_t left[],
                           uint8_t right_len, const uint8_t right[]) {
  return left != NULL && right != NULL && left_len != 0
      && left_len == right_len && memcmp(left, right, left_len) == 0;
}

template<typename RuleMask>
inline int nextOrderedRule(RuleMask match_mask, RuleMask visited_mask,
                           const uint8_t priorities[], uint8_t count) {
  if (priorities == NULL || count > sizeof(RuleMask) * 8U) return -1;
  int best = -1;
  for (uint8_t i = 0; i < count; i++) {
    RuleMask bit = (RuleMask)1U << i;
    if ((match_mask & bit) == 0 || (visited_mask & bit) != 0) continue;
    if (best < 0 || priorities[i] > priorities[best]) best = i;
  }
  return best;
}

template<typename RuleMask>
inline RuleMask truncateRulesAtStop(RuleMask match_mask,
                                    const uint8_t priorities[],
                                    const uint8_t stop_flags[],
                                    uint8_t count) {
  if (stop_flags == NULL) return match_mask;
  RuleMask effective = 0;
  RuleMask visited = 0;
  while (true) {
    int index = nextOrderedRule(match_mask, visited, priorities, count);
    if (index < 0) break;
    RuleMask bit = (RuleMask)1U << index;
    visited |= bit;
    effective |= bit;
    if (stop_flags[index] != 0) break;
  }
  return effective;
}

inline bool stopActionApplies(bool stop_on_match, bool has_region_target,
                              bool region_target_usable) {
  // A direct scope= target is derived from its public name and is always
  // usable. A region= target is configuration-backed; if it disappeared or
  // can no longer carry floods, its rewrite and its terminal stop are inert.
  return stop_on_match
      && (!has_region_target || region_target_usable);
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
