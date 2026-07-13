#include <gtest/gtest.h>

#include <helpers/RxReservePacketManager.h>

class TestClock : public mesh::MillisecondClock {
public:
  unsigned long now = 0;
  unsigned long getMillis() override { return now; }
};

class TestRadio : public mesh::Radio {
public:
  int send_starts = 0;

  int recvRaw(uint8_t*, int) override { return 0; }
  uint32_t getEstAirtimeFor(int) override { return 1; }
  float packetScore(float, int) override { return 0; }
  bool startSendRaw(const uint8_t*, int) override { send_starts++; return true; }
  bool isSendComplete() override { return false; }
  void onSendFinished() override { }
  bool isInRecvMode() const override { return true; }
};

class TestDispatcher : public mesh::Dispatcher {
  RxReservePacketManager& manager;

protected:
  mesh::DispatcherAction onRecvPacket(mesh::Packet*) override { return ACTION_RELEASE; }
  void onSendFail(mesh::Packet* packet) override {
    failed_packet = packet;
    free_count_during_failure = manager.getFreeCount();
  }

public:
  mesh::Packet* failed_packet = nullptr;
  int free_count_during_failure = -1;

  TestDispatcher(TestRadio& radio, TestClock& clock, RxReservePacketManager& mgr)
    : mesh::Dispatcher(radio, clock, mgr), manager(mgr) { }
};

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
  // The manager must not silently free a packet while Dispatcher/application
  // retry state can still refer to it.
  EXPECT_EQ(7, manager.getFreeCount());
  EXPECT_EQ(stale, manager.getNextDroppedOutbound());
  EXPECT_EQ(nullptr, manager.getNextDroppedOutbound());
  manager.free(stale);
  EXPECT_EQ(8, manager.getFreeCount());
}

TEST(RxReservePacketManager, DispatcherNotifiesBeforeReleasingStaleOutbound) {
  RxReservePacketManager manager(8, 4);
  TestClock clock;
  TestRadio radio;
  TestDispatcher dispatcher(radio, clock, manager);
  dispatcher.begin();

  mesh::Packet* stale = dispatcher.obtainNewPacket();
  ASSERT_NE(stale, nullptr);
  stale->header = ROUTE_TYPE_DIRECT | (PAYLOAD_TYPE_RAW_CUSTOM << PH_TYPE_SHIFT);
  stale->payload[0] = 0x42;
  stale->payload_len = 1;
  ASSERT_TRUE(dispatcher.sendPacket(stale, 0));

  clock.now = 30001;
  dispatcher.loop();

  EXPECT_EQ(stale, dispatcher.failed_packet);
  EXPECT_EQ(7, dispatcher.free_count_during_failure);
  EXPECT_EQ(8, manager.getFreeCount());
  EXPECT_EQ(0, radio.send_starts);
}

int main(int argc, char** argv) {
  ::testing::InitGoogleTest(&argc, argv);
  return RUN_ALL_TESTS();
}
