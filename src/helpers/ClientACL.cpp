#include "ClientACL.h"
#include "ClientACLFileTransaction.h"
#include "ClientACLFileIntegrity.h"
#include "ClientLoginPersistence.h"
#include "ClientPathPersistence.h"
#if defined(NRF52_PLATFORM)
#include "AtomicFileWriter.h"
#endif

static const uint8_t CONTACT_RECORD_VERSION_ALT_PATH = 1;
static const uint8_t EMPTY_OUT_PATH[MAX_PATH_SIZE] = {};

static File openRead(FILESYSTEM* fs, const char* filename) {
#if defined(RP2040_PLATFORM)
  return fs->open(filename, "r");
#else
  return fs->open(filename);
#endif
}

#if !defined(NRF52_PLATFORM)
static const size_t CONTACT_RECORD_SIZE =
    32 + 1 + 4 + 2 + 1 + 64 + PUB_KEY_SIZE + 1 + 64;
static const size_t LEGACY_CONTACT_RECORD_SIZE =
    32 + 1 + 4 + 2 + 1 + 64 + PUB_KEY_SIZE;
#endif

static bool readPersistedClientPath(FILESYSTEM* fs,
                                    const uint8_t pubkey[PUB_KEY_SIZE],
                                    uint8_t* path_len,
                                    uint8_t path[MAX_PATH_SIZE]) {
  if (!fs->exists(mesh::CLIENT_ACL_PRIMARY_PATH)) return false;
  File file = openRead(fs, mesh::CLIENT_ACL_PRIMARY_PATH);
  if (!file) return false;

  bool found = false;
  while (!found) {
    uint8_t record_pubkey[PUB_KEY_SIZE];
    uint8_t permissions;
    uint32_t sync_since;
    uint8_t version[2];
    uint8_t record_path_len;
    uint8_t record_path[MAX_PATH_SIZE];
    uint8_t shared_secret[PUB_KEY_SIZE];

    bool success = file.read(record_pubkey, sizeof(record_pubkey))
        == (int)sizeof(record_pubkey);
    success = success && file.read(&permissions, sizeof(permissions))
        == (int)sizeof(permissions);
    success = success
        && file.read((uint8_t*)&sync_since, sizeof(sync_since))
            == (int)sizeof(sync_since);
    success = success && file.read(version, sizeof(version))
        == (int)sizeof(version);
    success = success && file.read(&record_path_len, sizeof(record_path_len))
        == (int)sizeof(record_path_len);
    success = success && file.read(record_path, sizeof(record_path))
        == (int)sizeof(record_path);
    success = success && file.read(shared_secret, sizeof(shared_secret))
        == (int)sizeof(shared_secret);
    if (!success) break;

    if (version[0] >= CONTACT_RECORD_VERSION_ALT_PATH) {
      uint8_t alt_path_len;
      uint8_t alt_path[MAX_PATH_SIZE];
      success = file.read(&alt_path_len, sizeof(alt_path_len))
          == (int)sizeof(alt_path_len);
      success = success && file.read(alt_path, sizeof(alt_path))
          == (int)sizeof(alt_path);
      if (!success) break;
    }

    if (memcmp(record_pubkey, pubkey, PUB_KEY_SIZE) == 0
        && (record_path_len == OUT_PATH_UNKNOWN
            || record_path_len == OUT_PATH_FORCE_FLOOD
            || mesh::isValidEncodedClientPathLength(
                record_path_len, MAX_PATH_SIZE))) {
      *path_len = record_path_len;
      memcpy(path, record_path, MAX_PATH_SIZE);
      found = true;
    }
  }
  file.close();
  return found;
}

