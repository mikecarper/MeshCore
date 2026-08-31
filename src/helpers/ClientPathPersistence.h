#pragma once

#include <stddef.h>
#include <stdint.h>
#include <string.h>

namespace mesh {

struct ClientPathUpdateResult {
  bool changed;
  bool persistence_needed;
};

struct StoredClientPathView {
  uint8_t encoded_path_len;
  const uint8_t* path;
};

inline bool clientPathPersistenceAllowed(bool client_is_persistable,
                                         bool replay_freshness_proven) {
  return client_is_persistable && replay_freshness_proven;
}

inline uint8_t storedClientPathLength(bool path_is_persistable,
                                      uint8_t current_path_len,
                                      uint8_t unknown_path) {
  return path_is_persistable ? current_path_len : unknown_path;
}

// A replay-unproven PATH may replace the route used for the remainder of this
// boot, but it must not erase an earlier operator-selected route when an
// unrelated ACL mutation rewrites /s_contacts.  Select the live route only
// when it owns durable state; otherwise retain the already-published route (or
// serialize unknown for a contact that has never had one).
inline StoredClientPathView selectStoredClientPath(
    bool runtime_path_is_persistable,
    uint8_t runtime_path_len,
    const uint8_t* runtime_path,
    bool prior_path_exists,
    uint8_t prior_path_len,
    const uint8_t* prior_path,
    uint8_t unknown_path,
    const uint8_t* empty_path) {
  if (runtime_path_is_persistable) {
    return {runtime_path_len, runtime_path};
  }
  if (prior_path_exists) {
    return {prior_path_len, prior_path};
  }
  return {unknown_path, empty_path};
}

// Packet paths encode the hash width in the upper two bits and the hop count
// in the lower six bits. This intentionally matches Packet::writePath()'s byte
// count without pulling the firmware-only Packet dependencies into host tests.
inline size_t encodedClientPathByteLength(uint8_t encoded_path_len) {
  const size_t hash_count = encoded_path_len & 63U;
  const size_t hash_size = (encoded_path_len >> 6) + 1U;
  return hash_count * hash_size;
}

inline bool isValidEncodedClientPathLength(uint8_t encoded_path_len,
                                           size_t capacity) {
  const size_t hash_size = (encoded_path_len >> 6) + 1U;
  return hash_size != 4U
      && encodedClientPathByteLength(encoded_path_len) <= capacity;
}

// Update the RAM route and report whether the caller owns a persistent change.
// FORCE_FLOOD is an explicit operator setting and is never replaced by a
// learned route. Bytes beyond the encoded route length are deliberately
// ignored: they are stale capacity, not part of the logical path.
template <typename Client>
ClientPathUpdateResult applyReceivedClientPath(
    Client& client,
    const uint8_t* path,
    uint8_t encoded_path_len,
    bool client_is_persistable,
    uint8_t force_flood_path) {
  if (client.out_path_len == force_flood_path) return {false, false};

  const size_t path_bytes = encodedClientPathByteLength(encoded_path_len);
  if (!isValidEncodedClientPathLength(
          encoded_path_len, sizeof(client.out_path))
      || (path_bytes != 0 && path == NULL)) {
    return {false, false};
  }

  const bool changed = client.out_path_len != encoded_path_len
      || (path_bytes != 0
          && memcmp(client.out_path, path, path_bytes) != 0);
  if (!changed) {
    // A later request may carry the replay/freshness proof that the first copy
    // lacked.  Promote the identical RAM route without requiring it to change
    // bytes a second time.
    const bool persistence_upgrade = client_is_persistable
        && !client.out_path_is_persistable;
    if (persistence_upgrade) client.out_path_is_persistable = true;
    return {persistence_upgrade, persistence_upgrade};
  }

  if (path_bytes != 0) memcpy(client.out_path, path, path_bytes);
  client.out_path_len = encoded_path_len;
  client.out_path_is_persistable = client_is_persistable;
  return {true, client_is_persistable};
}

} // namespace mesh
