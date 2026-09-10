#pragma once

#include <MeshCore.h>
#include <string.h>

#ifndef FLOOD_ADVERT_SOURCE_SLOTS
  #if defined(STM32_PLATFORM)
    #define FLOOD_ADVERT_SOURCE_SLOTS 8
  #elif defined(NRF52_PLATFORM)
    // Preserve the runtime heap reserve alongside the fixed 64 KiB mOTA arena.
    #define FLOOD_ADVERT_SOURCE_SLOTS 96
  #else
    #define FLOOD_ADVERT_SOURCE_SLOTS 128
  #endif
#endif

namespace mesh {

// Volatile, bounded receive history. Only forwarding roles own this table.
// All times are uint32_t uptime milliseconds, never advert timestamps or RTC.
class FloodAdvertLimiter {
public:
  static constexpr uint32_t WINDOW_MS = 180UL * 60UL * 1000UL;
  static constexpr uint32_t BAD_INTERVAL_MS = 12UL * 60UL * 60UL * 1000UL;
  static constexpr uint32_t RECOVERY_MS = 7UL * 24UL * 60UL * 60UL * 1000UL;
  static constexpr uint8_t HASH_SLOTS = 11; // maximum normal allowance + evidence of excess
  static constexpr uint8_t PREFIX_BYTES = 6; // first 12 public-key hex characters

  enum class Decision : uint8_t { Allow, Duplicate, Quota, BadList, Capacity };

  struct Entry {
    uint8_t key[PUB_KEY_SIZE];
    uint8_t hashes[HASH_SLOTS][MAX_HASH_SIZE];
    uint32_t window_start;
    uint32_t last_violation;
    uint32_t last_forwarded;
    uint32_t last_heard;
    uint16_t forwarded;
    uint8_t min_hops;
    uint8_t count;
    uint8_t flags;
  };

  void reset();
  void clear(const uint8_t* full_key);
  void tick(uint32_t now);
  static uint8_t allowance(uint8_t hops);
  bool needsShorterPath(const uint8_t* key, uint8_t hops, uint32_t now);
  void noteKnownCopy(const uint8_t* key, const uint8_t* hash, uint32_t now);
  // Call ONLY after signature validation. Previously seen copies may improve
  // the minimum path, but cannot add evidence or consume forwarding quota.
  void observe(const uint8_t* key, const uint8_t* hash, uint8_t hops,
               uint32_t now, bool previously_seen = false);
  Decision check(const uint8_t* key, const uint8_t* hash, uint32_t now);
  void commit(const uint8_t* key, const uint8_t* hash, uint32_t now);
  bool isBad(const uint8_t* key, uint32_t now);

protected:
  FloodAdvertLimiter(Entry* entries, size_t capacity) : _entries(entries), _capacity(capacity) {}
  FloodAdvertLimiter(const FloodAdvertLimiter&) = delete;
  FloodAdvertLimiter& operator=(const FloodAdvertLimiter&) = delete;

private:
  enum : uint8_t { ACTIVE = 1, PREVIOUS_OVER = 2, BAD = 4, HAS_FORWARDED = 8 };
  Entry* _entries;
  size_t _capacity;
  uint32_t _last_sweep = 0;
  Entry* find(const uint8_t* key);
  static int hashIndex(const Entry& entry, const uint8_t* hash);
  static void advance(Entry& entry, uint32_t now);
  void refresh(uint32_t now);
};

template<size_t Capacity = FLOOD_ADVERT_SOURCE_SLOTS>
class StaticFloodAdvertLimiter : public FloodAdvertLimiter {
  static_assert(Capacity > 0, "Advert limiter needs at least one source slot");
  Entry _storage[Capacity];
public:
  StaticFloodAdvertLimiter() : FloodAdvertLimiter(_storage, Capacity) { reset(); }
};

} // namespace mesh
