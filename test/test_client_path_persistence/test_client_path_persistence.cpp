#include <gtest/gtest.h>

#include <helpers/ClientPathPersistence.h>
#include <helpers/LazyPersistence.h>

namespace {

static constexpr uint8_t UNKNOWN_PATH = 0xFF;
static constexpr uint8_t FORCE_FLOOD_PATH = 0xFE;

struct ClientState {
  uint8_t permissions = 3;
  uint8_t out_path_len = UNKNOWN_PATH;
  uint8_t out_path[64] = {};
  bool out_path_is_persistable = true;
};

TEST(ClientPathPersistence, UnknownToLearnedPathNeedsPersistence) {
  ClientState client;
  const uint8_t path[] = {0x10, 0x11, 0x12, 0x13};

  const auto result = mesh::applyReceivedClientPath(
      client, path, 0x42, true, FORCE_FLOOD_PATH);
  EXPECT_TRUE(result.changed);
  EXPECT_TRUE(result.persistence_needed);
  EXPECT_TRUE(client.out_path_is_persistable);
  EXPECT_EQ(client.out_path_len, 0x42);
  EXPECT_EQ(memcmp(client.out_path, path, sizeof(path)), 0);
}

TEST(ClientPathPersistence, SameEncodedLengthAndBytesIsUnchanged) {
  ClientState client;
  client.out_path_len = 0x42;
  const uint8_t path[] = {0x10, 0x11, 0x12, 0x13};
  memcpy(client.out_path, path, sizeof(path));
  client.out_path[10] = 0xA5;  // stale capacity is not part of the path

  const auto result = mesh::applyReceivedClientPath(
      client, path, 0x42, true, FORCE_FLOOD_PATH);
  EXPECT_FALSE(result.changed);
  EXPECT_FALSE(result.persistence_needed);
  EXPECT_TRUE(client.out_path_is_persistable);
  EXPECT_EQ(client.out_path[10], 0xA5);
}

TEST(ClientPathPersistence, SameEncodedLengthDifferentBytesChanges) {
  ClientState client;
  client.out_path_len = 2;
  client.out_path[0] = 0x10;
  client.out_path[1] = 0x11;
  const uint8_t path[] = {0x10, 0x22};

  const auto result = mesh::applyReceivedClientPath(
      client, path, 2, true, FORCE_FLOOD_PATH);
  EXPECT_TRUE(result.changed);
  EXPECT_TRUE(result.persistence_needed);
  EXPECT_EQ(client.out_path[1], 0x22);
}

TEST(ClientPathPersistence, DifferentEncodingChangesEvenAtSameByteLength) {
  ClientState client;
  client.out_path_len = 2;  // two one-byte hashes
  client.out_path[0] = 0x10;
  client.out_path[1] = 0x11;
  const uint8_t path[] = {0x10, 0x11};

  const auto result = mesh::applyReceivedClientPath(
      client, path, 0x41, true, FORCE_FLOOD_PATH); // one two-byte hash
  EXPECT_TRUE(result.changed);
  EXPECT_TRUE(result.persistence_needed);
  EXPECT_EQ(client.out_path_len, 0x41);
}

TEST(ClientPathPersistence, EncodingValidationMatchesPacketPathRules) {
  EXPECT_TRUE(mesh::isValidEncodedClientPathLength(0, 64));
  EXPECT_TRUE(mesh::isValidEncodedClientPathLength(0x5F, 64));
  EXPECT_FALSE(mesh::isValidEncodedClientPathLength(0x61, 64));
  EXPECT_FALSE(mesh::isValidEncodedClientPathLength(0xC0, 64));
}

TEST(ClientPathPersistence, ZeroHopIsARealLogicalPath) {
  ClientState client;

  const auto result = mesh::applyReceivedClientPath(
      client, nullptr, 0, true, FORCE_FLOOD_PATH);
  EXPECT_TRUE(result.changed);
  EXPECT_TRUE(result.persistence_needed);
  EXPECT_EQ(client.out_path_len, 0);

  const auto repeated = mesh::applyReceivedClientPath(
      client, nullptr, 0, true, FORCE_FLOOD_PATH);
  EXPECT_FALSE(repeated.changed);
}

TEST(ClientPathPersistence, ForceFloodIsNeverOverwritten) {
  ClientState client;
  client.out_path_len = FORCE_FLOOD_PATH;
  client.out_path[0] = 0x55;
  const uint8_t path[] = {0x10};

  const auto result = mesh::applyReceivedClientPath(
      client, path, 1, true, FORCE_FLOOD_PATH);
  EXPECT_FALSE(result.changed);
  EXPECT_FALSE(result.persistence_needed);
  EXPECT_EQ(client.out_path_len, FORCE_FLOOD_PATH);
  EXPECT_EQ(client.out_path[0], 0x55);
}

TEST(ClientPathPersistence, NonpersistentClientStillGetsLatestRamPath) {
  ClientState client;
  client.permissions = 0;
  const uint8_t path[] = {0x33};

  const auto result = mesh::applyReceivedClientPath(
      client, path, 1, false, FORCE_FLOOD_PATH);
  EXPECT_TRUE(result.changed);
  EXPECT_FALSE(result.persistence_needed);
  EXPECT_EQ(client.out_path_len, 1);
  EXPECT_EQ(client.out_path[0], 0x33);
  EXPECT_FALSE(client.out_path_is_persistable);
}

TEST(ClientPathPersistence, NoReplayProofForcesRamOnlyPath) {
  ClientState client;
  const uint8_t path[] = {0x44};
  EXPECT_FALSE(mesh::clientPathPersistenceAllowed(true, false));

  const auto result = mesh::applyReceivedClientPath(
      client, path, 1,
      mesh::clientPathPersistenceAllowed(true, false),
      FORCE_FLOOD_PATH);
  EXPECT_TRUE(result.changed);
  EXPECT_FALSE(result.persistence_needed);
  EXPECT_EQ(client.out_path[0], 0x44);
  EXPECT_FALSE(client.out_path_is_persistable);
  EXPECT_EQ(mesh::storedClientPathLength(
      client.out_path_is_persistable, client.out_path_len, UNKNOWN_PATH),
      UNKNOWN_PATH);
}

TEST(ClientPathPersistence, IdenticalProvenPathPromotesRamRouteToDurable) {
  ClientState client;
  const uint8_t path[] = {0x44};
  ASSERT_TRUE(mesh::applyReceivedClientPath(
      client, path, 1, false, FORCE_FLOOD_PATH).changed);
  ASSERT_FALSE(client.out_path_is_persistable);

  const auto promoted = mesh::applyReceivedClientPath(
      client, path, 1, true, FORCE_FLOOD_PATH);
  EXPECT_TRUE(promoted.changed);
  EXPECT_TRUE(promoted.persistence_needed);
  EXPECT_TRUE(client.out_path_is_persistable);
}

TEST(ClientPathPersistence,
     TransientLearnedPathCannotEraseOperatorPathOnUnrelatedSaveReload) {
  ClientState client;
  const uint8_t operator_path[] = {0x11, 0x22};
  client.out_path_len = 2;
  memcpy(client.out_path, operator_path, sizeof(operator_path));
  client.out_path_is_persistable = true;

  // This represents the already-published /s_contacts record.
  uint8_t published_path[64] = {};
  memcpy(published_path, client.out_path, sizeof(published_path));
  uint8_t published_path_len = client.out_path_len;

  const uint8_t learned_path[] = {0x33, 0x44};
  const auto learned = mesh::applyReceivedClientPath(
      client, learned_path, 2, false, FORCE_FLOOD_PATH);
  ASSERT_TRUE(learned.changed);
  ASSERT_FALSE(learned.persistence_needed);
  ASSERT_FALSE(client.out_path_is_persistable);
  ASSERT_EQ(client.out_path[0], 0x33);

  // An unrelated permissions/ACL save must select the prior durable route,
  // not UNKNOWN and not the replay-unproven runtime route.
  const uint8_t empty_path[64] = {};
  const auto selected = mesh::selectStoredClientPath(
      client.out_path_is_persistable,
      client.out_path_len,
      client.out_path,
      true,
      published_path_len,
      published_path,
      UNKNOWN_PATH,
      empty_path);
  EXPECT_EQ(selected.encoded_path_len, 2);
  EXPECT_EQ(memcmp(selected.path, operator_path, sizeof(operator_path)), 0);

  // Emulate reload from that just-written record.
  ClientState reloaded;
  reloaded.out_path_len = selected.encoded_path_len;
  memcpy(reloaded.out_path, selected.path, sizeof(reloaded.out_path));
  reloaded.out_path_is_persistable = true;
  EXPECT_EQ(reloaded.out_path_len, 2);
  EXPECT_EQ(memcmp(reloaded.out_path, operator_path,
                   sizeof(operator_path)), 0);
}

TEST(ClientPathPersistence, TransientPathWithoutPriorRecordSavesUnknown) {
  ClientState client;
  const uint8_t learned_path[] = {0x55};
  ASSERT_TRUE(mesh::applyReceivedClientPath(
      client, learned_path, 1, false, FORCE_FLOOD_PATH).changed);

  const uint8_t empty_path[64] = {};
  const auto selected = mesh::selectStoredClientPath(
      client.out_path_is_persistable,
      client.out_path_len,
      client.out_path,
      false,
      UNKNOWN_PATH,
      empty_path,
      UNKNOWN_PATH,
      empty_path);
  EXPECT_EQ(selected.encoded_path_len, UNKNOWN_PATH);
  EXPECT_EQ(selected.path, empty_path);
}

TEST(ClientPathPersistence, PendingWriteKeepsFirstDeadlineAndLatestRamPath) {
  ClientState client;
  unsigned long pending = 0;
  const uint8_t first[] = {0x11};
  const uint8_t latest[] = {0x22};

  auto result = mesh::applyReceivedClientPath(
      client, first, 1, true, FORCE_FLOOD_PATH);
  EXPECT_TRUE(mesh::armFirstLazyPersistence(
      pending, 5000, result.persistence_needed));

  result = mesh::applyReceivedClientPath(
      client, latest, 1, true, FORCE_FLOOD_PATH);
  EXPECT_FALSE(mesh::armFirstLazyPersistence(
      pending, 9000, result.persistence_needed));
  EXPECT_EQ(pending, 5000UL);
  EXPECT_EQ(client.out_path[0], 0x22);
}

} // namespace

int main(int argc, char** argv) {
  ::testing::InitGoogleTest(&argc, argv);
  return RUN_ALL_TESTS();
}
