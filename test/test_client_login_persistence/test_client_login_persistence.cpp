#include <gtest/gtest.h>

#include <map>
#include <set>
#include <string>
#include <utility>
#include <vector>

#include <helpers/ClientLoginPersistence.h>
#include <helpers/LazyPersistence.h>

namespace {

static constexpr uint8_t ROLE_MASK = 0x07;
static constexpr uint8_t ROLE_GUEST = 0x00;
static constexpr uint8_t ROLE_READ_ONLY = 0x01;
static constexpr uint8_t ROLE_READ_WRITE = 0x02;
static constexpr uint8_t ROLE_ADMIN = 0x03;
static constexpr uint8_t UNKNOWN_PATH = 0xFF;
static constexpr uint8_t FORCE_FLOOD_PATH = 0xFE;

struct ClientState {
  uint8_t permissions = ROLE_ADMIN;
  uint8_t out_path_len = UNKNOWN_PATH;
  bool out_path_is_persistable = true;
  uint8_t shared_secret[4] = {1, 2, 3, 4};
  uint32_t last_timestamp = 10;
  uint32_t last_activity = 20;
};

bool applyLogin(ClientState& client, bool existed, const uint8_t* secret,
                uint8_t role = ROLE_ADMIN, bool is_flood = false) {
  return mesh::applySuccessfulClientLogin(
      client, existed, role, ROLE_MASK, secret,
      100, 200, is_flood, UNKNOWN_PATH);
}

bool loginNeedsSave(bool existed, uint8_t previous, uint8_t next,
                    bool changed) {
  return mesh::successfulClientLoginNeedsPersistence(
      existed, previous, next, ROLE_MASK, 0, changed);
}

TEST(ClientLoginPersistence, UnchangedAdminLoginRefreshesOnlyTransientState) {
  ClientState client;
  const uint8_t same_secret[4] = {1, 2, 3, 4};

  EXPECT_FALSE(applyLogin(client, true, same_secret));
  EXPECT_EQ(client.last_timestamp, 100u);
  EXPECT_EQ(client.last_activity, 200u);
  EXPECT_EQ(client.permissions, ROLE_ADMIN);
  EXPECT_EQ(client.out_path_len, UNKNOWN_PATH);
  EXPECT_EQ(memcmp(client.shared_secret, same_secret, 4), 0);
}

TEST(ClientLoginPersistence, NewAdminNeedsPersistenceAndArmsFirstWrite) {
  ClientState client;
  const uint8_t same_secret[4] = {1, 2, 3, 4};
  unsigned long pending = 0;

  const bool changed = applyLogin(client, false, same_secret);
  EXPECT_TRUE(changed);
  EXPECT_TRUE(loginNeedsSave(false, 0, ROLE_ADMIN, changed));
  EXPECT_TRUE(mesh::armFirstLazyPersistence(
      pending, 5000,
      loginNeedsSave(false, 0, ROLE_ADMIN, changed)));
  EXPECT_EQ(pending, 5000UL);
  EXPECT_EQ(client.last_timestamp, 100u);
  EXPECT_EQ(client.last_activity, 200u);
}

TEST(ClientLoginPersistence, NewGuestStaysRuntimeOnly) {
  ClientState client;
  client.permissions = 0;
  const uint8_t same_secret[4] = {1, 2, 3, 4};
  unsigned long pending = 0;

  const bool changed = applyLogin(client, false, same_secret, 0);
  EXPECT_TRUE(changed);
  EXPECT_EQ(client.permissions & ROLE_MASK, 0);
  EXPECT_FALSE(loginNeedsSave(false, 0, 0, changed));
  EXPECT_FALSE(mesh::armFirstLazyPersistence(
      pending, 5000, loginNeedsSave(false, 0, 0, changed)));
  EXPECT_EQ(pending, 0UL);
}

TEST(ClientLoginPersistence, GuestPromotionToAdminNeedsPersistence) {
  ClientState client;
  client.permissions = 0;
  const uint8_t same_secret[4] = {1, 2, 3, 4};

  const bool changed = applyLogin(client, true, same_secret, ROLE_ADMIN);
  EXPECT_TRUE(changed);
  EXPECT_TRUE(loginNeedsSave(true, 0, ROLE_ADMIN, changed));
  EXPECT_EQ(client.permissions, ROLE_ADMIN);
}

TEST(ClientLoginPersistence, AdminDowngradeToGuestRemovesPersistedEntry) {
  ClientState client;
  client.permissions = 0x83;
  const uint8_t same_secret[4] = {1, 2, 3, 4};

  const bool changed = applyLogin(client, true, same_secret, 0);
  EXPECT_TRUE(changed);
  EXPECT_EQ(client.permissions & ROLE_MASK, 0);
  EXPECT_EQ(client.permissions, 0x80);
  EXPECT_FALSE(mesh::clientRoleIsPersisted(
      client.permissions, ROLE_MASK, 0));
  EXPECT_TRUE(loginNeedsSave(true, ROLE_ADMIN, 0, changed));
}

TEST(ClientLoginPersistence, GuestUpperPermissionBitsNeverMakeItPersistent) {
  EXPECT_FALSE(mesh::clientRoleIsPersisted(0x00, ROLE_MASK, 0));
  EXPECT_FALSE(mesh::clientRoleIsPersisted(0x80, ROLE_MASK, 0));
  EXPECT_TRUE(mesh::clientRoleIsPersisted(0x81, ROLE_MASK, 0));
}

TEST(ClientLoginPersistence, PreauthorizedLoginStillRefreshesReplayState) {
  ClientState client;
  client.permissions = 1;
  const uint8_t same_secret[4] = {1, 2, 3, 4};

  EXPECT_FALSE(applyLogin(client, true, same_secret, 1));
  EXPECT_EQ(client.permissions, 1);
  EXPECT_EQ(client.last_timestamp, 100u);
  EXPECT_EQ(client.last_activity, 200u);
}

TEST(ClientLoginPersistence, PersistedRoleOrSecretChangesNeedPersistence) {
  const uint8_t same_secret[4] = {1, 2, 3, 4};
  const uint8_t changed_secret[4] = {4, 3, 2, 1};

  ClientState role_changed;
  role_changed.permissions = 0xA1;
  EXPECT_TRUE(applyLogin(role_changed, true, same_secret, 0xFB));
  EXPECT_EQ(role_changed.permissions, 0xA3);

  ClientState secret_changed;
  EXPECT_TRUE(applyLogin(secret_changed, true, changed_secret));
  EXPECT_EQ(memcmp(secret_changed.shared_secret, changed_secret, 4), 0);
}

TEST(ClientLoginPersistence, FloodPathResetPersistsOnlyWhenValueChanges) {
  const uint8_t same_secret[4] = {1, 2, 3, 4};

  ClientState known_path;
  known_path.out_path_len = 2;
  EXPECT_TRUE(applyLogin(
      known_path, true, same_secret, ROLE_ADMIN, true));
  EXPECT_EQ(known_path.out_path_len, UNKNOWN_PATH);
  EXPECT_TRUE(known_path.out_path_is_persistable);

  ClientState already_unknown;
  EXPECT_FALSE(applyLogin(
      already_unknown, true, same_secret, ROLE_ADMIN, true));
}

TEST(ClientLoginPersistence, FloodLoginNeverDestroysForceFlood) {
  const uint8_t same_secret[4] = {1, 2, 3, 4};
  ClientState client;
  client.out_path_len = FORCE_FLOOD_PATH;

  const bool reset_out_path = client.out_path_len != FORCE_FLOOD_PATH;
  EXPECT_FALSE(mesh::applySuccessfulClientLogin(
      client, true, ROLE_ADMIN, ROLE_MASK, same_secret,
      100, 200, reset_out_path, UNKNOWN_PATH));
  EXPECT_EQ(client.out_path_len, FORCE_FLOOD_PATH);
  EXPECT_TRUE(client.out_path_is_persistable);
}

TEST(ClientLoginPersistence, ReplayValidationPrecedesAllocation) {
  EXPECT_FALSE(mesh::isFreshClientLoginTimestamp(false, 0, 0));
  EXPECT_TRUE(mesh::isFreshClientLoginTimestamp(false, 1, 0));

  EXPECT_FALSE(mesh::isFreshClientLoginTimestamp(true, 0, 50));
  EXPECT_FALSE(mesh::isFreshClientLoginTimestamp(true, 49, 50));
  EXPECT_FALSE(mesh::isFreshClientLoginTimestamp(true, 50, 50));
  EXPECT_TRUE(mesh::isFreshClientLoginTimestamp(true, 51, 50));
}

TEST(ClientLoginPersistence, DurableReservationSurvivesReboot) {
  const auto first = mesh::planClientLoginReplay(0, 0, 100);
  ASSERT_TRUE(first.fresh);
  ASSERT_TRUE(first.reservation_needed);
  EXPECT_EQ(first.reservation_ceiling, 160u);

  // Once this boot has accepted 100, reconnects advance against the exact RAM
  // floor and consume the already durable reservation without another write.
  const auto reconnect = mesh::planClientLoginReplay(
      100, first.reservation_ceiling, 101);
  EXPECT_TRUE(reconnect.fresh);
  EXPECT_FALSE(reconnect.reservation_needed);

  // A reboot has no exact RAM floor.  It deliberately skips the unused tail,
  // so neither accepted timestamp can become fresh again.
  const auto replay_after_reboot = mesh::planClientLoginReplay(
      0, first.reservation_ceiling, 101);
  EXPECT_FALSE(replay_after_reboot.fresh);
  const auto after_reserved_tail = mesh::planClientLoginReplay(
      0, first.reservation_ceiling, 161);
  EXPECT_TRUE(after_reserved_tail.fresh);
  EXPECT_TRUE(after_reserved_tail.reservation_needed);
  EXPECT_EQ(after_reserved_tail.reservation_ceiling, 221u);
}

TEST(ClientLoginPersistence, EvictionCannotEraseReplayFloor) {
  const uint32_t tombstone_ceiling = 460;

  // Eviction removes the live ClientInfo and therefore its exact timestamp.
  // The absent runtime floor must fall back to the per-identity tombstone.
  EXPECT_FALSE(mesh::planClientLoginReplay(
      0, tombstone_ceiling, 400).fresh);
  EXPECT_FALSE(mesh::planClientLoginReplay(
      0, tombstone_ceiling, tombstone_ceiling).fresh);
  EXPECT_TRUE(mesh::planClientLoginReplay(
      0, tombstone_ceiling, tombstone_ceiling + 1).fresh);
}

TEST(ClientLoginPersistence, RevokedAdminReplayUsesTombstoneNotRole) {
  const uint32_t revoked_admin_floor = 1060;

  // Role removal is intentionally absent from the planner: replay history is
  // keyed by public identity and outlives the ACL record/permissions.
  const uint8_t current_role = 0;  // guest/deleted
  (void)current_role;
  EXPECT_FALSE(mesh::planClientLoginReplay(
      0, revoked_admin_floor, 1000).fresh);
  EXPECT_FALSE(mesh::planClientLoginReplay(
      0, revoked_admin_floor, 1001).fresh);
}

TEST(ClientLoginPersistence, ReservationSaturatesWithoutWrapping) {
  EXPECT_EQ(mesh::reserveClientLoginTimestamp(UINT32_MAX - 10, 60),
            UINT32_MAX);
  EXPECT_FALSE(mesh::planClientLoginReplay(
      UINT32_MAX, UINT32_MAX, UINT32_MAX).fresh);
}

TEST(ClientLoginPersistence, GuestAndReadOnlyDoNotAllocateDurableIdentity) {
  EXPECT_FALSE(mesh::clientLoginRoleNeedsDurableReplay(
      ROLE_GUEST, ROLE_MASK, ROLE_GUEST, ROLE_READ_ONLY));
  EXPECT_FALSE(mesh::clientLoginRoleNeedsDurableReplay(
      ROLE_READ_ONLY, ROLE_MASK, ROLE_GUEST, ROLE_READ_ONLY));
  // Unrelated upper permission bits do not turn a low-trust role into a
  // durable replay-store consumer.
  EXPECT_FALSE(mesh::clientLoginRoleNeedsDurableReplay(
      0x81, ROLE_MASK, ROLE_GUEST, ROLE_READ_ONLY));

  const auto first_guest = mesh::planClientLoginReplay(
      0, 0, 100);
  EXPECT_TRUE(first_guest.fresh);
  EXPECT_TRUE(first_guest.reservation_needed);

  // A full durable store is irrelevant because policy selects no write for a
  // missing low-trust identity.
  EXPECT_EQ(mesh::selectClientLoginReplayReservation(
      first_guest.reservation_needed, false, false),
      mesh::ClientLoginReplayReservationAction::None);
}

TEST(ClientLoginPersistence, LowTrustLoginStillEnforcesExistingFloors) {
  const uint32_t privileged_tombstone = 460;
  EXPECT_FALSE(mesh::planClientLoginReplay(
      0, privileged_tombstone, 400).fresh);
  EXPECT_FALSE(mesh::planClientLoginReplay(
      0, privileged_tombstone, privileged_tombstone).fresh);

  const auto newer = mesh::planClientLoginReplay(
      0, privileged_tombstone, privileged_tombstone + 1);
  EXPECT_TRUE(newer.fresh);
  EXPECT_TRUE(newer.reservation_needed);
  EXPECT_EQ(mesh::selectClientLoginReplayReservation(
      newer.reservation_needed, true, false),
      mesh::ClientLoginReplayReservationAction::UpdateExisting);

  // Once admitted, the exact RAM floor still rejects same-boot replay even
  // for a missing low-trust identity which deliberately performs no write.
  EXPECT_FALSE(mesh::planClientLoginReplay(
      privileged_tombstone + 1, privileged_tombstone,
      privileged_tombstone + 1).fresh);
}

TEST(ClientLoginPersistence, FullStoreFailsOnlyNewPrivilegedIdentity) {
  for (uint8_t role : {ROLE_READ_WRITE, ROLE_ADMIN, (uint8_t)4, (uint8_t)5}) {
    EXPECT_TRUE(mesh::clientLoginRoleNeedsDurableReplay(
        role, ROLE_MASK, ROLE_GUEST, ROLE_READ_ONLY));
  }

  const auto privileged = mesh::planClientLoginReplay(0, 0, 100);
  ASSERT_TRUE(privileged.fresh);
  ASSERT_TRUE(privileged.reservation_needed);
  EXPECT_EQ(mesh::selectClientLoginReplayReservation(
      privileged.reservation_needed, false, true),
      mesh::ClientLoginReplayReservationAction::CreateNew);
  EXPECT_FALSE(mesh::clientLoginReplayCanInsert(
      mesh::MAX_CLIENT_LOGIN_REPLAY_IDENTITIES));
  EXPECT_TRUE(mesh::clientLoginReplayCanInsert(
      mesh::MAX_CLIENT_LOGIN_REPLAY_IDENTITIES - 1));

  EXPECT_EQ(mesh::selectClientLoginReplayReservation(
      privileged.reservation_needed, true, true),
      mesh::ClientLoginReplayReservationAction::UpdateExisting);
  EXPECT_EQ(mesh::selectClientLoginReplayReservation(
      privileged.reservation_needed, true, false),
      mesh::ClientLoginReplayReservationAction::UpdateExisting);
  EXPECT_EQ(mesh::selectClientLoginReplayReservation(
      privileged.reservation_needed, false, false),
      mesh::ClientLoginReplayReservationAction::None);
  EXPECT_EQ(mesh::selectClientLoginReplayReservation(
      false, false, true),
      mesh::ClientLoginReplayReservationAction::None);
}

std::vector<uint8_t> replayImage(
    const std::vector<std::pair<std::vector<uint8_t>, uint32_t> >& records) {
  std::vector<uint8_t> image;
  uint32_t crc = 0xFFFFFFFFUL;
  for (const auto& record : records) {
    EXPECT_EQ(record.first.size(), 32u);
    image.insert(image.end(), record.first.begin(), record.first.end());
    crc = mesh::updateClientLoginReplayCRC(
        crc, record.first.data(), record.first.size());
    const uint8_t* floor =
        reinterpret_cast<const uint8_t*>(&record.second);
    image.insert(image.end(), floor, floor + sizeof(record.second));
    crc = mesh::updateClientLoginReplayCRC(
        crc, floor, sizeof(record.second));
  }
  image.insert(image.end(), mesh::CLIENT_LOGIN_REPLAY_MAGIC,
               mesh::CLIENT_LOGIN_REPLAY_MAGIC + 4);
  const uint32_t final_crc = crc ^ 0xFFFFFFFFUL;
  const uint8_t* trailer =
      reinterpret_cast<const uint8_t*>(&final_crc);
  image.insert(image.end(), trailer, trailer + sizeof(final_crc));
  return image;
}

TEST(ClientLoginPersistence, ReplayImageIntegrityRejectsTornOrChangedState) {
  std::vector<uint8_t> key(32, 0xA5);
  std::vector<uint8_t> image = replayImage({{key, 160}});
  ASSERT_TRUE(mesh::validateClientLoginReplayImage(
      image.data(), image.size()));

  std::vector<uint8_t> changed = image;
  changed[32] ^= 1;
  EXPECT_FALSE(mesh::validateClientLoginReplayImage(
      changed.data(), changed.size()));
  image.pop_back();
  EXPECT_FALSE(mesh::validateClientLoginReplayImage(
      image.data(), image.size()));
}

class ReplayFakeFilesystem {
public:
  std::set<std::string> files;
  std::set<std::string> valid_files;
  std::string fail_from;
  std::string fail_to;
  std::string fail_remove;

