#pragma once

#include <MeshCore.h>
#include <stddef.h>
#include <stdint.h>
#include <string.h>

namespace mesh {

// Keeps the most recently completed remote CLI reply so an exact request
// retry can recover a lost response without executing the command twice.
// This is deliberately a single bounded entry: remote CLI commands are
// serialized, and repeater builds on small MCUs cannot afford one reply-sized
// buffer for every ACL client.
class RemoteCliReplyCache {
public:
  static constexpr size_t MAX_REPLY_TEXT =
      MAX_PACKET_PAYLOAD - CIPHER_MAC_SIZE - (CIPHER_BLOCK_SIZE - 1) - 5;

  RemoteCliReplyCache() { clear(); }

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

    memcpy(sender_pub_key_, sender_pub_key, sizeof(sender_pub_key_));
    request_timestamp_ = request_timestamp;
    command_fingerprint_ = command_fingerprint;

    size_t response_len = 0;
    while (response_len < MAX_REPLY_TEXT && response[response_len] != 0) {
      response_len++;
    }
    memcpy(response_, response, response_len);
    response_[response_len] = 0;
    valid_ = true;
    return true;
  }

  bool matches(const uint8_t* sender_pub_key, uint32_t request_timestamp,
               uint32_t command_fingerprint) const {
    return valid_ && sender_pub_key != NULL
        && request_timestamp_ == request_timestamp
        && command_fingerprint_ == command_fingerprint
        && memcmp(sender_pub_key_, sender_pub_key, sizeof(sender_pub_key_)) == 0;
  }

  const char* response() const { return response_; }
  bool hasResponse() const { return valid_ && response_[0] != 0; }
  bool isValid() const { return valid_; }

  void clear() {
    valid_ = false;
    memset(sender_pub_key_, 0, sizeof(sender_pub_key_));
    request_timestamp_ = 0;
    command_fingerprint_ = 0;
    memset(response_, 0, sizeof(response_));
  }

private:
  bool valid_;
  uint8_t sender_pub_key_[PUB_KEY_SIZE];
  uint32_t request_timestamp_;
  uint32_t command_fingerprint_;
  char response_[MAX_REPLY_TEXT + 1];
};

} // namespace mesh