static mesh::StoredClientPathView storedClientPathForSave(
    FILESYSTEM* fs,
    const ClientInfo* client,
    uint8_t prior_path[MAX_PATH_SIZE],
    uint8_t* prior_path_len) {
  const bool prior_exists = !client->out_path_is_persistable
      && readPersistedClientPath(
          fs, client->id.pub_key, prior_path_len, prior_path);
  return mesh::selectStoredClientPath(
      client->out_path_is_persistable,
      client->out_path_len,
      client->out_path,
      prior_exists,
      *prior_path_len,
      prior_path,
      OUT_PATH_UNKNOWN,
      EMPTY_OUT_PATH);
}

#if !defined(NRF52_PLATFORM)

static File openWrite(FILESYSTEM* _fs, const char* filename) {
  #if defined(STM32_PLATFORM)
    _fs->remove(filename);
    return _fs->open(filename, FILE_O_WRITE);
  #elif defined(RP2040_PLATFORM)
    return _fs->open(filename, "w");
  #else
    return _fs->open(filename, "w", true);
  #endif
}

static bool readMatches(File& file, const uint8_t* expected, size_t length) {
  uint8_t buffer[32];
  while (length > 0) {
    const size_t chunk = length < sizeof(buffer) ? length : sizeof(buffer);
    if (file.read(buffer, chunk) != (int)chunk
        || memcmp(buffer, expected, chunk) != 0) {
      return false;
    }
    expected += chunk;
    length -= chunk;
  }
  return true;
}

static bool validateContactsFileIntegrity(FILESYSTEM* fs,
                                          const char* filename) {
  File file = openRead(fs, filename);
  if (!file) return false;
  const size_t size = file.size();
  const bool has_crc = size >= 8 && (size - 8) % CONTACT_RECORD_SIZE == 0;
  // A backup means publication was interrupted or is being finalized. In that
  // state a primary without its CRC trailer is a torn new image, not a legacy
  // image, even when truncation lands exactly on a record-size multiple.
  const bool crc_required = strcmp(filename, mesh::CLIENT_ACL_PRIMARY_PATH) == 0
      && fs->exists(mesh::CLIENT_ACL_BACKUP_PATH);
  if (!has_crc && size % CONTACT_RECORD_SIZE != 0
      && size % LEGACY_CONTACT_RECORD_SIZE != 0) {
    file.close();
    return false;
  }
  if (!has_crc) {
    file.close();
    return !crc_required; // standalone legacy fixed-record image
  }
  uint32_t crc = 0xFFFFFFFFUL;
  size_t remaining = size - 8;
  uint8_t buffer[32];
  while (remaining != 0) {
    const size_t chunk = remaining < sizeof(buffer) ? remaining : sizeof(buffer);
    if (file.read(buffer, chunk) != (int)chunk) {
      file.close();
      return false;
    }
    crc = mesh::updateClientACLCRC(crc, buffer, chunk);
    remaining -= chunk;
  }
  uint8_t magic[4];
  uint32_t stored_crc;
  const bool valid = file.read(magic, sizeof(magic)) == (int)sizeof(magic)
      && file.read((uint8_t*)&stored_crc, sizeof(stored_crc))
          == (int)sizeof(stored_crc)
      && memcmp(magic, mesh::CLIENT_ACL_CRC_MAGIC, sizeof(magic)) == 0
      && stored_crc == (crc ^ 0xFFFFFFFFUL);
  file.close();
  return valid;
}