  bool exists(const char* path) const {
    return files.count(path) != 0;
  }
  bool remove(const char* path) {
    if (fail_remove == path) return false;
    valid_files.erase(path);
    return files.erase(path) != 0;
  }
  bool rename(const char* from, const char* to) {
    if (fail_from == from && fail_to == to) return false;
    if (!exists(from) || exists(to)) return false;
    files.erase(from);
    files.insert(to);
    if (valid_files.erase(from) != 0) valid_files.insert(to);
    return true;
  }
};

bool replayFileIsValid(ReplayFakeFilesystem* fs, const char* path) {
  return fs->valid_files.count(path) != 0;
}

TEST(ClientLoginPersistence, InterruptedReplayPublishRestoresOldFloorImage) {
  ReplayFakeFilesystem fs;
  fs.files = {
      mesh::CLIENT_LOGIN_REPLAY_PRIMARY_PATH,
      mesh::CLIENT_LOGIN_REPLAY_TEMP_PATH,
  };
  fs.valid_files = {
      mesh::CLIENT_LOGIN_REPLAY_PRIMARY_PATH,
      mesh::CLIENT_LOGIN_REPLAY_TEMP_PATH,
  };
  fs.fail_from = mesh::CLIENT_LOGIN_REPLAY_TEMP_PATH;
  fs.fail_to = mesh::CLIENT_LOGIN_REPLAY_PRIMARY_PATH;

  EXPECT_FALSE(mesh::publishClientLoginReplayTemp(
      &fs, true, replayFileIsValid));
  EXPECT_TRUE(fs.exists(mesh::CLIENT_LOGIN_REPLAY_PRIMARY_PATH));
  EXPECT_TRUE(fs.valid_files.count(
      mesh::CLIENT_LOGIN_REPLAY_PRIMARY_PATH) != 0);
  EXPECT_FALSE(fs.exists(mesh::CLIENT_LOGIN_REPLAY_BACKUP_PATH));
}

TEST(ClientLoginPersistence, PublishedReplaySurvivesBackupCleanupFailure) {
  ReplayFakeFilesystem fs;
  fs.files = {
      mesh::CLIENT_LOGIN_REPLAY_PRIMARY_PATH,
      mesh::CLIENT_LOGIN_REPLAY_TEMP_PATH,
  };
  fs.valid_files = {
      mesh::CLIENT_LOGIN_REPLAY_PRIMARY_PATH,
      mesh::CLIENT_LOGIN_REPLAY_TEMP_PATH,
  };
  fs.fail_remove = mesh::CLIENT_LOGIN_REPLAY_BACKUP_PATH;

  EXPECT_TRUE(mesh::publishClientLoginReplayTemp(
      &fs, true, replayFileIsValid));
  EXPECT_TRUE(fs.exists(mesh::CLIENT_LOGIN_REPLAY_PRIMARY_PATH));
  EXPECT_TRUE(fs.valid_files.count(
      mesh::CLIENT_LOGIN_REPLAY_PRIMARY_PATH) != 0);
  EXPECT_TRUE(fs.exists(mesh::CLIENT_LOGIN_REPLAY_BACKUP_PATH));

  fs.fail_remove.clear();
  EXPECT_TRUE(mesh::recoverClientLoginReplayFiles(
      &fs, replayFileIsValid));
  EXPECT_TRUE(fs.exists(mesh::CLIENT_LOGIN_REPLAY_PRIMARY_PATH));
  EXPECT_FALSE(fs.exists(mesh::CLIENT_LOGIN_REPLAY_BACKUP_PATH));
}

TEST(ClientLoginPersistence, ValidReplayLoadsWhenCleanupCannotFinish) {
  ReplayFakeFilesystem fs;
  fs.files = {
      mesh::CLIENT_LOGIN_REPLAY_PRIMARY_PATH,
      mesh::CLIENT_LOGIN_REPLAY_TEMP_PATH,
      mesh::CLIENT_LOGIN_REPLAY_BACKUP_PATH,
  };
  fs.valid_files = {mesh::CLIENT_LOGIN_REPLAY_PRIMARY_PATH};
  fs.fail_remove = mesh::CLIENT_LOGIN_REPLAY_BACKUP_PATH;

  EXPECT_TRUE(mesh::recoverClientLoginReplayFiles(
      &fs, replayFileIsValid));
  EXPECT_TRUE(fs.exists(mesh::CLIENT_LOGIN_REPLAY_PRIMARY_PATH));
  EXPECT_FALSE(fs.exists(mesh::CLIENT_LOGIN_REPLAY_TEMP_PATH));
  EXPECT_TRUE(fs.exists(mesh::CLIENT_LOGIN_REPLAY_BACKUP_PATH));
}

TEST(ClientLoginPersistence, BootRecoveryRestoresValidReplayBackup) {
  ReplayFakeFilesystem fs;
  fs.files = {
      mesh::CLIENT_LOGIN_REPLAY_PRIMARY_PATH,
      mesh::CLIENT_LOGIN_REPLAY_BACKUP_PATH,
      mesh::CLIENT_LOGIN_REPLAY_TEMP_PATH,
  };
  fs.valid_files = {mesh::CLIENT_LOGIN_REPLAY_BACKUP_PATH};

  EXPECT_TRUE(mesh::recoverClientLoginReplayFiles(
      &fs, replayFileIsValid));
  EXPECT_TRUE(fs.exists(mesh::CLIENT_LOGIN_REPLAY_PRIMARY_PATH));
  EXPECT_TRUE(fs.valid_files.count(
      mesh::CLIENT_LOGIN_REPLAY_PRIMARY_PATH) != 0);
  EXPECT_FALSE(fs.exists(mesh::CLIENT_LOGIN_REPLAY_BACKUP_PATH));
  EXPECT_FALSE(fs.exists(mesh::CLIENT_LOGIN_REPLAY_TEMP_PATH));
}

TEST(ClientLoginPersistence, CorruptReplayStateFailsClosed) {
  ReplayFakeFilesystem fs;
  fs.files = {mesh::CLIENT_LOGIN_REPLAY_PRIMARY_PATH};

  EXPECT_FALSE(mesh::recoverClientLoginReplayFiles(
      &fs, replayFileIsValid));
  EXPECT_TRUE(fs.exists(mesh::CLIENT_LOGIN_REPLAY_PRIMARY_PATH));
}

} // namespace

int main(int argc, char** argv) {
  ::testing::InitGoogleTest(&argc, argv);
  return RUN_ALL_TESTS();
}
