#include "FloodAdvertLimiter.h"

namespace mesh {

uint8_t FloodAdvertLimiter::allowance(uint8_t hops) {
  static const uint8_t limits[] = {10, 10, 9, 8, 6, 5, 4, 3, 2};
  return limits[hops < 8 ? hops : 8];
}

void FloodAdvertLimiter::reset() {
  for (size_t i = 0; i < _capacity; ++i) _entries[i].flags = 0;
  _last_sweep = 0;
}

FloodAdvertLimiter::Entry* FloodAdvertLimiter::find(const uint8_t* key) {
  for (size_t i = 0; i < _capacity; ++i) {
    if ((_entries[i].flags & ACTIVE) && memcmp(_entries[i].key, key, PUB_KEY_SIZE) == 0) {
      return &_entries[i];
    }
  }
  return nullptr;
}

void FloodAdvertLimiter::clear(const uint8_t* full_key) {
  Entry* entry = find(full_key);
  if (entry) entry->flags = 0;
}

int FloodAdvertLimiter::hashIndex(const Entry& entry, const uint8_t* hash) {
  for (uint8_t i = 0; i < entry.count; ++i) {
    if (memcmp(entry.hashes[i], hash, MAX_HASH_SIZE) == 0) return i;
  }
  return -1;
}

void FloodAdvertLimiter::advance(Entry& entry, uint32_t now) {
  if (!(entry.flags & ACTIVE)) return;
  if ((entry.flags & HAS_FORWARDED) && uint32_t(now - entry.last_forwarded) >= BAD_INTERVAL_MS) {
    entry.flags &= ~HAS_FORWARDED;
  }
  uint32_t elapsed = now - entry.window_start;
  if (elapsed >= WINDOW_MS) {
    const bool over = entry.count > allowance(entry.min_hops);
    if (over) {
      // Recovery starts after the last over-limit window, not after the last
      // forwarded packet. Suppressed RX traffic still counts as abuse.
      entry.last_violation = entry.window_start + WINDOW_MS;
    }
    entry.flags &= ~PREVIOUS_OVER;
    if (over && elapsed < 2 * WINDOW_MS) entry.flags |= PREVIOUS_OVER;
    entry.window_start += (elapsed / WINDOW_MS) * WINDOW_MS;
    entry.count = 0;
    entry.forwarded = 0;
    entry.min_hops = 255;
    // Quiet ordinary sources need no history beyond their three-hour window.
    // Keep a first strike through the next window; never evict a bad entry.
    if (!(entry.flags & (BAD | PREVIOUS_OVER))) entry.flags = 0;
  }
  if ((entry.flags & BAD) && uint32_t(now - entry.last_violation) >= RECOVERY_MS) {
    entry.flags &= ~BAD;
    if (entry.count == 0 && !(entry.flags & PREVIOUS_OVER)) entry.flags = 0;
  }
}

void FloodAdvertLimiter::refresh(uint32_t now) {
  for (size_t i = 0; i < _capacity; ++i) advance(_entries[i], now);
  _last_sweep = now;
}

void FloodAdvertLimiter::tick(uint32_t now) {
  // Periodic expiry prevents stale history resurrecting after a millis wrap,
  // even when no adverts arrive for weeks. Receive operations also expire it
  // immediately at the exact window/recovery boundary.
  if (uint32_t(now - _last_sweep) >= 60000UL) refresh(now);
}

bool FloodAdvertLimiter::needsShorterPath(const uint8_t* key, uint8_t hops, uint32_t now) {
  refresh(now);
  Entry* entry = find(key);
  return entry && hops < entry->min_hops;
}

void FloodAdvertLimiter::noteKnownCopy(const uint8_t* key, const uint8_t* hash, uint32_t now) {
  refresh(now);
  Entry* entry = find(key);
  // Only the exact retained hash of a previously verified payload can refresh
  // last-heard without another signature check. Never trust just the key/path.
  if (entry && hashIndex(*entry, hash) >= 0) entry->last_heard = now;
}

void FloodAdvertLimiter::observe(const uint8_t* key, const uint8_t* hash, uint8_t hops,
                                 uint32_t now, bool previously_seen) {
  refresh(now);
  Entry* entry = find(key);
  if (!entry) {
    if (previously_seen) return;
    uint32_t start = now;
    // Keys sharing the first 12 hex characters share a normal quota AND its epoch.
    for (size_t i = 0; i < _capacity; ++i) {
      if ((_entries[i].flags & ACTIVE) && memcmp(_entries[i].key, key, PREFIX_BYTES) == 0) {
        start = _entries[i].window_start;
        if (_entries[i].min_hops < hops) hops = _entries[i].min_hops;
        break;
      }
    }
    Entry* oldest = nullptr;
    for (size_t i = 0; i < _capacity; ++i) {
      if (!(_entries[i].flags & ACTIVE)) {
        entry = &_entries[i];
        break;
      }
      // Preserve both bad-list entries and first-strike evidence. Evict only
      // normal entries, least recently heard first, using rollover-safe ages.
      const Entry& candidate = _entries[i];
      if (!(candidate.flags & (BAD | PREVIOUS_OVER))
          && candidate.count <= allowance(candidate.min_hops)
          && (!oldest || uint32_t(now - candidate.last_heard) > uint32_t(now - oldest->last_heard))) {
        oldest = &_entries[i];
      }
    }
    if (!entry) entry = oldest;
    if (!entry) return; // all slots protect abuse history: fail closed
    memset(entry, 0, sizeof(*entry));
    memcpy(entry->key, key, PUB_KEY_SIZE);
    entry->window_start = start;
    entry->min_hops = hops;
    entry->flags = ACTIVE;
  }
  entry->last_heard = now;
  // The normal level belongs to the prefix, including its shortest RX path.
  // Keep that minimum on every colliding full key for consistent window-close
  // abuse accounting, but never combine their received counts into a strike.
  if (hops < entry->min_hops) entry->min_hops = hops;
  for (size_t i = 0; i < _capacity; ++i) {
    if ((_entries[i].flags & ACTIVE) && memcmp(_entries[i].key, key, PREFIX_BYTES) == 0
        && hops < _entries[i].min_hops) _entries[i].min_hops = hops;
  }
  if (previously_seen || hashIndex(*entry, hash) >= 0 || entry->count == HASH_SLOTS) return;
  memcpy(entry->hashes[entry->count++], hash, MAX_HASH_SIZE);
  if (entry->count > allowance(entry->min_hops)) {
    entry->last_violation = now;
    if (entry->flags & PREVIOUS_OVER) entry->flags |= BAD;
  }
}

FloodAdvertLimiter::Decision FloodAdvertLimiter::check(const uint8_t* key, const uint8_t* hash,
                                                      uint32_t now) {
  refresh(now);
  Entry* entry = find(key);
  if (!entry) return Decision::Capacity;
  int idx = hashIndex(*entry, hash);
  if (idx < 0) return Decision::Quota; // saturated receive history is fail-closed
  if (entry->forwarded & (1U << idx)) return Decision::Duplicate;
  if ((entry->flags & (BAD | HAS_FORWARDED)) == (BAD | HAS_FORWARDED)
      && uint32_t(now - entry->last_forwarded) < BAD_INTERVAL_MS) return Decision::BadList;

  uint8_t hops = entry->min_hops;
  unsigned forwarded = 0;
  for (size_t i = 0; i < _capacity; ++i) {
    const Entry& other = _entries[i];
    if (!(other.flags & ACTIVE) || memcmp(other.key, key, PREFIX_BYTES) != 0) continue;
    if (other.min_hops < hops) hops = other.min_hops;
    for (uint16_t bits = other.forwarded; bits; bits &= bits - 1) ++forwarded;
  }
  return forwarded < allowance(hops) ? Decision::Allow : Decision::Quota;
}

void FloodAdvertLimiter::commit(const uint8_t* key, const uint8_t* hash, uint32_t now) {
  // Caller has passed check() and ALL other forwarding gates, before appending
  // its own path hop. Retries of this same advert are not new distinct adverts.
  Entry* entry = find(key);
  if (!entry) return;
  int idx = hashIndex(*entry, hash);
  if (idx < 0) return;
  entry->forwarded |= uint16_t(1U << idx);
  entry->last_forwarded = now;
  entry->flags |= HAS_FORWARDED;
}

bool FloodAdvertLimiter::isBad(const uint8_t* key, uint32_t now) {
  refresh(now);
  Entry* entry = find(key);
  return entry && (entry->flags & BAD);
}

} // namespace mesh
