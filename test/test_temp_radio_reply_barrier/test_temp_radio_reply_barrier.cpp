#include <gtest/gtest.h>

#include <helpers/TempRadioReplyBarrier.h>

TEST(TempRadioReplyBarrier, WaitsForTheExactReplyPacket) {
  mesh::TempRadioReplyBarrier barrier;
  int reply_packet = 1;
  int unrelated_packet = 2;

  EXPECT_FALSE(barrier.waiting());
  barrier.arm(&reply_packet);
  EXPECT_TRUE(barrier.waiting());

  EXPECT_FALSE(barrier.complete(&unrelated_packet));
  EXPECT_TRUE(barrier.waiting());
  EXPECT_TRUE(barrier.complete(&reply_packet));
  EXPECT_FALSE(barrier.waiting());
}

TEST(TempRadioReplyBarrier, ExactTransmitFailureReleasesForFailClosedCleanup) {
  mesh::TempRadioReplyBarrier barrier;
  int reply_packet = 1;

  barrier.arm(&reply_packet);
  EXPECT_TRUE(barrier.fail(&reply_packet));
  EXPECT_FALSE(barrier.waiting());
  EXPECT_FALSE(barrier.fail(&reply_packet));
}

TEST(TempRadioReplyBarrier, UntrackedAlternateCannotResolveAuthoritativeReply) {
  mesh::TempRadioReplyBarrier barrier;
  int primary_reply = 1;
  int alternate_reply = 2;

  barrier.arm(&primary_reply);

  // TempRadio mutation acknowledgements deliberately queue only the primary
  // copy, without an alternate-path copy or a transport retry. If an
  // obsolete/foreign packet callback arrives anyway, it must not release or
  // fail the authoritative handoff.
  EXPECT_FALSE(barrier.complete(&alternate_reply));
  EXPECT_FALSE(barrier.fail(&alternate_reply));
  EXPECT_TRUE(barrier.waiting());

  EXPECT_TRUE(barrier.complete(&primary_reply));
  EXPECT_FALSE(barrier.waiting());
}

TEST(TempRadioReplyBarrier, ClearCancelsAnObsoleteHandoff) {
  mesh::TempRadioReplyBarrier barrier;
  int old_reply = 1;
  int new_reply = 2;

  barrier.arm(&old_reply);
  barrier.clear();
  EXPECT_FALSE(barrier.complete(&old_reply));

  barrier.arm(&new_reply);
  EXPECT_FALSE(barrier.complete(&old_reply));
  EXPECT_TRUE(barrier.complete(&new_reply));
}

int main(int argc, char** argv) {
  ::testing::InitGoogleTest(&argc, argv);
  return RUN_ALL_TESTS();
}
