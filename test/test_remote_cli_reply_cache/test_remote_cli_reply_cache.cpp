#include <gtest/gtest.h>

#include <helpers/RemoteCliReplyCache.h>

TEST(RemoteCliReplyCache, ReplaysOnlyTheSameAuthenticatedRequest) {
  mesh::RemoteCliReplyCache cache;
  uint8_t alice[PUB_KEY_SIZE] = {};
  uint8_t bob[PUB_KEY_SIZE] = {};
  alice[0] = 0xA1;
  bob[0] = 0xB2;
  const char command[] = "set repeat off";
  const uint32_t fingerprint =
      mesh::RemoteCliReplyCache::fingerprint(command, strlen(command));

  EXPECT_FALSE(cache.matches(alice, 123U, fingerprint));
  ASSERT_TRUE(cache.remember(alice, 123U, fingerprint, "OK"));
  EXPECT_TRUE(cache.matches(alice, 123U, fingerprint));
  EXPECT_STREQ("OK", cache.response());
  EXPECT_TRUE(cache.hasResponse());

  EXPECT_FALSE(cache.matches(bob, 123U, fingerprint));
  EXPECT_FALSE(cache.matches(alice, 124U, fingerprint));
  EXPECT_FALSE(cache.matches(
      alice, 123U,
      mesh::RemoteCliReplyCache::fingerprint("set repeat on", 13)));
}

TEST(RemoteCliReplyCache, OwnsAndReplacesTheRememberedResponse) {
  mesh::RemoteCliReplyCache cache;
  uint8_t first_sender[PUB_KEY_SIZE] = {};
  uint8_t sender[PUB_KEY_SIZE] = {};
  first_sender[0] = 0x11;
  memcpy(sender, first_sender, sizeof(sender));
  char response[] = "first";

  ASSERT_TRUE(cache.remember(sender, 1U, 10U, response));
  response[0] = 'X';
  sender[0] = 0x22;
  EXPECT_STREQ("first", cache.response());

  ASSERT_TRUE(cache.remember(sender, 2U, 20U, "second"));
  EXPECT_FALSE(cache.matches(first_sender, 1U, 10U));
  EXPECT_TRUE(cache.matches(sender, 2U, 20U));
  EXPECT_STREQ("second", cache.response());
}

TEST(RemoteCliReplyCache, EmptyResponseStillMarksRequestComplete) {
  mesh::RemoteCliReplyCache cache;
  uint8_t sender[PUB_KEY_SIZE] = {};

  ASSERT_TRUE(cache.remember(sender, 9U, 99U, ""));
  EXPECT_TRUE(cache.matches(sender, 9U, 99U));
  EXPECT_TRUE(cache.isValid());
  EXPECT_FALSE(cache.hasResponse());
  EXPECT_STREQ("", cache.response());
}

TEST(RemoteCliReplyCache, TruncatesToTheOnAirReplyLimit) {
  mesh::RemoteCliReplyCache cache;
  uint8_t sender[PUB_KEY_SIZE] = {};
  char response[mesh::RemoteCliReplyCache::MAX_REPLY_TEXT + 20];
  memset(response, 'x', sizeof(response));
  response[sizeof(response) - 1] = 0;

  ASSERT_TRUE(cache.remember(sender, 7U, 77U, response));
  EXPECT_EQ(mesh::RemoteCliReplyCache::MAX_REPLY_TEXT,
            strlen(cache.response()));
}

TEST(RemoteCliReplyCache, ClearForgetsTheRequestAndResponse) {
  mesh::RemoteCliReplyCache cache;
  uint8_t sender[PUB_KEY_SIZE] = {};
  ASSERT_TRUE(cache.remember(sender, 1U, 2U, "OK"));

  cache.clear();

  EXPECT_FALSE(cache.isValid());
  EXPECT_FALSE(cache.matches(sender, 1U, 2U));
  EXPECT_STREQ("", cache.response());
}

int main(int argc, char** argv) {
  ::testing::InitGoogleTest(&argc, argv);
  return RUN_ALL_TESTS();
}
