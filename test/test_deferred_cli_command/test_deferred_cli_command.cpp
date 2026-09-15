#include <gtest/gtest.h>

#include <helpers/DeferredCliCommand.h>

static const uint8_t FIRST[PUB_KEY_SIZE] = {0x12};
static const uint8_t SECOND[PUB_KEY_SIZE] = {0x77};

TEST(DeferredCliCommand, CopiesAuthenticatedCommandContext) {
  mesh::DeferredCliCommand deferred;
  uint8_t secret[PUB_KEY_SIZE];
  memset(secret, 0x5A, sizeof(secret));
  const char command[] = "del flood.moderation.all";

  uint8_t sender[PUB_KEY_SIZE];
  memcpy(sender, FIRST, sizeof(sender));
  ASSERT_TRUE(deferred.enqueue(sender, 123456U, 2, secret, command,
                               strlen(command), 654321U, 1, 91));
  EXPECT_TRUE(deferred.pending);
  EXPECT_EQ(0, memcmp(FIRST, deferred.client_pub_key, PUB_KEY_SIZE));
  EXPECT_EQ(123456U, deferred.sender_timestamp);
  EXPECT_EQ(654321U, deferred.request_id);
  EXPECT_EQ(mesh::RemoteCliReplyCache::fingerprint(command, strlen(command)),
            deferred.command_fingerprint);
  EXPECT_EQ(2, deferred.path_hash_size);
  EXPECT_EQ(1, deferred.radio_profile);
  EXPECT_EQ(91U, deferred.radio_generation);
  EXPECT_EQ(0, memcmp(secret, deferred.secret, sizeof(secret)));
  EXPECT_STREQ(command, deferred.command);
  EXPECT_TRUE(deferred.matches(FIRST, 654321U, command, strlen(command)));
  EXPECT_FALSE(deferred.matches(SECOND, 654321U, command, strlen(command)));
  EXPECT_FALSE(deferred.matches(FIRST, 654322U, command, strlen(command)));
  EXPECT_FALSE(deferred.matches(FIRST, 654321U, "region save", 11));
  sender[31] = 1;
  EXPECT_FALSE(deferred.matches(sender, 654321U, command, strlen(command)));
  EXPECT_FALSE(deferred.matches(nullptr, 654321U, command, strlen(command)));
  EXPECT_EQ(0, deferred.client_pub_key[31]);

  secret[0] = 0;
  EXPECT_EQ(0x5A, deferred.secret[0]);
}

TEST(DeferredCliCommand, RejectsSecondCommandUntilCleared) {
  mesh::DeferredCliCommand deferred;
  uint8_t secret[PUB_KEY_SIZE] = {};
  const char first[] = "region save";
  const char second[] = "advert";

  ASSERT_TRUE(deferred.enqueue(FIRST, 10U, 1, secret, first, strlen(first)));
  EXPECT_FALSE(deferred.enqueue(SECOND, 11U, 3, secret, second, strlen(second)));
  EXPECT_EQ(0, memcmp(FIRST, deferred.client_pub_key, PUB_KEY_SIZE));
  EXPECT_STREQ(first, deferred.command);

  deferred.clear();
  EXPECT_FALSE(deferred.pending);
  EXPECT_FALSE(deferred.matches(FIRST, 10U, first, strlen(first)));
  EXPECT_EQ(0, deferred.client_pub_key[0]);
  EXPECT_EQ(0, deferred.secret[0]);
  EXPECT_EQ(0U, deferred.request_id);
  EXPECT_EQ(0U, deferred.command_fingerprint);
  EXPECT_EQ(0, deferred.command[0]);
  ASSERT_TRUE(deferred.enqueue(SECOND, 11U, 3, secret, second, strlen(second)));
  EXPECT_STREQ(second, deferred.command);
}

TEST(DeferredCliCommand, RejectsAnOverlongCommand) {
  mesh::DeferredCliCommand deferred;
  uint8_t secret[PUB_KEY_SIZE] = {};
  char command[MAX_PACKET_PAYLOAD + 2];
  memset(command, 'x', sizeof(command));
  command[sizeof(command) - 1] = 0;

  EXPECT_FALSE(deferred.enqueue(FIRST, 1U, 1, secret, command,
                                sizeof(deferred.command)));
  EXPECT_FALSE(deferred.enqueue(nullptr, 1U, 1, secret, "advert", 6));
  EXPECT_FALSE(deferred.pending);
}

TEST(DeferredCliCommand, PreservesWireIdentityAcrossCommandMutation) {
  mesh::DeferredCliCommand deferred;
  mesh::RemoteCliReplyCache replies;
  uint8_t secret[PUB_KEY_SIZE] = {};
  uint8_t sender[PUB_KEY_SIZE] = {0x12};
  const char wire[] = "ADVERT\r\n";
  const auto fingerprint = mesh::RemoteCliReplyCache::fingerprint(wire, strlen(wire));
  ASSERT_TRUE(deferred.enqueue(sender, 100U, 1, secret, wire, strlen(wire), 99U));
  strcpy(deferred.command, "advert");
  EXPECT_EQ(fingerprint, deferred.command_fingerprint);
  ASSERT_TRUE(replies.remember(sender, deferred.request_id,
                               deferred.command_fingerprint, "OK"));
  EXPECT_TRUE(replies.matches(sender, 99U, fingerprint));
  EXPECT_FALSE(replies.matches(sender, 99U,
      mesh::RemoteCliReplyCache::fingerprint("advert", 6)));
  // A rejected enqueue must not overwrite the identity of the current owner.
  EXPECT_FALSE(deferred.enqueue(SECOND, 101U, 1, secret, "reboot", 6, 100U));
  EXPECT_EQ(fingerprint, deferred.command_fingerprint);
  deferred.clear();
  EXPECT_EQ(0U, deferred.command_fingerprint);
}

TEST(DeferredCliCommand, ResolvesFullKeyAfterCompactionAndRejectsReusedSlot) {
  struct Client { struct { uint8_t pub_key[PUB_KEY_SIZE]; } id; };
  struct ACL {
    Client clients[2] = {};
    int count = 2;
    int getNumClients() const { return count; }
    const Client* getClientByIdx(int i) const { return &clients[i]; }
  } acl;
  memcpy(acl.clients[0].id.pub_key, FIRST, PUB_KEY_SIZE);
  memcpy(acl.clients[1].id.pub_key, SECOND, PUB_KEY_SIZE);
  mesh::DeferredCliCommand deferred;
  uint8_t secret[PUB_KEY_SIZE] = {};
  EXPECT_EQ(-1, deferred.findClientIndex(acl));
  ASSERT_TRUE(deferred.enqueue(SECOND, 1, 1, secret, "advert", 6));
  EXPECT_EQ(1, deferred.findClientIndex(acl));
  acl.clients[0] = acl.clients[1];
  acl.count = 1;
  EXPECT_EQ(0, deferred.findClientIndex(acl));
  acl.clients[0].id.pub_key[31] = 1;
  EXPECT_EQ(-1, deferred.findClientIndex(acl));
}

int main(int argc, char** argv) {
  ::testing::InitGoogleTest(&argc, argv);
  return RUN_ALL_TESTS();
}
