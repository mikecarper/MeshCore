#include <gtest/gtest.h>

#include "helpers/DatagramPayloadLimits.h"

TEST(DatagramPayloadLimits, MeshDatagramPlaintextLimitIncludesWorstCasePadding) {
  EXPECT_EQ(167U, DatagramPayloadLimits::maxPlaintext(184, 2, 16));
}

TEST(DatagramPayloadLimits, RegionReplyLeavesRoomForItsEightBytePrefix) {
  const size_t reply_limit = DatagramPayloadLimits::maxPlaintext(184, 2, 16);
  ASSERT_GE(reply_limit, 8U);
  EXPECT_EQ(159U, reply_limit - 8U);
}

TEST(DatagramPayloadLimits, InvalidGeometryFailsClosed) {
  EXPECT_EQ(0U, DatagramPayloadLimits::maxPlaintext(184, 2, 0));
  EXPECT_EQ(0U, DatagramPayloadLimits::maxPlaintext(8, 2, 16));
}

int main(int argc, char** argv) {
  ::testing::InitGoogleTest(&argc, argv);
  return RUN_ALL_TESTS();
}
