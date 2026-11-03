#pragma once

#include <stddef.h>
#include <stdint.h>
#include <string.h>
#include "HomeBeacon.h"
#include "TrackerEscalation.h"

namespace mesh { namespace tracker {

// Main-loop-owned verifier for several independent home beacons. Radio event
// handlers must queue raw reports and let the main loop call observe().
// Storage, BLE scanning and trusted-clock establishment belong to the caller.
template <size_t Capacity>
class HomeBeaconSet {
  static_assert(Capacity > 0, "at least one beacon slot is required");
  struct Entry {
    uint8_t id[4];
    uint8_t key[HomeBeacon::kKeyMaxBytes];
    size_t key_len;
    Entry() : id{}, key{}, key_len(0) {}
  };
  Entry entries_[Capacity];
  HomeBeacon::Hmac hmac_;
  uint64_t last_time_ = 0, present_until_ = 0;
  bool have_time_ = false, rollback_ = false;

  bool observeTime(uint64_t now, bool trusted) {
    if (!trusted || now < HomeBeacon::kEarliestUnix ||
        now / HomeBeacon::kSlotSeconds > UINT32_MAX || rollback_) {
      present_until_ = 0;
      return false;
    }
    if (have_time_ && now < last_time_) {
      rollback_ = true;
      present_until_ = 0;
      return false;
    }
    have_time_ = true;
    last_time_ = now;
    if (now >= present_until_) present_until_ = 0;
    return true;
  }

 public:
  explicit HomeBeaconSet(HomeBeacon::Hmac hmac) : hmac_(hmac) {}

  // IDs must be unique. Reject duplicate secrets too, so revoking one beacon
  // does not inadvertently leave an identical key trusted on another ID.
  bool add(const uint8_t id[4], const uint8_t* key, size_t key_len) {
    if (!id || !key || !hmac_ || (key_len != 16 && key_len != 32)) return false;
    uint8_t any = 0;
    for (size_t i = 0; i < key_len; ++i) any |= key[i];
    if (!any) return false;
    size_t vacant = Capacity;
    for (size_t i = 0; i < Capacity; ++i) {
      const Entry& e = entries_[i];
      if (!e.key_len) { if (vacant == Capacity) vacant = i; continue; }
      if (!memcmp(e.id, id, 4) || (e.key_len == key_len && !memcmp(e.key, key, key_len))) return false;
    }
    if (vacant == Capacity) return false;
    Entry& e = entries_[vacant];
    memcpy(e.id, id, 4);
    memcpy(e.key, key, key_len);
    e.key_len = key_len;
    return true;
  }

  bool remove(const uint8_t id[4]) {
    if (!id) return false;
    for (size_t i = 0; i < Capacity; ++i) {
      Entry& e = entries_[i];
      if (e.key_len && !memcmp(e.id, id, 4)) {
        memset(e.key, 0, sizeof(e.key));
        memset(e.id, 0, sizeof(e.id));
        e.key_len = 0;
        // Cached presence may have come from the removed device.
        present_until_ = 0;
        return true;
      }
    }
    return false;
  }

  // Accept one manufacturer payload: little-endian experimental company ID
  // 0xffff followed by the complete authenticated wire packet.
  bool observe(const uint8_t* payload, size_t len, uint64_t now, bool trusted) {
    if (!observeTime(now, trusted) || !payload || len != HomeBeacon::kSize + 2 ||
        payload[0] != 0xff || payload[1] != 0xff) return false;
    const uint8_t* packet = payload + 2;
    for (size_t i = 0; i < Capacity; ++i) {
      Entry& e = entries_[i];
      if (!e.key_len || memcmp(packet + 1, e.id, 4)) continue;
      HomeBeacon verifier(e.id, e.key, e.key_len, hmac_);
      uint64_t expires = 0;
      if (!verifier.verify(packet, HomeBeacon::kSize, now, true, expires)) return false;
      if (expires > present_until_) present_until_ = expires;
      return true;
    }
    return false;
  }

  HomePresence presence(uint64_t now, bool trusted) {
    return observeTime(now, trusted) && present_until_ > now
        ? HomePresence::Present : HomePresence::Unknown;
  }
  bool clockRollback() const { return rollback_; }

  // The caller may reset this only after explicitly re-establishing time.
  void resetClockTrust() {
    have_time_ = false; rollback_ = false; last_time_ = 0; present_until_ = 0;
  }
};

}} // namespace mesh::tracker
