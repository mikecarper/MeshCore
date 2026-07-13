#pragma once

#include <Mesh.h>
#if ARDUINO
  #include <Arduino.h>
#endif

#ifdef ESP32
  #include <FS.h>
#endif

#define MAX_PACKET_HASHES  (128+32)
#ifndef MAX_PACKET_ACKS
  #define MAX_PACKET_ACKS  64
#endif
#if MAX_PACKET_ACKS < 1
  #error "MAX_PACKET_ACKS must be at least 1"
#endif
#define ACK_VALID_BYTES  ((MAX_PACKET_ACKS + 7) / 8)
#define MAX_ROUTE_HASH_BYTES   3

inline bool routeHashPrefixesOverlap(const uint8_t* a, uint8_t a_len,
                                     const uint8_t* b, uint8_t b_len) {
  if (a == NULL || b == NULL || a_len == 0 || b_len == 0
      || a_len > MAX_ROUTE_HASH_BYTES || b_len > MAX_ROUTE_HASH_BYTES) {
    return false;
  }
  uint8_t compare_len = a_len < b_len ? a_len : b_len;
  return memcmp(a, b, compare_len) == 0;
}

class SimpleMeshTables : public mesh::MeshTables {
public:
  struct RecentRepeaterInfo {
    // Identity and link quality for a next-hop path prefix.
    uint8_t prefix[MAX_ROUTE_HASH_BYTES];
    uint8_t prefix_len;
    int8_t snr_x4;
    uint32_t last_heard_millis;
  };

private:
  uint8_t _hashes[MAX_PACKET_HASHES*MAX_HASH_SIZE];
  int _next_idx;
  uint8_t _ack_hashes[MAX_PACKET_ACKS*MAX_HASH_SIZE];
  uint8_t _ack_valid[ACK_VALID_BYTES];
  int _next_ack_idx;
  uint32_t _direct_dups, _flood_dups;
  RecentRepeaterInfo* _recent_repeaters;
  int _max_recent_repeaters;

  bool hasSeenHash(const uint8_t* hash) const {
    const uint8_t* sp = _hashes;
    for (int i = 0; i < MAX_PACKET_HASHES; i++, sp += MAX_HASH_SIZE) {
      if (memcmp(hash, sp, MAX_HASH_SIZE) == 0) {
        return true;
      }
    }
    return false;
  }

  void storeHash(const uint8_t* hash) {
    memcpy(&_hashes[_next_idx*MAX_HASH_SIZE], hash, MAX_HASH_SIZE);
    _next_idx = (_next_idx + 1) % MAX_PACKET_HASHES;
  }

  bool isDedicatedAckPacket(const mesh::Packet* packet) const {
    if (packet->getPayloadType() == PAYLOAD_TYPE_ACK) {
      return packet->payload_len >= sizeof(uint32_t);
    }
    return packet->getPayloadType() == PAYLOAD_TYPE_MULTIPART
      && packet->payload_len >= sizeof(uint32_t) + 1
      && (packet->payload[0] & 0x0F) == PAYLOAD_TYPE_ACK;
  }

  bool isAckSlotValid(int idx) const {
    return (_ack_valid[idx >> 3] & (uint8_t)(1U << (idx & 7))) != 0;
  }

  void setAckSlotValid(int idx, bool valid) {
    uint8_t mask = (uint8_t)(1U << (idx & 7));
    if (valid) {
      _ack_valid[idx >> 3] |= mask;
    } else {
      _ack_valid[idx >> 3] &= (uint8_t)~mask;
    }
  }

  bool hasSeenAckHash(const uint8_t* hash) const {
    for (int i = 0; i < MAX_PACKET_ACKS; i++) {
      if (isAckSlotValid(i)
          && memcmp(&_ack_hashes[i*MAX_HASH_SIZE], hash, MAX_HASH_SIZE) == 0) {
        return true;
      }
    }
    return false;
  }

  void storeAckHash(const uint8_t* hash) {
    if (hasSeenAckHash(hash)) return;
    memcpy(&_ack_hashes[_next_ack_idx*MAX_HASH_SIZE], hash, MAX_HASH_SIZE);
    setAckSlotValid(_next_ack_idx, true);
    _next_ack_idx = (_next_ack_idx + 1) % MAX_PACKET_ACKS;
  }

  void clearAckHash(const uint8_t* hash) {
    for (int i = 0; i < MAX_PACKET_ACKS; i++) {
      if (isAckSlotValid(i)
          && memcmp(&_ack_hashes[i*MAX_HASH_SIZE], hash, MAX_HASH_SIZE) == 0) {
        setAckSlotValid(i, false);
        return;
      }
    }
  }