static bool verifyContactsFile(
    FILESYSTEM* fs,
    const char* filename,
    ClientInfo* clients,
    int num_clients,
    bool (*filter)(ClientInfo*),
    size_t expected_size) {
  File file = openRead(fs, filename);
  if (!file || file.size() != expected_size + 8) {
    if (file) file.close();
    return false;
  }

  const uint8_t unused[2] = {CONTACT_RECORD_VERSION_ALT_PATH, 0};
  bool matches = true;
  for (int i = 0; matches && i < num_clients; i++) {
    ClientInfo* c = &clients[i];
    if (!mesh::clientRoleIsPersisted(
            c->permissions, PERM_ACL_ROLE_MASK, PERM_ACL_GUEST)
        || (filter && !filter(c))) continue;
    uint8_t prior_path_len = OUT_PATH_UNKNOWN;
    uint8_t prior_path[MAX_PATH_SIZE] = {};
    const mesh::StoredClientPathView persisted_path =
        storedClientPathForSave(
            fs, c, prior_path, &prior_path_len);
    matches = readMatches(file, c->id.pub_key, 32)
        && readMatches(file, &c->permissions, 1)
        && readMatches(file, (uint8_t*)&c->extra.room.sync_since, 4)
        && readMatches(file, unused, sizeof(unused))
        && readMatches(file, &persisted_path.encoded_path_len, 1)
        && readMatches(file, persisted_path.path, 64)
        && readMatches(file, c->shared_secret, PUB_KEY_SIZE)
        && readMatches(file, &c->alt_path_len, 1)
        && readMatches(file, c->alt_path, 64);
  }
  uint8_t magic[4];
  uint32_t stored_crc;
  matches = matches
      && file.read(magic, sizeof(magic)) == (int)sizeof(magic)
      && file.read((uint8_t*)&stored_crc, sizeof(stored_crc))
          == (int)sizeof(stored_crc)
      && memcmp(magic, mesh::CLIENT_ACL_CRC_MAGIC, sizeof(magic)) == 0;
  file.close();
  return matches && validateContactsFileIntegrity(fs, filename);
}
#endif

static bool validateLoginReplayFileIntegrity(FILESYSTEM* fs,
                                             const char* filename) {
  File file = openRead(fs, filename);
  if (!file) return false;
  const size_t size = file.size();
  if (size < mesh::CLIENT_LOGIN_REPLAY_TRAILER_SIZE) {
    file.close();
    return false;
  }
  const size_t payload_size =
      size - mesh::CLIENT_LOGIN_REPLAY_TRAILER_SIZE;
  const size_t record_count =
      payload_size / mesh::CLIENT_LOGIN_REPLAY_RECORD_SIZE;
  if (payload_size % mesh::CLIENT_LOGIN_REPLAY_RECORD_SIZE != 0
      || record_count > mesh::MAX_CLIENT_LOGIN_REPLAY_IDENTITIES) {
    file.close();
    return false;
  }

  uint32_t crc = 0xFFFFFFFFUL;
  bool valid = true;
  for (size_t i = 0; valid && i < record_count; i++) {
    uint8_t pubkey[PUB_KEY_SIZE];
    uint32_t ceiling;
    valid = file.read(pubkey, sizeof(pubkey)) == (int)sizeof(pubkey)
        && file.read((uint8_t*)&ceiling, sizeof(ceiling))
            == (int)sizeof(ceiling)
        && ceiling != 0;
    if (valid) {
      crc = mesh::updateClientLoginReplayCRC(
          crc, pubkey, sizeof(pubkey));
      crc = mesh::updateClientLoginReplayCRC(
          crc, (const uint8_t*)&ceiling, sizeof(ceiling));
    }
  }
  uint8_t magic[sizeof(mesh::CLIENT_LOGIN_REPLAY_MAGIC)];
  uint32_t stored_crc;
  valid = valid
      && file.read(magic, sizeof(magic)) == (int)sizeof(magic)
      && file.read((uint8_t*)&stored_crc, sizeof(stored_crc))
          == (int)sizeof(stored_crc)
      && memcmp(magic, mesh::CLIENT_LOGIN_REPLAY_MAGIC,
                sizeof(magic)) == 0
      && stored_crc == (crc ^ 0xFFFFFFFFUL);
  file.close();
  return valid;
}

