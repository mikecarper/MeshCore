#pragma once

#include <MeshCore.h>
#include <stddef.h>
#include <stdint.h>
#include <string.h>

namespace mesh {

// Keeps a small bounded history of completed remote CLI replies so a delayed
// exact retry can recover a lost response without executing the command twice.
class RemoteCliReplyCache {
public:
  static constexpr size_t MAX_REPLY_TEXT =
      MAX_PACKET_PAYLOAD - CIPHER_MAC_SIZE - (CIPHER_BLOCK_SIZE - 1) - 5;

  RemoteCliReplyCache() { clear(); }

#if defined(STM32_PLATFORM)
  static constexpr size_t ENTRY_COUNT = 2;
#else
  static constexpr size_t ENTRY_COUNT = 4;
#endif

  static uint32_t fingerprint(const char* command, size_t command_len) {
    if (command == NULL) return 0;

    // FNV-1a is only a compact request-mismatch guard. Authentication and the
    // sender/timestamp tuple remain the replay-protection boundary.
    uint32_t value = 2166136261UL;
    for (size_t i = 0; i < command_len; i++) {
      value ^= (uint8_t)command[i];
      value *= 16777619UL;
    }
    return value;
  }

  bool remember(const uint8_t* sender_pub_key, uint32_t request_timestamp,
                uint32_t command_fingerprint, const char* response) {
    if (sender_pub_key == NULL || response == NULL) return false;

    Entry* target = NULL;
    for (size_t i = 0; i < ENTRY_COUNT; ++i) {
      if (entryMatches(entries_[i], sender_pub_key, request_timestamp,
                       command_fingerprint)) {
        target = &entries_[i];
        latest_entry_ = (uint8_t)i;
        break;
      }
    }
    if (target == NULL) {
      target = &entries_[next_entry_];
      latest_entry_ = next_entry_;
      next_entry_ = (uint8_t)((next_entry_ + 1) % ENTRY_COUNT);
    }

    memcpy(target->sender_pub_key, sender_pub_key,
           sizeof(target->sender_pub_key));
    target->request_timestamp = request_timestamp;
    target->command_fingerprint = command_fingerprint;

    size_t response_len = 0;
    while (response_len < MAX_REPLY_TEXT && response[response_len] != 0) {
      response_len++;
    }
    memcpy(target->response, response, response_len);
    target->response[response_len] = 0;
    target->valid = true;
    return true;
  }

  bool lookup(const uint8_t* sender_pub_key, uint32_t request_timestamp,
              uint32_t command_fingerprint,
              const char** response = NULL) const {
    for (size_t i = 0; i < ENTRY_COUNT; ++i) {
      if (!entryMatches(entries_[i], sender_pub_key, request_timestamp,
                        command_fingerprint)) {
        continue;
      }
      if (response != NULL) *response = entries_[i].response;
      return true;
    }
    return false;
  }

  bool matches(const uint8_t* sender_pub_key, uint32_t request_timestamp,
               uint32_t command_fingerprint) const {
    return lookup(sender_pub_key, request_timestamp, command_fingerprint);
  }

  const char* response() const {
    return latest_entry_ < ENTRY_COUNT && entries_[latest_entry_].valid
        ? entries_[latest_entry_].response : "";
  }
  bool hasResponse() const { return isValid() && response()[0] != 0; }
  bool isValid() const {
    return latest_entry_ < ENTRY_COUNT && entries_[latest_entry_].valid;
  }

  void clear() {
    memset(entries_, 0, sizeof(entries_));
    next_entry_ = 0;
    latest_entry_ = (uint8_t)ENTRY_COUNT;
  }

private:
  struct Entry {
    bool valid;
    uint8_t sender_pub_key[PUB_KEY_SIZE];
    uint32_t request_timestamp;
    uint32_t command_fingerprint;
    char response[MAX_REPLY_TEXT + 1];
  };

  static bool entryMatches(const Entry& entry,
                           const uint8_t* sender_pub_key,
                           uint32_t request_timestamp,
                           uint32_t command_fingerprint) {
    return entry.valid && sender_pub_key != NULL
        && entry.request_timestamp == request_timestamp
        && entry.command_fingerprint == command_fingerprint
        && memcmp(entry.sender_pub_key, sender_pub_key,
                  sizeof(entry.sender_pub_key)) == 0;
  }

  Entry entries_[ENTRY_COUNT];
  uint8_t next_entry_;
  uint8_t latest_entry_;
};

} // namespace mesh