  int8_t weightedSnrX4RoundUp(int8_t curr_snr_x4, int8_t new_snr_x4) const {
    // Keep existing SNR heavier than a single new sample: 75% existing + 25% new.
    int32_t weighted_sum = ((int32_t)curr_snr_x4 * 3) + (int32_t)new_snr_x4;
    int32_t blended = weighted_sum / 4;  // truncates toward zero
    // "Round up" means ceil(), which only differs from truncation for positive remainders.
    if (weighted_sum > 0 && (weighted_sum % 4) != 0) {
      blended++;
    }
    if (blended > 127) {
      blended = 127;
    } else if (blended < -128) {
      blended = -128;
    }
    return (int8_t)blended;
  }

  bool extractRecentRepeater(const mesh::Packet* packet, uint8_t* prefix, uint8_t& prefix_len) const {
    // Learn repeater prefixes only from packet shapes that expose a trustworthy repeater ID.
    // For flood traffic, the last path entry is the repeater we directly heard.
    if (packet->isRouteFlood() && packet->getPathHashCount() > 0) {
      prefix_len = packet->getPathHashSize();
      if (prefix_len > MAX_ROUTE_HASH_BYTES) {
        prefix_len = MAX_ROUTE_HASH_BYTES;
      }

      const uint8_t* last_hop = &packet->path[(packet->getPathHashCount() - 1) * packet->getPathHashSize()];
      memcpy(prefix, last_hop, prefix_len);
      return true;
    }

    // If there is no flood path to inspect, fall back to payload-derived identities.
    if (packet->getPayloadType() == PAYLOAD_TYPE_ADVERT && packet->payload_len >= PUB_KEY_SIZE) {
      memcpy(prefix, packet->payload, MAX_ROUTE_HASH_BYTES);
      prefix_len = MAX_ROUTE_HASH_BYTES;
      return true;
    }

    if (packet->getPayloadType() == PAYLOAD_TYPE_CONTROL
        && packet->isRouteDirect()
        && packet->getPathHashCount() == 0
        && packet->payload_len >= 6 + MAX_ROUTE_HASH_BYTES
        && (packet->payload[0] & 0xF0) == 0x90) {
      memcpy(prefix, &packet->payload[6], MAX_ROUTE_HASH_BYTES);
      prefix_len = MAX_ROUTE_HASH_BYTES;
      return true;
    }

    return false;
  }

  bool recentRepeaterComesBefore(const RecentRepeaterInfo& a, int a_idx,
                                 const RecentRepeaterInfo& b, int b_idx) const {
    if (a.prefix_len != b.prefix_len) {
      return a.prefix_len > b.prefix_len;  // 3-byte prefixes, then 2-byte, then 1-byte.
    }
    if (a.snr_x4 != b.snr_x4) {
      return a.snr_x4 > b.snr_x4;  // Highest SNR first within each prefix length.
    }
    int cmp = memcmp(a.prefix, b.prefix, a.prefix_len);
    if (cmp != 0) {
      return cmp < 0;
    }
    return a_idx < b_idx;
  }

  void recordRecentRepeater(const mesh::Packet* packet) {
    if (_max_recent_repeaters == 0) {
      return;
    }

    uint8_t prefix[MAX_ROUTE_HASH_BYTES] = {0};
    uint8_t prefix_len = 0;
    if (!extractRecentRepeater(packet, prefix, prefix_len) || prefix_len == 0) {
      return;
    }
    setRecentRepeater(prefix, prefix_len, packet->_snr);
  }

public:
  // Recent-repeater storage is supplied only by repeater firmware. Keeping it
  // external makes this class layout identical in every translation unit;
  // role-local feature macros must never change a C++ class definition.
  SimpleMeshTables(RecentRepeaterInfo* recent_repeaters = NULL, int max_recent_repeaters = 0)
      : _recent_repeaters(recent_repeaters),
        _max_recent_repeaters(recent_repeaters != NULL && max_recent_repeaters > 0
                                  ? max_recent_repeaters : 0) {
    memset(_hashes, 0, sizeof(_hashes));
    _next_idx = 0;
    memset(_ack_hashes, 0, sizeof(_ack_hashes));
    memset(_ack_valid, 0, sizeof(_ack_valid));
    _next_ack_idx = 0;
    _direct_dups = _flood_dups = 0;
    if (_max_recent_repeaters > 0) {
      memset(_recent_repeaters, 0, _max_recent_repeaters * sizeof(RecentRepeaterInfo));
    }
  }

#ifdef ESP32
  void restoreFrom(File f) {
    f.read(_hashes, sizeof(_hashes));
    f.read((uint8_t *) &_next_idx, sizeof(_next_idx));
    // ACKs are short-lived transport state and are intentionally not persisted.
    memset(_ack_hashes, 0, sizeof(_ack_hashes));
    memset(_ack_valid, 0, sizeof(_ack_valid));
    _next_ack_idx = 0;
    // Recent repeater entries are intentionally not restored across boots.
    // This avoids struct-layout migration issues and keeps stale path quality
    // stats from persisting indefinitely.
    clearRecentRepeaters();
  }
  void saveTo(File f) {
    f.write(_hashes, sizeof(_hashes));
    f.write((const uint8_t *) &_next_idx, sizeof(_next_idx));
  }
#endif

