#pragma once

#include <stdint.h>
#include <string.h>

namespace mesh { namespace ota {

// Source-side evidence only. No wire fields, allocations, RSSI polling, or
// assumptions that admission to a radio queue means the packet was transmitted.
class OtaPacketPacer {
public:
  enum Format : uint8_t { V2 = 1, Extended = 2, Direct = 4 };
  enum Feedback : uint8_t { Ignored, Suppressed, Duplicate, Loss };
  static constexpr uint8_t HistorySlots = 6;
  static constexpr uint8_t PortableProfile = 0xFF;

  struct Key {
    uint32_t generation = 0;
    uint32_t stream = 0;  // zero for legacy; v2's encoded representation ID
    uint8_t mid[4] = {0};
    uint16_t block = 0;
    uint8_t profile = PortableProfile;
    uint8_t format = 0;
  };

  void reset() {
    for (auto& entry : _history) entry = Entry{};
    for (auto& state : _states) state = State{};
    _next = 0;
    _last = 2;
  }

  // Bind only an accepted, currently valid radio route. Stale TX callbacks
  // never bind a route and therefore cannot resurrect an earlier generation.
  void bind(uint8_t profile, uint32_t generation) {
    const uint8_t slot = slotFor(profile);
    if (slot >= 3) return;
    State& state = _states[slot];
    if (!(state.flags & StateValid) || state.generation != generation) {
      state = State{};
      state.generation = generation;
      state.level = state.stable = 3;
      state.flags = StateValid;
      for (auto& entry : _history)
        if ((entry.flags & Valid) && entry.slot == slot) entry.flags = 0;
    }
    _last = slot;
  }

  float pace(uint8_t profile, uint32_t generation) const {
    const uint8_t slot = activeSlot(profile, generation);
    return slot < 3 ? speed(_states[slot].level) : 1.0f;
  }
  float pace() const {
    return (_states[_last].flags & StateValid) ? speed(_states[_last].level) : 1.0f;
  }
  float pace(uint8_t profile) const {
    const uint8_t slot = slotFor(profile);
    return slot < 3 && (_states[slot].flags & StateValid) ? speed(_states[slot].level) : 1.0f;
  }

  // Call only after successful queue admission. full_mask describes this exact
  // encoded representation, rather than the larger uncompressed block.
  bool queued(const Key& key, uint16_t full_mask, uint16_t fragment_bit) {
    const uint8_t slot = activeSlot(key.profile, key.generation);
    if (slot >= 3 || !singleBit(fragment_bit) || !(full_mask & fragment_bit)) return false;
    Entry* entry = find(key, slot);
    if (entry && entry->full_mask != full_mask) return false;
    if (!entry) {
      uint8_t inherited = 0;
      for (const auto& prior : _history)
        if (matches(prior, slot, key.mid, key.block))
          inherited |= prior.flags & (LossNoted | Consumed);
      entry = allocate(slot);
      if (!entry) return false;  // unknown evidence is never called loss
      *entry = Entry{};
      entry->stream = key.stream;
      entry->mid = manifestWord(key.mid);
      entry->block = key.block;
      entry->full_mask = full_mask;
      entry->slot = slot;
      entry->format = key.format;
      entry->flags = Valid | inherited;
    }
    const uint8_t shift = bitIndex(fragment_bit) * 2;
    const uint8_t count = uint8_t((entry->queued_counts >> shift) & 3u);
    if (count == 3) {
      entry->flags |= TxFailed; // cannot represent another copy: remain conservative
      return false;
    }
    entry->queued_counts += uint32_t(1) << shift;
    return true;
  }

  bool sent(const Key& key, uint16_t fragment_bit, uint32_t now) {
    Entry* entry = queuedEntry(key, fragment_bit);
    if (!entry) return false;
    decrement(*entry, fragment_bit);
    entry->sent_mask |= fragment_bit;
    entry->last_tx = now;
    return true;
  }
  bool failed(const Key& key, uint16_t fragment_bit) {
    Entry* entry = queuedEntry(key, fragment_bit);
    if (!entry) return false;
    decrement(*entry, fragment_bit);
    entry->flags |= TxFailed;
    return true;
  }