// The image is validated before this scan.  Choose the maximum if a legacy or
// manually recovered image somehow contains a duplicate identity; taking the
// strongest boundary remains fail-safe.
static bool readClientLoginReplayCeiling(
    FILESYSTEM* fs,
    const uint8_t pubkey[PUB_KEY_SIZE],
    uint32_t* ceiling,
    bool* found) {
  *ceiling = 0;
  *found = false;
  if (!fs->exists(mesh::CLIENT_LOGIN_REPLAY_PRIMARY_PATH)) return true;
  if (!validateLoginReplayFileIntegrity(
          fs, mesh::CLIENT_LOGIN_REPLAY_PRIMARY_PATH)) {
    return false;
  }
  File file = openRead(fs, mesh::CLIENT_LOGIN_REPLAY_PRIMARY_PATH);
  if (!file) return false;
  const size_t record_count =
      (file.size() - mesh::CLIENT_LOGIN_REPLAY_TRAILER_SIZE)
      / mesh::CLIENT_LOGIN_REPLAY_RECORD_SIZE;
  bool success = true;
  for (size_t i = 0; success && i < record_count; i++) {
    uint8_t record_pubkey[PUB_KEY_SIZE];
    uint32_t record_ceiling;
    success = file.read(record_pubkey, sizeof(record_pubkey))
        == (int)sizeof(record_pubkey);
    success = success
        && file.read((uint8_t*)&record_ceiling, sizeof(record_ceiling))
            == (int)sizeof(record_ceiling);
    if (success && memcmp(record_pubkey, pubkey, PUB_KEY_SIZE) == 0
        && (!*found || record_ceiling > *ceiling)) {
      *ceiling = record_ceiling;
      *found = true;
    }
  }
  file.close();
  return success;
}

template <typename Writer>
static bool writeClientLoginReplayRecord(
    Writer& writer,
    const uint8_t pubkey[PUB_KEY_SIZE],
    uint32_t ceiling,
    uint32_t* crc) {
  const bool success = writer.write(pubkey, PUB_KEY_SIZE) == PUB_KEY_SIZE
      && writer.write((const uint8_t*)&ceiling, sizeof(ceiling))
          == sizeof(ceiling);
  if (success) {
    *crc = mesh::updateClientLoginReplayCRC(
        *crc, pubkey, PUB_KEY_SIZE);
    *crc = mesh::updateClientLoginReplayCRC(
        *crc, (const uint8_t*)&ceiling, sizeof(ceiling));
  }
  return success;
}