  bool wasSeen(const mesh::Packet* packet) override {
    if (isDedicatedAckPacket(packet)) {
      uint8_t hash[MAX_HASH_SIZE];
      packet->calculatePacketHash(hash);
      if (hasSeenAckHash(hash)) {
        if (packet->isRouteDirect()) {
          _direct_dups++;
        } else {
          _flood_dups++;
        }
        return true;
      }
      return false;
    }

    uint8_t hash[MAX_HASH_SIZE];
    packet->calculatePacketHash(hash);

    if (hasSeenHash(hash)) {
      if (packet->isRouteDirect()) {
        _direct_dups++;   // keep some stats
      } else {
        _flood_dups++;
      }
      return true;
    }

    return false;
  }

  void markSeen(const mesh::Packet* packet) override {
    if (isDedicatedAckPacket(packet)) {
      uint8_t hash[MAX_HASH_SIZE];
      packet->calculatePacketHash(hash);
      storeAckHash(hash);
      return;
    }

    uint8_t hash[MAX_HASH_SIZE];
    packet->calculatePacketHash(hash);
    if (!hasSeenHash(hash)) {
      storeHash(hash);
      recordRecentRepeater(packet);
    }
  }

  void markSent(const mesh::Packet* packet) override {
    if (isDedicatedAckPacket(packet)) {
      uint8_t hash[MAX_HASH_SIZE];
      packet->calculatePacketHash(hash);
      storeAckHash(hash);
      return;
    }

    // Outbound packets must be marked as already-sent without teaching the recent-heard cache about ourselves.
    uint8_t hash[MAX_HASH_SIZE];
    packet->calculatePacketHash(hash);
    if (!hasSeenHash(hash)) {
      storeHash(hash);
    }
  }

  void clear(const mesh::Packet* packet) override {
    if (isDedicatedAckPacket(packet)) {
      uint8_t hash[MAX_HASH_SIZE];
      packet->calculatePacketHash(hash);
      clearAckHash(hash);
      return;
    }

    uint8_t hash[MAX_HASH_SIZE];
    packet->calculatePacketHash(hash);

    uint8_t* sp = _hashes;
    for (int i = 0; i < MAX_PACKET_HASHES; i++, sp += MAX_HASH_SIZE) {
      if (memcmp(hash, sp, MAX_HASH_SIZE) == 0) {
        memset(sp, 0, MAX_HASH_SIZE);
        break;
      }
    }
  }

  uint32_t getNumDirectDups() const { return _direct_dups; }
  uint32_t getNumFloodDups() const { return _flood_dups; }

