#include <gtest/gtest.h>

#include <helpers/WiFiOtaSeederPolicy.h>
#include <helpers/WiFiOtaSeederStatus.h>

namespace Policy = WiFiOtaSeederPolicy;

TEST(WiFiOtaSeederPolicy, ListenerTracksNetworkAvailability) {
  EXPECT_EQ(Policy::ListenerAction::Keep, Policy::listenerAction(false, false));
  EXPECT_EQ(Policy::ListenerAction::Start, Policy::listenerAction(true, false));
  EXPECT_EQ(Policy::ListenerAction::Keep, Policy::listenerAction(true, true));
  EXPECT_EQ(Policy::ListenerAction::Stop, Policy::listenerAction(false, true));
}

TEST(WiFiOtaSeederPolicy, ExistingSerialFolderCannotBeReplacedByTcp) {
  EXPECT_TRUE(Policy::canAttachTcpFolder(false, false));
  EXPECT_FALSE(Policy::canAttachTcpFolder(true, false));
  EXPECT_TRUE(Policy::canAttachTcpFolder(true, true));
}

TEST(WiFiOtaSeederPolicy, DetectsExternalDetachOfTcpFolder) {
  EXPECT_FALSE(Policy::tcpFolderWasDetached(false, false));
  EXPECT_FALSE(Policy::tcpFolderWasDetached(false, true));
  EXPECT_FALSE(Policy::tcpFolderWasDetached(true, true));
  EXPECT_TRUE(Policy::tcpFolderWasDetached(true, false));
}

TEST(WiFiOtaSeederStatus, OmitsInactiveListener) {
  char reply[64] = "> connected";
  EXPECT_FALSE(WiFiOtaSeederStatus::append(
      reply, sizeof(reply), false, false, 5001));
  EXPECT_STREQ("> connected", reply);
}

TEST(WiFiOtaSeederStatus, ReportsListeningPort) {
  char reply[64] = "> connected";
  EXPECT_TRUE(WiFiOtaSeederStatus::append(
      reply, sizeof(reply), true, false, 5001));
  EXPECT_STREQ("> connected, OTA TCP 5001: listening", reply);
}

TEST(WiFiOtaSeederStatus, ReportsAttachedClient) {
  char reply[64] = "> connected";
  EXPECT_TRUE(WiFiOtaSeederStatus::append(
      reply, sizeof(reply), true, true, 5001));
  EXPECT_STREQ("> connected, OTA TCP 5001: client connected", reply);
}

TEST(WiFiOtaSeederStatus, TruncatesSafelyWhenReplyIsFull) {
  char reply[20] = "> connected";
  EXPECT_FALSE(WiFiOtaSeederStatus::append(
      reply, sizeof(reply), true, false, 5001));
  EXPECT_EQ('\0', reply[sizeof(reply) - 1]);
}

int main(int argc, char** argv) {
  ::testing::InitGoogleTest(&argc, argv);
  return RUN_ALL_TESTS();
}
