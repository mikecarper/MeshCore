#include <gtest/gtest.h>

#include <helpers/RxReservePacketManager.h>

TEST(RxReservePacketManager, RejectedOutboundRemainsOwnedByCaller) {
  RxReservePacketManager manager(8, 4);
  mesh::Packet* held[6];
  for (int i = 0; i < 6; i++) {
    held[i] = manager.allocNew();
    ASSERT_NE(held[i], nullptr);
  }
  ASSERT_EQ(manager.getFreeCount(), 2);

  EXPECT_FALSE(manager.queueOutbound(held[5], 2, 0));
  EXPECT_EQ(manager.getFreeCount(), 2);

  manager.free(held[5]);
  EXPECT_EQ(manager.getFreeCount(), 3);
  for (int i = 0; i < 5; i++) {
    manager.free(held[i]);
  }
  EXPECT_EQ(manager.getFreeCount(), 8);
}

TEST(RxReservePacketManager, PeekMatchesDequeueWithoutRemovingPacket) {
  RxReservePacketManager manager(8, 4);
  mesh::Packet* low = manager.allocNew();
  mesh::Packet* high = manager.allocNew();
  ASSERT_NE(low, nullptr);
  ASSERT_NE(high, nullptr);

  ASSERT_TRUE(manager.queueOutbound(low, 2, 100));
  ASSERT_TRUE(manager.queueOutbound(high, 0, 100));
  EXPECT_EQ(high, manager.peekNextOutbound(100));
  EXPECT_EQ(2, manager.getOutboundTotal());
  EXPECT_EQ(high, manager.getNextOutbound(100));

  manager.free(high);
  EXPECT_EQ(low, manager.getNextOutbound(100));
  manager.free(low);
}

TEST(RxReservePacketManager, PeekExpiresStalePacketBeforeChannelSelection) {
  RxReservePacketManager manager(8, 4);
  mesh::Packet* stale = manager.allocNew();
  ASSERT_NE(stale, nullptr);
  ASSERT_TRUE(manager.queueOutbound(stale, 0, 0));
  ASSERT_EQ(7, manager.getFreeCount());

  EXPECT_EQ(nullptr, manager.peekNextOutbound(30001));
  EXPECT_EQ(0, manager.getOutboundTotal());
  EXPECT_EQ(8, manager.getFreeCount());
}

int main(int argc, char** argv) {
  ::testing::InitGoogleTest(&argc, argv);
  return RUN_ALL_TESTS();
}