static bool writeClientLoginReplayCeiling(
    FILESYSTEM* fs,
    const uint8_t pubkey[PUB_KEY_SIZE],
    uint32_t new_ceiling,
    mesh::ClientLoginReplayReservationAction action) {
  if (action == mesh::ClientLoginReplayReservationAction::None) return true;
#if defined(NRF52_PLATFORM)
  if (fs->exists(mesh::CLIENT_LOGIN_REPLAY_PRIMARY_PATH)
      && !validateLoginReplayFileIntegrity(
          fs, mesh::CLIENT_LOGIN_REPLAY_PRIMARY_PATH)) {
    return false;
  }
#else
  if (!mesh::recoverClientLoginReplayFiles(
          fs, validateLoginReplayFileIntegrity)) {
    return false;
  }
#endif

  File source = openRead(fs, mesh::CLIENT_LOGIN_REPLAY_PRIMARY_PATH);
  const size_t record_count = source
      ? (source.size() - mesh::CLIENT_LOGIN_REPLAY_TRAILER_SIZE)
          / mesh::CLIENT_LOGIN_REPLAY_RECORD_SIZE
      : 0;
#if defined(NRF52_PLATFORM)
  mesh::AtomicFileWriter destination(
      fs, mesh::CLIENT_LOGIN_REPLAY_PRIMARY_PATH);
#else
  File destination = openWrite(
      fs, mesh::CLIENT_LOGIN_REPLAY_TEMP_PATH);
#endif
  if (!destination) {
    if (source) source.close();
    return false;
  }

  uint32_t crc = 0xFFFFFFFFUL;
  bool found = false;
  bool success = true;
  for (size_t i = 0; success && i < record_count; i++) {
    uint8_t record_pubkey[PUB_KEY_SIZE];
    uint32_t record_ceiling;
    success = source.read(record_pubkey, sizeof(record_pubkey))
        == (int)sizeof(record_pubkey);
    success = success
        && source.read((uint8_t*)&record_ceiling, sizeof(record_ceiling))
            == (int)sizeof(record_ceiling);
    if (!success) break;
    if (memcmp(record_pubkey, pubkey, PUB_KEY_SIZE) == 0) {
      found = true;
      if (record_ceiling < new_ceiling) record_ceiling = new_ceiling;
    }
    success = writeClientLoginReplayRecord(
        destination, record_pubkey, record_ceiling, &crc);
  }
  if (source) source.close();

  if (success
      && action == mesh::ClientLoginReplayReservationAction::UpdateExisting
      && !found) {
    // Do not turn a low-trust update into insertion if the image changed
    // between its admission read and this transaction.
    success = false;
  }
  if (success && !found
      && action == mesh::ClientLoginReplayReservationAction::CreateNew) {
    success = mesh::clientLoginReplayCanInsert(record_count);
  }
  if (success && !found) {
    success = writeClientLoginReplayRecord(
        destination, pubkey, new_ceiling, &crc);
  }
  if (success) {
    const uint32_t final_crc = crc ^ 0xFFFFFFFFUL;
    success = destination.write(
                  mesh::CLIENT_LOGIN_REPLAY_MAGIC,
                  sizeof(mesh::CLIENT_LOGIN_REPLAY_MAGIC))
            == sizeof(mesh::CLIENT_LOGIN_REPLAY_MAGIC)
        && destination.write(
                  (const uint8_t*)&final_crc, sizeof(final_crc))
            == sizeof(final_crc);
  }

#if defined(NRF52_PLATFORM)
  return destination.commit(success);
#else
  destination.close();
  const bool verified = success && validateLoginReplayFileIntegrity(
      fs, mesh::CLIENT_LOGIN_REPLAY_TEMP_PATH);
  return mesh::publishClientLoginReplayTemp(
      fs, verified, validateLoginReplayFileIntegrity);
#endif
}