  // PROOF is common to every encoding of a logical block. It may credit only
  // representations whose DATA actually finished, not merely queued ones.
  bool proofSent(uint8_t profile, uint32_t generation, const uint8_t mid[4],
                 uint16_t block, uint32_t now) {
    const uint8_t slot = activeSlot(profile, generation);
    if (slot >= 3) return false;
    bool matched = false;
    for (auto& entry : _history) {
      if (matches(entry, slot, mid, block) && dataComplete(entry)) {
        entry.flags |= ProofSent;
        entry.last_tx = now;
        matched = true;
      }
    }
    return matched;
  }
  bool proofFailed(uint8_t profile, uint32_t generation, const uint8_t mid[4], uint16_t block) {
    const uint8_t slot = activeSlot(profile, generation);
    if (slot >= 3) return false;
    bool matched = false;
    for (auto& entry : _history) {
      if (matches(entry, slot, mid, block)) {
        entry.flags |= TxFailed;
        matched = true;
      }
    }
    return matched;
  }

  Feedback request(uint8_t profile, uint32_t generation, const uint8_t mid[4],
                   uint16_t block, uint8_t format, uint16_t want,
                   uint32_t now, uint32_t grace_ms) {
    const uint8_t slot = activeSlot(profile, generation);
    if (slot >= 3 || !want) return Ignored;
    Entry* candidate = nullptr;
    for (auto& entry : _history) {
      if (!matches(entry, slot, mid, block) || entry.format != format) continue;
      // A request does not echo the v2 stream ID. Different recent encodings
      // make its mask ambiguous, so do not attribute loss to either of them.
      if (candidate) return suppress(slot);
      candidate = &entry;
    }
    if (!candidate) return Ignored;
    Entry& entry = *candidate;
    want &= entry.full_mask;
    // Full requests can be delayed duplicates of the original request. They
    // are not evidence of a missing subset, and must not cause a penalty.
    if (!want || want == entry.full_mask || (entry.flags & Consumed)) return Ignored;
    if (entry.flags & LossNoted) return Duplicate;
    if (!dataComplete(entry) || (entry.flags & TxFailed)
        || uint32_t(now - entry.last_tx) < grace_ms) return suppress(slot);
    // An encoding fallback is still the same logical block. One lost block
    // must not receive another penalty solely because its representation changed.
    for (auto& prior : _history)
      if (matches(prior, slot, mid, block)) prior.flags |= LossNoted;
    State& state = _states[slot];
    state.clean = 0;
    if (state.level != state.stable) {
      state.level = state.stable;
    } else {
      state.level = state.level > 1 ? 1 : 0;
      state.stable = state.level;
    }
    return Loss;
  }

  // Invoke once for an accepted all-full forward request window, using its
  // smallest block index. Initial/full duplicates alone never credit recovery.
  bool forwardRequest(uint8_t profile, uint32_t generation, const uint8_t mid[4],
                      uint16_t first_full_block, uint32_t now, uint32_t grace_ms) {
    const uint8_t slot = activeSlot(profile, generation);
    if (slot >= 3) return false;
    bool any = false, clean = true;
    for (const auto& entry : _history) {
      if (!older(entry, slot, mid, first_full_block)) continue;
      any = true;
      // One complete encoding proves this block's response finished. An
      // abandoned encoding must not pin forward recovery forever, but its
      // dirty/incomplete history does prevent calling this a clean sample.
      bool completed = false;
      for (const auto& representation : _history) {
        if (matches(representation, slot, mid, entry.block)
            && dataComplete(representation) && (representation.flags & ProofSent)
            && uint32_t(now - representation.last_tx) >= grace_ms) completed = true;
      }
      if (!completed) return false;
      if (!dataComplete(entry) || !(entry.flags & ProofSent)
          || (entry.flags & (LossNoted | TxFailed))) clean = false;
    }
    if (!any) return false;
    for (auto& entry : _history)
      if (older(entry, slot, mid, first_full_block)) entry.flags |= Consumed;
    State& state = _states[slot];
    if (!clean) { state.clean = 0; return false; }
    state.stable = state.level; // A clean forward flight confirms any active probe.
    if (state.level == 3) { state.clean = 0; return true; }
    if (++state.clean >= 2) {
      state.clean = 0;
      state.level = state.stable + 1;
    }
    return true;
  }

private:
  enum : uint8_t { StateValid = 1 };
  enum : uint8_t { Valid = 1, ProofSent = 2, LossNoted = 4, Consumed = 8, TxFailed = 16 };
  struct State {
    uint32_t generation = 0;
    uint8_t level = 3, stable = 3, clean = 0, flags = 0;
  };
  struct Entry {
    uint32_t stream = 0, last_tx = 0, queued_counts = 0;
    uint32_t mid = 0;
    uint16_t block = 0, full_mask = 0, sent_mask = 0;
    uint8_t slot = 0, format = 0, flags = 0;
  };
  State _states[3] = {};
  Entry _history[HistorySlots] = {};
  uint8_t _next = 0, _last = 2;

