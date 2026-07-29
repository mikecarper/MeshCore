#include <gtest/gtest.h>

#include <helpers/WiFiOtaSeederPolicy.h>

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

int main(int argc, char** argv) {
  ::testing::InitGoogleTest(&argc, argv);
  return RUN_ALL_TESTS();
}