void ClientACL::load(FILESYSTEM* fs, const mesh::LocalIdentity& self_id) {
  _fs = fs;
  num_clients = 0;
#if defined(NRF52_PLATFORM)
  // AtomicFileWriter may leave only a harmless temp image when reset before
  // rename.  The live image remains authoritative.
  mesh::removeClientLoginReplayArtifact(
      _fs, mesh::CLIENT_LOGIN_REPLAY_TEMP_PATH);
  login_replay_store_available =
      !_fs->exists(mesh::CLIENT_LOGIN_REPLAY_PRIMARY_PATH)
      || validateLoginReplayFileIntegrity(
          _fs, mesh::CLIENT_LOGIN_REPLAY_PRIMARY_PATH);
#else
  login_replay_store_available = mesh::recoverClientLoginReplayFiles(
      _fs, validateLoginReplayFileIntegrity);
#endif
  if (!login_replay_store_available) {
    MESH_DEBUG_PRINTLN(
        "ERROR: ClientACL::load could not recover login replay state");
  }
#if !defined(NRF52_PLATFORM)
  if (!mesh::recoverClientACLFilesVerified(
          _fs, validateContactsFileIntegrity)) {
    MESH_DEBUG_PRINTLN("ERROR: ClientACL::load could not recover contacts files");
    return;
  }
#endif
  if (_fs->exists("/s_contacts")) {
  #if defined(RP2040_PLATFORM)
    File file = _fs->open("/s_contacts", "r");
  #else
    File file = _fs->open("/s_contacts");
  #endif
    if (file) {
      bool full = false;
      while (!full) {
        ClientInfo c;
        uint8_t pub_key[32];
        uint8_t unused[2];

        memset(&c, 0, sizeof(c));
        c.alt_path_len = OUT_PATH_UNKNOWN;
        c.observed_path_len = OUT_PATH_UNKNOWN;

        bool success = (file.read(pub_key, 32) == 32);
        success = success && (file.read((uint8_t *) &c.permissions, 1) == 1);
        success = success && (file.read((uint8_t *) &c.extra.room.sync_since, 4) == 4);
        success = success && (file.read(unused, 2) == 2);
        success = success && (file.read((uint8_t *)&c.out_path_len, 1) == 1);
        success = success && (file.read(c.out_path, 64) == 64);
        success = success && (file.read(c.shared_secret, PUB_KEY_SIZE) == PUB_KEY_SIZE); // will be recalculated below
        if (success && unused[0] >= CONTACT_RECORD_VERSION_ALT_PATH) {
          success = success && (file.read((uint8_t *)&c.alt_path_len, 1) == 1);
          success = success && (file.read(c.alt_path, 64) == 64);
        }

        if (!success) break; // EOF

        c.id = mesh::Identity(pub_key);
        c.out_path_is_persistable = true;
        if (login_replay_store_available) {
          uint32_t replay_ceiling;
          bool replay_entry_found;
          if (!readClientLoginReplayCeiling(
                  _fs, pub_key, &replay_ceiling, &replay_entry_found)) {
            login_replay_store_available = false;
            c.last_timestamp = UINT32_MAX;
          } else if (replay_entry_found) {
            // On boot, skip the unused reservation tail.  During this boot a
            // successful login replaces this with its exact accepted value.
            c.last_timestamp = replay_ceiling;
          }
        } else {
          c.last_timestamp = UINT32_MAX;
        }
        self_id.calcSharedSecret(c.shared_secret, pub_key);  // recalculate shared secrets in case our private key changed
        if (num_clients < MAX_CLIENTS) {
          clients[num_clients++] = c;
        } else {
          full = true;
        }
      }
      file.close();
    }
  }
}

bool ClientACL::authorizeLoginTimestamp(
    const uint8_t* pubkey,
    uint32_t sender_timestamp,
    uint32_t runtime_last_timestamp,
    uint8_t login_permissions) {
  if (_fs == NULL || pubkey == NULL || !login_replay_store_available) {
    return false;
  }

  uint32_t persisted_ceiling;
  bool replay_entry_found;
  if (!readClientLoginReplayCeiling(
          _fs, pubkey, &persisted_ceiling, &replay_entry_found)) {
    login_replay_store_available = false;
    return false;
  }
  if (!replay_entry_found) persisted_ceiling = 0;

  const bool role_needs_durable_replay =
      mesh::clientLoginRoleNeedsDurableReplay(
          login_permissions, PERM_ACL_ROLE_MASK,
          PERM_ACL_GUEST, PERM_ACL_READ_ONLY);
  const mesh::ClientLoginReplayPlan plan = mesh::planClientLoginReplay(
      runtime_last_timestamp, persisted_ceiling, sender_timestamp);
  if (!plan.fresh) return false;
  const mesh::ClientLoginReplayReservationAction action =
      mesh::selectClientLoginReplayReservation(
          plan.reservation_needed, replay_entry_found,
          role_needs_durable_replay);
  if (action != mesh::ClientLoginReplayReservationAction::None
      && !writeClientLoginReplayCeiling(
          _fs, pubkey, plan.reservation_ceiling, action)) {
    // The old valid image remains usable after an ordinary write failure, but
    // this login is not safe to accept until its new boundary is durable.
    return false;
  }
  return true;
}

