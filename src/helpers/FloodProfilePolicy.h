#pragma once
#include <stdint.h>
#include <string.h>
#include <MeshCore.h>
namespace mesh { namespace flood_profiles {
// Only relaying is controlled here: locally generated replies and direct routes
// do not pass through either flood-forwarding gate.
template<typename Packet, typename Policy>
bool allowIngress(const Packet* packet, const Policy& policy) {
  return packet && (!packet->isRouteFlood() || policy.floodProfileAllowed(packet->radio_profile));
}
template<typename Packet, typename Policy>
bool allowEgress(const Packet* packet, const Policy& policy) {
  return !packet || !packet->forwarded || !packet->isRouteFlood()
      || policy.floodProfileAllowed(packet->radio_profile);
}

// Preserve one forwarding opportunity when a packet was locally processed on
// a disabled ingress profile before another profile heard the same flood. The
// normal mesh seen table still owns delivery deduplication. Candidates are
// recorded only after the packet has reached the ordinary forwarding gate.
class DeferredFloods {
  struct Entry { uint8_t hash[MAX_HASH_SIZE]; uint32_t at; uint8_t profile; bool valid; };
  Entry entries_[8] = {};
  uint8_t next_ = 0;
  static constexpr uint32_t LifetimeMs = 120000;
public:
  template<typename Packet, typename Policy>
  void remember(const Packet* packet, const Policy& policy, uint32_t now) {
    if (!packet || !packet->isRouteFlood() || packet->radio_profile > 1 ||
        !policy.floodProfileAllowed(packet->radio_profile ^ 1)) return;
    uint8_t hash[MAX_HASH_SIZE]; packet->calculatePacketHash(hash);
    Entry& entry = entries_[next_++ % 8];
    memcpy(entry.hash, hash, sizeof(hash)); entry.at = now;
    entry.profile = packet->radio_profile; entry.valid = true;
  }
  template<typename Packet, typename Policy>
  bool match(const Packet* packet, const Policy& policy, uint32_t now, uint8_t profile, bool consume = false) {
    if (!packet || !packet->isRouteFlood() || profile > 1 ||
        !policy.floodProfileAllowed(profile)) return false;
    uint8_t hash[MAX_HASH_SIZE]; packet->calculatePacketHash(hash);
    for (auto& entry : entries_) {
      if (entry.valid && uint32_t(now - entry.at) >= LifetimeMs) entry.valid = false;
      if (entry.valid && entry.profile != profile && !memcmp(hash, entry.hash, sizeof(hash))) {
        if (consume) entry.valid = false;
        return true;
      }
    }
    return false;
  }
};
}}
