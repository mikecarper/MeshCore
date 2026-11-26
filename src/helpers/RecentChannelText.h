#pragma once

#include <stddef.h>
#include <stdint.h>
#include <string.h>

namespace mesh {

struct ChannelTextDedupSettings {
  static constexpr size_t SlotCount = 64;
  bool global_enabled = true;
  // Zero bits represent enabled slots, including on older installations.
  uint8_t disabled_slots[SlotCount / 8] = {};

  bool channelEnabled(size_t slot) const {
    return (disabled_slots[slot / 8] & (1U << (slot % 8))) == 0;
  }

  void setChannelEnabled(size_t slot, bool enabled) {
    auto& flags = disabled_slots[slot / 8];
    const uint8_t mask = 1U << (slot % 8);
    if (enabled) flags &= uint8_t(~mask);
    else flags |= mask;
  }
};

// Display history lives separately from the app delivery queue so draining the
// queue does not forget what the user already saw.
template <size_t ChannelCount, size_t ExternalCount = 0>
class RecentChannelText {
  static_assert(ChannelCount <= ChannelTextDedupSettings::SlotCount, "channel mask too small");
  static_assert(ExternalCount < ChannelCount, "leave some channel history in ordinary RAM");

 public:
  static constexpr size_t FingerprintSize = 8;
  static constexpr uint32_t WindowMs = 15UL * 60 * 1000;

  ChannelTextDedupSettings settings;

  bool enabled(size_t index) const {
    return settings.global_enabled && settings.channelEnabled(index);
  }

  bool suppress(size_t index, const uint8_t* fingerprint, uint32_t now) {
    auto& channel = channelAt(index);
    for (uint8_t i = 0; i < Depth; ++i) {
      auto& entry = channel.entries[i];
      if ((channel.valid & (1U << i)) && now - entry.last_seen < WindowMs
          && memcmp(entry.fingerprint, fingerprint, FingerprintSize) == 0) {
        entry.last_seen = now;
        return true;
      }
    }
    return false;
  }

  void remember(size_t index, const uint8_t* fingerprint, uint32_t now) {
    auto& channel = channelAt(index);
    auto& entry = channel.entries[channel.next];
    memcpy(entry.fingerprint, fingerprint, FingerprintSize);
    entry.last_seen = now;
    channel.valid |= uint8_t(1U << channel.next);
    channel.next = (channel.next + 1) % Depth;
  }

  void clear(size_t index) {
    auto& channel = channelAt(index);
    channel.valid = channel.next = 0;
  }

  void clearAll() {
    for (size_t index = 0; index < ChannelCount; ++index) clear(index);
  }

  void expire(uint32_t now) {
    if (now - last_sweep_ < WindowMs) return;
    last_sweep_ = now;
    // Clear stale entries even on quiet channels before the millisecond clock
    // can wrap all the way back to their original timestamps.
    for (size_t index = 0; index < ChannelCount; ++index) {
      auto& channel = channelAt(index);
      for (uint8_t i = 0; i < Depth; ++i) {
        if (now - channel.entries[i].last_seen >= WindowMs)
          channel.valid &= uint8_t(~(1U << i));
      }
    }
  }

 private:
  static constexpr uint8_t Depth = 6;
  struct Entry {
    uint32_t last_seen;
    uint8_t fingerprint[FingerprintSize];
  };
  struct Channel {
    Entry entries[Depth];
    uint8_t valid;
    uint8_t next;
  };
  // Only targets that select an external bank define this symbol.
  static Channel external_channels_[ExternalCount ? ExternalCount : 1];
  Channel channels_[ChannelCount - ExternalCount] = {};
  uint32_t last_sweep_ = 0;

  Channel& channelAt(size_t index) {
    if (index < ExternalCount) return external_channels_[index];
    return channels_[index - ExternalCount];
  }
};

}  // namespace mesh