bool ClientACL::save(FILESYSTEM* fs, bool (*filter)(ClientInfo*)) {
  _fs = fs;
#if defined(NRF52_PLATFORM)
  mesh::AtomicFileWriter file(_fs, "/s_contacts");
#else
  if (!mesh::recoverClientACLFilesVerified(
          _fs, validateContactsFileIntegrity)) {
    MESH_DEBUG_PRINTLN("ERROR: ClientACL::save recovery is incomplete");
    return false;
  }
  File file = openWrite(_fs, mesh::CLIENT_ACL_TEMP_PATH);
#endif
  if (!file) {
    MESH_DEBUG_PRINTLN("ERROR: ClientACL::save could not open contacts file");
    return false;
  }

  uint8_t unused[2];
  unused[0] = CONTACT_RECORD_VERSION_ALT_PATH;
  unused[1] = 0;

  bool success = true;
#if !defined(NRF52_PLATFORM)
  size_t expected_size = 0;
  uint32_t contacts_crc = 0xFFFFFFFFUL;
#endif
  for (int i = 0; success && i < num_clients; i++) {
    auto c = &clients[i];
    if (!mesh::clientRoleIsPersisted(
            c->permissions, PERM_ACL_ROLE_MASK, PERM_ACL_GUEST)
        || (filter && !filter(c))) continue; // skip guests/deleted entries or filtered clients

    success = (file.write(c->id.pub_key, 32) == 32);
    success = success && (file.write((uint8_t *) &c->permissions, 1) == 1);
    success = success && (file.write((uint8_t *) &c->extra.room.sync_since, 4) == 4);
    success = success && (file.write(unused, 2) == 2);
    uint8_t prior_path_len = OUT_PATH_UNKNOWN;
    uint8_t prior_path[MAX_PATH_SIZE] = {};
    const mesh::StoredClientPathView persisted_path =
        storedClientPathForSave(
            _fs, c, prior_path, &prior_path_len);
    success = success
        && (file.write(&persisted_path.encoded_path_len, 1) == 1);
    success = success
        && (file.write(persisted_path.path, MAX_PATH_SIZE) == MAX_PATH_SIZE);
    success = success && (file.write(c->shared_secret, PUB_KEY_SIZE) == PUB_KEY_SIZE);
    success = success && (file.write((uint8_t *)&c->alt_path_len, 1) == 1);
    success = success && (file.write(c->alt_path, 64) == 64);

  #if !defined(NRF52_PLATFORM)
    if (success) {
      expected_size += 32 + 1 + 4 + 2 + 1 + 64 + PUB_KEY_SIZE + 1 + 64;
      contacts_crc = mesh::updateClientACLCRC(contacts_crc, c->id.pub_key, 32);
      contacts_crc = mesh::updateClientACLCRC(contacts_crc, &c->permissions, 1);
      contacts_crc = mesh::updateClientACLCRC(
          contacts_crc, (uint8_t*)&c->extra.room.sync_since, 4);
      contacts_crc = mesh::updateClientACLCRC(contacts_crc, unused, sizeof(unused));
      contacts_crc = mesh::updateClientACLCRC(
          contacts_crc, &persisted_path.encoded_path_len, 1);
      contacts_crc = mesh::updateClientACLCRC(
          contacts_crc, persisted_path.path, MAX_PATH_SIZE);
      contacts_crc = mesh::updateClientACLCRC(contacts_crc, c->shared_secret, PUB_KEY_SIZE);
      contacts_crc = mesh::updateClientACLCRC(contacts_crc, &c->alt_path_len, 1);
      contacts_crc = mesh::updateClientACLCRC(contacts_crc, c->alt_path, 64);
    }
  #endif
  }
#if !defined(NRF52_PLATFORM)
  if (success) {
    const uint32_t final_crc = contacts_crc ^ 0xFFFFFFFFUL;
    success = file.write(mesh::CLIENT_ACL_CRC_MAGIC,
                         sizeof(mesh::CLIENT_ACL_CRC_MAGIC))
            == sizeof(mesh::CLIENT_ACL_CRC_MAGIC)
        && file.write((const uint8_t*)&final_crc, sizeof(final_crc))
            == sizeof(final_crc);
  }
#endif
#if defined(NRF52_PLATFORM)
  success = file.commit(success);
  if (!success) {
    MESH_DEBUG_PRINTLN("ERROR: ClientACL::save atomic commit failed");
  }
#else
  file.close();
  if (success) {
    success = verifyContactsFile(
        _fs, mesh::CLIENT_ACL_TEMP_PATH, clients, num_clients,
        filter, expected_size);
  }
  if (!success) {
    mesh::publishVerifiedClientACLTemp(_fs, false);
    MESH_DEBUG_PRINTLN("ERROR: ClientACL::save close/readback failed");
    return false;
  }
  success = mesh::publishVerifiedClientACLTemp(
      _fs, true, validateContactsFileIntegrity);
  if (!success) {
    MESH_DEBUG_PRINTLN("ERROR: ClientACL::save atomic publish failed");
  }
#endif
  return success;
}

