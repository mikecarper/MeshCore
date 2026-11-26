#pragma once

#include <stddef.h>
#include <stdint.h>
#include <string.h>

namespace mesh {

// Display history lives separately from the app delivery queue so draining the
// queue does not forget what the user already saw.
template <size_t ChannelCount>
class RecentChannelText {
 public:
  static constexpr size_t FingerprintSize = 8;
  static constexpr uint32_t WindowMs = 15UL * 60 * 1000;

  bool contains(size_t index, const uint8_t* fingerprint, uint32_t now) const {
    const auto& channel = channels_[index];
    for (uint8_t i = 0; i < Depth; ++i) {
      const auto& entry = channel.entries[i];
      if ((channel.valid & (1U << i)) && now - entry.shown_at < WindowMs
          && memcmp(entry.fingerprint, fingerprint, FingerprintSize) == 0) return true;
    }
    return false;
  }

  void remember(size_t index, const uint8_t* fingerprint, uint32_t now) {
    auto& channel = channels_[index];
    auto& entry = channel.entries[channel.next];
    memcpy(entry.fingerprint, fingerprint, FingerprintSize);
    entry.shown_at = now;
    channel.valid |= uint8_t(1U << channel.next);
    channel.next = (channel.next + 1) % Depth;
  }

  void clear(size_t index) {
    channels_[index].valid = channels_[index].next = 0;
  }

  void expire(uint32_t now) {
    if (now - last_sweep_ < WindowMs) return;
    last_sweep_ = now;
    // Clear stale entries even on quiet channels before the millisecond clock
    // can wrap all the way back to their original timestamps.
    for (auto& channel : channels_) {
      for (uint8_t i = 0; i < Depth; ++i) {
        if (now - channel.entries[i].shown_at >= WindowMs)
          channel.valid &= uint8_t(~(1U << i));
      }
    }
  }

 private:
  static constexpr uint8_t Depth = 6;
  struct Entry {
    uint32_t shown_at;
    uint8_t fingerprint[FingerprintSize];
  };
  struct Channel {
    Entry entries[Depth];
    uint8_t valid;
    uint8_t next;
  };
  Channel channels_[ChannelCount] = {};
  uint32_t last_sweep_ = 0;
};

}  // namespace mesh
