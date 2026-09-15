#pragma once

#include <MeshCore.h>
#include "RemoteCliReplyCache.h"
#include <stddef.h>
#include <stdint.h>
#include <string.h>

namespace mesh {

// A single-entry mailbox for authenticated remote CLI commands. The repeater
// drains it immediately after Mesh::loop() returns; a second command arriving
// in the same pass receives a busy response. Keeping the command in object
// storage lets the receive/decrypt call chain unwind before command handlers
// perform filesystem or cryptographic work.
struct DeferredCliCommand {
  bool pending;
  // ACL entries can move or be reused while a request is pending or executing.
  // Bind its owner to the complete authenticated key, never an array slot.
  uint8_t client_pub_key[PUB_KEY_SIZE];
  uint32_t sender_timestamp;
  uint32_t request_id;
  // Command handlers may normalize or tokenize command[] in place. Keep the
  // authenticated wire identity separately so retries find the stored result.
  uint32_t command_fingerprint;
  uint8_t path_hash_size;
  uint8_t radio_profile = 0;
  uint32_t radio_generation = 0;
  uint8_t secret[PUB_KEY_SIZE];
  char command[MAX_PACKET_PAYLOAD + 1];

  DeferredCliCommand()
      : pending(false), sender_timestamp(0), request_id(0),
        command_fingerprint(0), path_hash_size(1) {
    memset(client_pub_key, 0, sizeof(client_pub_key));
    memset(secret, 0, sizeof(secret));
    command[0] = 0;
  }

  bool enqueue(const uint8_t* new_client_pub_key, uint32_t new_sender_timestamp,
               uint8_t new_path_hash_size, const uint8_t* new_secret,
               const char* new_command, size_t command_len,
               uint32_t new_request_id = 0, uint8_t new_radio_profile = 0,
               uint32_t new_radio_generation = 0) {
    if (pending || new_client_pub_key == NULL || new_secret == NULL || new_command == NULL
        || command_len >= sizeof(command)) {
      return false;
    }

    memcpy(client_pub_key, new_client_pub_key, sizeof(client_pub_key));
    sender_timestamp = new_sender_timestamp;
    request_id = new_request_id != 0
        ? new_request_id : new_sender_timestamp;
    command_fingerprint = RemoteCliReplyCache::fingerprint(new_command, command_len);
    path_hash_size = new_path_hash_size;
    radio_profile = new_radio_profile;
    radio_generation = new_radio_generation;
    memcpy(secret, new_secret, sizeof(secret));
    memcpy(command, new_command, command_len);
    command[command_len] = 0;
    pending = true;
    return true;
  }

  bool matches(const uint8_t* other_client_pub_key, uint32_t other_request_id,
               const char* other_command, size_t other_command_len) const {
    return pending && other_client_pub_key != NULL && other_command != NULL
        && memcmp(client_pub_key, other_client_pub_key, sizeof(client_pub_key)) == 0
        && request_id == other_request_id
        && other_command_len < sizeof(command)
        && command[other_command_len] == 0
        && memcmp(command, other_command, other_command_len) == 0;
  }

  template <typename ACL> int findClientIndex(const ACL& acl) const {
    if (!pending) return -1;
    for (int i = 0; i < acl.getNumClients(); ++i) {
      if (memcmp(client_pub_key, acl.getClientByIdx(i)->id.pub_key,
                 sizeof(client_pub_key)) == 0) return i;
    }
    return -1;
  }

  void clear() {
    pending = false;
    memset(client_pub_key, 0, sizeof(client_pub_key));
    sender_timestamp = 0;
    request_id = 0;
    command_fingerprint = 0;
    path_hash_size = 1;
    radio_profile = 0;
    radio_generation = 0;
    memset(secret, 0, sizeof(secret));
    memset(command, 0, sizeof(command));
  }
};

} // namespace mesh