bool ClientACL::clear() {
  if (!_fs) return false; // no filesystem, nothing to clear
  if (_fs->exists("/s_contacts")) {
    _fs->remove("/s_contacts");
  }
  if (_fs->exists("/s_contacts.tmp")) _fs->remove("/s_contacts.tmp");
  if (_fs->exists("/s_contacts.bak")) _fs->remove("/s_contacts.bak");
  const bool files_cleared = !_fs->exists("/s_contacts")
      && !_fs->exists("/s_contacts.tmp")
      && !_fs->exists("/s_contacts.bak");
  memset(clients, 0, sizeof(clients));
  num_clients = 0;
  return files_cleared;
}

ClientInfo* ClientACL::getClient(const uint8_t* pubkey, int key_len) {
  if (pubkey == NULL || key_len <= 0 || key_len > PUB_KEY_SIZE) return NULL;
  for (int i = 0; i < num_clients; i++) {
    if (memcmp(pubkey, clients[i].id.pub_key, key_len) == 0) return &clients[i];  // already known
  }
  return NULL;  // not found
}

ClientInfo* ClientACL::putClient(const mesh::Identity& id, uint8_t init_perms) {
  uint32_t min_time = 0xFFFFFFFF;
  ClientInfo* oldest = NULL;
  for (int i = 0; i < num_clients; i++) {
    if (id.matches(clients[i].id)) return &clients[i];  // already known
    if (!clients[i].isProtectedManager() && clients[i].last_activity < min_time) {
      oldest = &clients[i];
      min_time = oldest->last_activity;
    }
  }

  ClientInfo* c;
  if (num_clients < MAX_CLIENTS) {
    c = &clients[num_clients++];
  } else {
    if (oldest == NULL) return NULL;  // every entry is protected
    c = oldest;  // evict least active contact
  }
  memset(c, 0, sizeof(*c));
  c->permissions = init_perms;
  c->id = id;
  c->out_path_len = OUT_PATH_UNKNOWN;
  c->out_path_is_persistable = true;
  c->alt_path_len = OUT_PATH_UNKNOWN;
  c->observed_path_len = OUT_PATH_UNKNOWN;
  return c;
}

bool ClientACL::applyPermissions(const mesh::LocalIdentity& self_id, const uint8_t* pubkey, int key_len, uint8_t perms) {
  if (pubkey == NULL || key_len <= 0 || key_len > PUB_KEY_SIZE) return false;
  ClientInfo* c;
  if ((perms & PERM_ACL_ROLE_MASK) == PERM_ACL_GUEST) {  // guest role is not persisted in contacts
    c = getClient(pubkey, key_len);
    if (c == NULL) return false;   // partial pubkey not found

    num_clients--;   // delete from contacts[]
    int i = c - clients;
    while (i < num_clients) {
      clients[i] = clients[i + 1];
      i++;
    }
  } else {
    if (key_len != PUB_KEY_SIZE) return false;   // need complete pubkey when adding/modifying

    mesh::Identity id(pubkey);
    c = putClient(id, 0);
    if (c == NULL) return false;

    c->permissions = perms;  // update their permissions
    self_id.calcSharedSecret(c->shared_secret, pubkey);
  }
  return true;
}
