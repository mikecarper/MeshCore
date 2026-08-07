#pragma once

#include <MeshCore.h>
#include <stddef.h>
#include <stdint.h>
#include <string.h>

namespace mesh {

// Remembers a bounded set of logical messages independently of their on-air
// attempt byte. The fingerprint supplied by the caller must already identify
// the peer and message text.
template <size_t ENTRY_COUNT>
class LogicalMessageCache {
public:
  enum class ReplayDecision : uint8_t {
    NewMessage,
    ExactRetry,
    StaleOrMismatched
  };

  LogicalMessageCache() { clear(); }

  bool find(const uint8_t fingerprint[MAX_HASH_SIZE], uint32_t source_timestamp,
            uint32_t* mapped_timestamp = NULL) const {
    if (fingerprint == NULL) return false;

    for (size_t i = 0; i < ENTRY_COUNT; i++) {
      const Entry& entry = entries_[i];
      if (entry.valid && entry.source_timestamp == source_timestamp
          && memcmp(entry.fingerprint, fingerprint, MAX_HASH_SIZE) == 0) {
        if (mapped_timestamp != NULL) {
          *mapped_timestamp = entry.mapped_timestamp;
        }
        return true;
      }
    }
    return false;
  }

  bool remember(const uint8_t fingerprint[MAX_HASH_SIZE], uint32_t source_timestamp,
                uint32_t mapped_timestamp = 0) {
    if (fingerprint == NULL) return false;

    for (size_t i = 0; i < ENTRY_COUNT; i++) {
      Entry& entry = entries_[i];
      if (entry.valid && entry.source_timestamp == source_timestamp
          && memcmp(entry.fingerprint, fingerprint, MAX_HASH_SIZE) == 0) {
        entry.mapped_timestamp = mapped_timestamp;
        return true;
      }
    }

    Entry& entry = entries_[next_entry_];
    memcpy(entry.fingerprint, fingerprint, MAX_HASH_SIZE);
    entry.source_timestamp = source_timestamp;
    entry.mapped_timestamp = mapped_timestamp;
    entry.valid = true;
    next_entry_ = (next_entry_ + 1) % ENTRY_COUNT;
    return true;
  }

  ReplayDecision classifyAndRemember(
      const uint8_t fingerprint[MAX_HASH_SIZE], uint32_t source_timestamp,
      uint32_t& latest_source_timestamp) {
    if (fingerprint == NULL) return ReplayDecision::StaleOrMismatched;
    if (find(fingerprint, source_timestamp)) {
      return ReplayDecision::ExactRetry;
    }
    if (source_timestamp <= latest_source_timestamp) {
      return ReplayDecision::StaleOrMismatched;
    }

    latest_source_timestamp = source_timestamp;
    remember(fingerprint, source_timestamp);
    return ReplayDecision::NewMessage;
  }

  void clear() {
    memset(entries_, 0, sizeof(entries_));
    next_entry_ = 0;
  }

private:
  static_assert(ENTRY_COUNT > 0, "LogicalMessageCache needs at least one entry");

  struct Entry {
    uint8_t fingerprint[MAX_HASH_SIZE];
    uint32_t source_timestamp;
    uint32_t mapped_timestamp;
    bool valid;
  };

  Entry entries_[ENTRY_COUNT];
  size_t next_entry_;
};

} // namespace mesh
