#pragma once

#include <stddef.h>
#include <stdint.h>
#include <string.h>

namespace mesh {

// Login timestamps are security state, not ordinary contact activity.  Keep
// their durable high-water marks outside /s_contacts so an ACL eviction or an
// explicit role revocation cannot erase the replay boundary with the contact.
static const char CLIENT_LOGIN_REPLAY_PRIMARY_PATH[] = "/s_login_replay";
static const char CLIENT_LOGIN_REPLAY_TEMP_PATH[] = "/s_login_replay.tmp";
static const char CLIENT_LOGIN_REPLAY_BACKUP_PATH[] = "/s_login_replay.bak";
static const uint8_t CLIENT_LOGIN_REPLAY_MAGIC[4] = {'M', 'C', 'R', '1'};
static const size_t CLIENT_LOGIN_REPLAY_RECORD_SIZE = 32 + sizeof(uint32_t);
static const size_t CLIENT_LOGIN_REPLAY_TRAILER_SIZE =
    sizeof(CLIENT_LOGIN_REPLAY_MAGIC) + sizeof(uint32_t);

// A successful login reserves this many sender-clock seconds in one durable
// write.  Further monotonically increasing logins inside the reservation need
// no flash write.  After a reset, the unused tail is deliberately skipped;
// this is the availability cost that prevents an already accepted timestamp
// from becoming fresh again.  One minute bounds that cost while coalescing
// rapid reconnects and Bluetooth/Wi-Fi transport changes.
static const uint32_t CLIENT_LOGIN_REPLAY_RESERVATION_SECONDS = 60;

// This is deliberately larger than the live ACL.  Historical entries are
// privileged tombstones and may not be evicted safely while their password
// remains usable.  A full store fails closed for a new privileged identity
// rather than silently discarding an older replay boundary; low-trust roles do
// not allocate records.
static const size_t MAX_CLIENT_LOGIN_REPLAY_IDENTITIES = 128;

struct ClientLoginReplayPlan {
  bool fresh;
  bool reservation_needed;
  uint32_t reservation_ceiling;
};

enum class ClientLoginReplayReservationAction : uint8_t {
  None,
  UpdateExisting,
  CreateNew,
};

// Guest and read-only sessions can consume public or deliberately low-trust
// credentials.  They still obey the exact in-boot floor and any historical
// privileged tombstone, but must not allocate a new permanent identity in the
// bounded replay store.  Read/write and manager roles retain the durable
// pre-allocation reservation which protects a later revocation or ACL
// eviction.
inline bool clientLoginRoleNeedsDurableReplay(
    uint8_t permissions,
    uint8_t role_mask,
    uint8_t guest_role,
    uint8_t read_only_role) {
  const uint8_t role = permissions & role_mask;
  return role != (guest_role & role_mask)
      && role != (read_only_role & role_mask);
}

// Select durable work only after freshness is established.  An existing
// privileged tombstone remains authoritative even if the identity currently
// logs in with a guest/read-only role, so advance that record rather than
// silently weakening revoked-role protection.  Only a missing low-trust
// identity stays RAM-only.
inline ClientLoginReplayReservationAction selectClientLoginReplayReservation(
    bool reservation_needed,
    bool identity_already_stored,
    bool role_needs_durable_replay) {
  if (!reservation_needed) {
    return ClientLoginReplayReservationAction::None;
  }
  if (identity_already_stored) {
    return ClientLoginReplayReservationAction::UpdateExisting;
  }
  return role_needs_durable_replay
      ? ClientLoginReplayReservationAction::CreateNew
      : ClientLoginReplayReservationAction::None;
}

inline bool clientLoginReplayCanInsert(size_t stored_identity_count) {
  return stored_identity_count < MAX_CLIENT_LOGIN_REPLAY_IDENTITIES;
}

inline uint32_t reserveClientLoginTimestamp(uint32_t sender_timestamp,
                                            uint32_t reservation_seconds) {
  const uint32_t room = UINT32_MAX - sender_timestamp;
  return room < reservation_seconds
      ? UINT32_MAX
      : sender_timestamp + reservation_seconds;
}

// Plan the admission before mutating the ACL.  runtime_last_timestamp is
// non-zero only when this boot has live state for the identity.  Otherwise the
// persisted ceiling is the freshness floor (boot, eviction, and revocation all
// take this branch).  The caller must durably publish reservation_ceiling
// before treating a plan with reservation_needed as accepted.
inline ClientLoginReplayPlan planClientLoginReplay(
    uint32_t runtime_last_timestamp,
    uint32_t persisted_ceiling,
    uint32_t sender_timestamp,
    uint32_t reservation_seconds =
        CLIENT_LOGIN_REPLAY_RESERVATION_SECONDS) {
  const uint32_t freshness_floor = runtime_last_timestamp != 0
      ? runtime_last_timestamp
      : persisted_ceiling;
  if (sender_timestamp == 0 || sender_timestamp <= freshness_floor) {
    return {false, false, persisted_ceiling};
  }
  if (sender_timestamp <= persisted_ceiling) {
    return {true, false, persisted_ceiling};
  }
  return {
      true,
      true,
      reserveClientLoginTimestamp(sender_timestamp, reservation_seconds),
  };
}

inline uint32_t updateClientLoginReplayCRC(uint32_t crc,
                                           const uint8_t* data,
                                           size_t length) {
  while (length-- != 0) {
    crc ^= *data++;
    for (uint8_t bit = 0; bit < 8; bit++) {
      crc = (crc >> 1) ^ (0xEDB88320UL &
          (uint32_t)-(int32_t)(crc & 1U));
    }
  }
  return crc;
}

inline bool validateClientLoginReplayImage(const uint8_t* image,
                                            size_t size) {
  if (image == NULL || size < CLIENT_LOGIN_REPLAY_TRAILER_SIZE) return false;
  const size_t payload_size = size - CLIENT_LOGIN_REPLAY_TRAILER_SIZE;
  if (payload_size % CLIENT_LOGIN_REPLAY_RECORD_SIZE != 0
      || payload_size / CLIENT_LOGIN_REPLAY_RECORD_SIZE
          > MAX_CLIENT_LOGIN_REPLAY_IDENTITIES) {
    return false;
  }
  if (memcmp(image + payload_size, CLIENT_LOGIN_REPLAY_MAGIC,
             sizeof(CLIENT_LOGIN_REPLAY_MAGIC)) != 0) {
    return false;
  }
  for (size_t offset = 0; offset < payload_size;
       offset += CLIENT_LOGIN_REPLAY_RECORD_SIZE) {
    uint32_t ceiling;
    memcpy(&ceiling, image + offset + 32, sizeof(ceiling));
    if (ceiling == 0) return false;
  }
  uint32_t stored_crc;
  memcpy(&stored_crc,
         image + payload_size + sizeof(CLIENT_LOGIN_REPLAY_MAGIC),
         sizeof(stored_crc));
  const uint32_t crc = updateClientLoginReplayCRC(
      0xFFFFFFFFUL, image, payload_size) ^ 0xFFFFFFFFUL;
  return stored_crc == crc;
}

template <typename Filesystem>
bool removeClientLoginReplayArtifact(Filesystem* fs, const char* path) {
  if (!fs->exists(path)) return true;
  fs->remove(path);
  return !fs->exists(path);
}

template <typename Filesystem, typename Validator>
bool recoverClientLoginReplayFiles(Filesystem* fs, Validator is_valid) {
  if (fs->exists(CLIENT_LOGIN_REPLAY_PRIMARY_PATH)
      && is_valid(fs, CLIENT_LOGIN_REPLAY_PRIMARY_PATH)) {
    // The validated primary is already authoritative.  Cleanup failure must
    // not disable replay protection or make committed state appear absent.
    removeClientLoginReplayArtifact(fs, CLIENT_LOGIN_REPLAY_TEMP_PATH);
    removeClientLoginReplayArtifact(fs, CLIENT_LOGIN_REPLAY_BACKUP_PATH);
    return true;
  }
  if (fs->exists(CLIENT_LOGIN_REPLAY_BACKUP_PATH)
      && is_valid(fs, CLIENT_LOGIN_REPLAY_BACKUP_PATH)) {
    if (!removeClientLoginReplayArtifact(fs,
                                          CLIENT_LOGIN_REPLAY_PRIMARY_PATH)
        || !fs->rename(CLIENT_LOGIN_REPLAY_BACKUP_PATH,
                       CLIENT_LOGIN_REPLAY_PRIMARY_PATH)) {
      return false;
    }
    removeClientLoginReplayArtifact(fs, CLIENT_LOGIN_REPLAY_TEMP_PATH);
    return true;
  }
  // A never-created store is the upgrade/first-boot case.  An invalid live or
  // backup image is different: retain it for diagnosis and fail closed.
  return !fs->exists(CLIENT_LOGIN_REPLAY_PRIMARY_PATH)
      && !fs->exists(CLIENT_LOGIN_REPLAY_BACKUP_PATH)
      && removeClientLoginReplayArtifact(fs,
                                          CLIENT_LOGIN_REPLAY_TEMP_PATH);
}

template <typename Filesystem, typename Validator>
bool publishClientLoginReplayTemp(Filesystem* fs,
                                  bool temp_verified,
                                  Validator is_valid) {
  if (!temp_verified) {
    removeClientLoginReplayArtifact(fs, CLIENT_LOGIN_REPLAY_TEMP_PATH);
    return false;
  }
  if (fs->exists(CLIENT_LOGIN_REPLAY_BACKUP_PATH)) return false;

  const bool had_primary = fs->exists(CLIENT_LOGIN_REPLAY_PRIMARY_PATH);
  if (had_primary
      && !fs->rename(CLIENT_LOGIN_REPLAY_PRIMARY_PATH,
                     CLIENT_LOGIN_REPLAY_BACKUP_PATH)) {
    return false;
  }
  if (!fs->rename(CLIENT_LOGIN_REPLAY_TEMP_PATH,
                  CLIENT_LOGIN_REPLAY_PRIMARY_PATH)
      || !is_valid(fs, CLIENT_LOGIN_REPLAY_PRIMARY_PATH)) {
    removeClientLoginReplayArtifact(fs, CLIENT_LOGIN_REPLAY_PRIMARY_PATH);
    if (had_primary) {
      fs->rename(CLIENT_LOGIN_REPLAY_BACKUP_PATH,
                 CLIENT_LOGIN_REPLAY_PRIMARY_PATH);
    }
    return false;
  }
  // The new replay ceiling committed at the successful rename and
  // validation.  Treat stale-backup cleanup as best-effort so callers never
  // reject a login whose durable reservation is already on disk.
  removeClientLoginReplayArtifact(fs, CLIENT_LOGIN_REPLAY_BACKUP_PATH);
  return true;
}

inline bool clientRoleIsPersisted(uint8_t permissions,
                                  uint8_t role_mask,
                                  uint8_t guest_role) {
  return (permissions & role_mask) != (guest_role & role_mask);
}

inline bool isFreshClientLoginTimestamp(bool client_existed,
                                        uint32_t sender_timestamp,
                                        uint32_t last_timestamp) {
  return sender_timestamp != 0
      && (!client_existed || sender_timestamp > last_timestamp);
}

inline bool successfulClientLoginNeedsPersistence(bool client_existed,
                                                   uint8_t previous_permissions,
                                                   uint8_t next_permissions,
                                                   uint8_t role_mask,
                                                   uint8_t guest_role,
                                                   bool persisted_changed) {
  if (!persisted_changed) return false;
  const uint8_t guest = guest_role & role_mask;
  const bool previously_persisted = client_existed
      && clientRoleIsPersisted(previous_permissions, role_mask, guest);
  const bool now_persisted =
      clientRoleIsPersisted(next_permissions, role_mask, guest);
  // A downgrade to guest must rewrite the ACL even though the guest itself is
  // omitted from the file; otherwise the prior persisted role survives reboot.
  return previously_persisted || now_persisted;
}

// Apply the successful-login state transition and report whether any field
// written by ClientACL::save() changed. Timestamps are deliberately excluded
// from that result: the exact live value is RAM state, while its reserved
// high-water boundary is synchronously owned by the separate replay store.
template <typename Client>
bool applySuccessfulClientLogin(
    Client& client,
    bool client_existed,
    uint8_t role_permissions,
    uint8_t role_mask,
    const uint8_t* shared_secret,
    uint32_t sender_timestamp,
    uint32_t activity_timestamp,
    bool reset_out_path,
    uint8_t unknown_path) {
  const uint8_t next_permissions =
      (uint8_t)((client.permissions & (uint8_t)~role_mask)
                | (role_permissions & role_mask));
  bool persisted_changed = !client_existed
      || client.permissions != next_permissions
      || memcmp(client.shared_secret, shared_secret,
                sizeof(client.shared_secret)) != 0
      || (reset_out_path && client.out_path_len != unknown_path);

  client.last_timestamp = sender_timestamp;
  client.last_activity = activity_timestamp;
  client.permissions = next_permissions;
  memcpy(client.shared_secret, shared_secret, sizeof(client.shared_secret));
  if (reset_out_path) {
    client.out_path_len = unknown_path;
    client.out_path_is_persistable = true;
  }

  return persisted_changed;
}

} // namespace mesh