  static float speed(uint8_t level) { return float(level + 1) * 0.25f; }
  static uint8_t slotFor(uint8_t profile) { return profile < 2 ? profile : profile == PortableProfile ? 2 : 0xFF; }
  uint8_t activeSlot(uint8_t profile, uint32_t generation) const {
    const uint8_t slot = slotFor(profile);
    return slot < 3 && (_states[slot].flags & StateValid)
        && _states[slot].generation == generation ? slot : 0xFF;
  }
  static bool singleBit(uint16_t bit) { return bit && !(bit & (bit - 1)); }
  static uint8_t bitIndex(uint16_t bit) {
    // Every caller already validated a nonzero single fragment bit.
#if defined(__GNUC__) || defined(__clang__)
    return uint8_t(__builtin_ctz(unsigned(bit)));
#else
    uint8_t index = 0;
    while (bit > 1) { bit >>= 1; ++index; }
    return index;
#endif
  }
  static uint32_t manifestWord(const uint8_t mid[4]) {
    uint32_t value;
    memcpy(&value, mid, sizeof(value));  // Safe for unaligned packet payloads.
    return value;
  }
  static bool matches(const Entry& entry, uint8_t slot, const uint8_t mid[4], uint16_t block) {
    return (entry.flags & Valid) && entry.slot == slot && entry.block == block
        && entry.mid == manifestWord(mid);
  }
  static bool dataComplete(const Entry& entry) {
    return entry.full_mask && entry.sent_mask == entry.full_mask && entry.queued_counts == 0;
  }
  static bool older(const Entry& entry, uint8_t slot, const uint8_t mid[4], uint16_t block) {
    return (entry.flags & Valid) && !(entry.flags & Consumed) && entry.slot == slot
        && entry.block < block && entry.mid == manifestWord(mid);
  }
  Entry* find(const Key& key, uint8_t slot) {
    for (auto& entry : _history)
      if (matches(entry, slot, key.mid, key.block) && entry.format == key.format
          && entry.stream == key.stream) return &entry;
    return nullptr;
  }
  Entry* allocate(uint8_t) {
    for (auto& entry : _history) if (!(entry.flags & Valid)) return &entry;
    for (uint8_t pass = 0; pass < 2; ++pass) {
      for (uint8_t n = 0; n < HistorySlots; ++n) {
        const uint8_t index = uint8_t((_next + n) % HistorySlots);
        Entry& entry = _history[index];
        if (entry.queued_counts || (pass == 0 && !(entry.flags & Consumed))) continue;
        _next = uint8_t((index + 1) % HistorySlots);
        return &entry;
      }
    }
    return nullptr;
  }
  Entry* queuedEntry(const Key& key, uint16_t bit) {
    const uint8_t slot = activeSlot(key.profile, key.generation);
    if (slot >= 3 || !singleBit(bit)) return nullptr;
    Entry* entry = find(key, slot);
    if (!entry || !(entry->full_mask & bit)
        || !((entry->queued_counts >> (bitIndex(bit) * 2)) & 3u)) return nullptr;
    return entry;
  }
  static void decrement(Entry& entry, uint16_t bit) {
    entry.queued_counts -= uint32_t(1) << (bitIndex(bit) * 2);
  }
  static Feedback suppress(uint8_t) {
    return Suppressed;
  }
};

static_assert(sizeof(OtaPacketPacer) <= 288, "OTA pacer must remain a small fixed workspace");

} }