  bool setRecentRepeater(const uint8_t* prefix, uint8_t prefix_len, int8_t snr_x4,
                         bool snr_locked = false, bool bypass_allow_filter = false) {
    (void)snr_locked;
    (void)bypass_allow_filter;
    if (_max_recent_repeaters == 0) {
      return false;
    }
    if (prefix == NULL || prefix_len == 0) {
      return false;
    }

    if (prefix_len > MAX_ROUTE_HASH_BYTES) {
      prefix_len = MAX_ROUTE_HASH_BYTES;
    }

    int empty_idx = -1;
    int oldest_idx = 0;
#if ARDUINO
    const uint32_t now = millis();
    uint32_t oldest_age = 0;
    bool have_oldest = false;
#endif

    // Find a match, the first empty slot, and the oldest occupied slot in one
    // pass. Keep exact prefixes distinct so a 1-byte path prefix does not
    // collapse independent 2/3-byte repeaters that share the same first byte.
    for (int i = 0; i < _max_recent_repeaters; i++) {
      RecentRepeaterInfo& existing = _recent_repeaters[i];
      if (existing.prefix_len == 0) {
        if (empty_idx < 0) empty_idx = i;
        continue;
      }
      if (existing.prefix_len != prefix_len || memcmp(existing.prefix, prefix, prefix_len) != 0) {
  #if ARDUINO
        uint32_t age = (uint32_t)(now - existing.last_heard_millis);
        if (!have_oldest || age > oldest_age) {
          oldest_age = age;
          oldest_idx = i;
          have_oldest = true;
        }
  #endif
        continue;
      }
      existing.snr_x4 = weightedSnrX4RoundUp(existing.snr_x4, snr_x4);
#if ARDUINO
      existing.last_heard_millis = now;
#else
      existing.last_heard_millis = 0;
#endif
      return true;
    }

    // Non-Arduino tests have no monotonic clock, so a full table retains the
    // historical deterministic fallback of evicting slot zero.
    int slot_idx = empty_idx >= 0 ? empty_idx : oldest_idx;

    RecentRepeaterInfo& slot = _recent_repeaters[slot_idx];
    memset(slot.prefix, 0, sizeof(slot.prefix));
    memcpy(slot.prefix, prefix, prefix_len);
    slot.prefix_len = prefix_len;
    slot.snr_x4 = snr_x4;
#if ARDUINO
    slot.last_heard_millis = now;
#else
    slot.last_heard_millis = 0;
#endif
    return true;
  }
  bool decrementRecentRepeaterSnrX4(const uint8_t* prefix, uint8_t prefix_len, uint8_t amount_x4 = 1) {
    if (_max_recent_repeaters == 0) {
      return false;
    }
    if (prefix == NULL || prefix_len == 0 || amount_x4 == 0) {
      return false;
    }
    if (prefix_len > MAX_ROUTE_HASH_BYTES) {
      prefix_len = MAX_ROUTE_HASH_BYTES;
    }

    RecentRepeaterInfo* existing = const_cast<RecentRepeaterInfo*>(findRecentRepeaterByHash(prefix, prefix_len));
    if (existing != NULL) {
      int16_t lowered = (int16_t)existing->snr_x4 - (int16_t)amount_x4;
      if (lowered < -128) lowered = -128;
      existing->snr_x4 = (int8_t)lowered;
      return true;
    }
    return false;
  }
  int getRecentRepeaterCount() const {
    if (_max_recent_repeaters == 0) {
      return 0;
    }
    int count = 0;
    for (int i = 0; i < _max_recent_repeaters; i++) {
      if (_recent_repeaters[i].prefix_len > 0) {
        count++;
      }
    }
    return count;
  }
  const RecentRepeaterInfo* getRecentRepeaterBySortedIdx(int idx_wanted) const {
    if (_max_recent_repeaters == 0) {
      return NULL;
    }
    if (idx_wanted < 0) {
      return NULL;
    }

    const RecentRepeaterInfo* last = NULL;
    int last_idx = -1;
    for (int rank = 0; rank <= idx_wanted; rank++) {
      const RecentRepeaterInfo* best = NULL;
      int best_idx = -1;
      for (int i = 0; i < _max_recent_repeaters; i++) {
        const RecentRepeaterInfo* info = &_recent_repeaters[i];
        if (info->prefix_len == 0) {
          continue;
        }
        if (last != NULL && !recentRepeaterComesBefore(*last, last_idx, *info, i)) {
          continue;
        }
        if (best == NULL || recentRepeaterComesBefore(*info, i, *best, best_idx)) {
          best = info;
          best_idx = i;
        }
      }
      if (best == NULL) {
        return NULL;
      }
      last = best;
      last_idx = best_idx;
    }
    return last;
  }

  const RecentRepeaterInfo* findRecentRepeaterByHash(const uint8_t* hash, uint8_t hash_len) const {
    if (_max_recent_repeaters == 0) {
      return NULL;
    }
    if (hash == NULL || hash_len == 0) {
      return NULL;
    }

    // Prefer exact matches. If none exists, fall back to the longest overlapping
    // prefix, using highest SNR to break ties.
    const RecentRepeaterInfo* best = NULL;
    for (int i = 0; i < _max_recent_repeaters; i++) {
      const RecentRepeaterInfo* info = &_recent_repeaters[i];
      if (info->prefix_len == 0) {
        continue;
      }
      if (info->prefix_len == hash_len && memcmp(info->prefix, hash, hash_len) == 0) {
        return info;
      }
      if (routeHashPrefixesOverlap(info->prefix, info->prefix_len, hash, hash_len)) {
        if (best == NULL || info->prefix_len > best->prefix_len
            || (info->prefix_len == best->prefix_len && info->snr_x4 > best->snr_x4)) {
          best = info;
        }
      }
    }
    return best;
  }
  void clearRecentRepeaters() {
    if (_max_recent_repeaters > 0) {
      memset(_recent_repeaters, 0, _max_recent_repeaters * sizeof(RecentRepeaterInfo));
    }
  }
  int expireRecentRepeaters(uint32_t now_millis, uint32_t max_age_millis) {
    if (_max_recent_repeaters == 0) {
      return 0;
    }

    int expired = 0;
    for (int i = 0; i < _max_recent_repeaters; i++) {
      RecentRepeaterInfo& info = _recent_repeaters[i];
      if (info.prefix_len > 0
          && (uint32_t)(now_millis - info.last_heard_millis) > max_age_millis) {
        memset(&info, 0, sizeof(info));
        expired++;
      }
    }
    return expired;
  }

  void resetStats() { _direct_dups = _flood_dups = 0; }
};
