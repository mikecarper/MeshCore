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
  bool receiving = false;

  int recvRaw(uint8_t*, int) override { return 0; }
  uint32_t getEstAirtimeFor(int) override { return 1; }
  float packetScore(float, int) override { return 0; }
  bool startSendRaw(const uint8_t*, int) override { send_starts++; return true; }
  bool isSendComplete() override { return false; }
  void onSendFinished() override { }
  bool isInRecvMode() const override { return true; }
  bool isReceiving() override { return receiving; }
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

  bool nextQueueWakeDelay(uint32_t& delay_millis) const {
    return getNextQueueWakeDelay(delay_millis);
  }
  bool queuedWorkDue() const { return hasQueuedWorkDue(); }
};

TEST(StaticPoolPacketManager, ReportsEarliestQueueTimesWithoutDequeuing) {
  StaticPoolPacketManager manager(8);
  mesh::Packet* later = manager.allocNew();
  mesh::Packet* earlier = manager.allocNew();
  mesh::Packet* inbound = manager.allocNew();
  ASSERT_NE(later, nullptr);
  ASSERT_NE(earlier, nullptr);
  ASSERT_NE(inbound, nullptr);

  ASSERT_TRUE(manager.queueOutbound(later, 0, 500));
  ASSERT_TRUE(manager.queueOutbound(earlier, 0, 300));
  manager.queueInbound(inbound, 250);

  uint32_t scheduled_for = 0;
  ASSERT_TRUE(manager.getNextOutboundTime(100, scheduled_for));
  EXPECT_EQ(300U, scheduled_for);
  ASSERT_TRUE(manager.getNextInboundTime(100, scheduled_for));
  EXPECT_EQ(250U, scheduled_for);
  EXPECT_EQ(2, manager.getOutboundTotal());

  ASSERT_TRUE(manager.getNextOutboundTime(400, scheduled_for));
  EXPECT_EQ(400U, scheduled_for);  // overdue work is runnable now

  manager.free(manager.getNextInbound(250));
  manager.free(manager.getNextOutbound(500));
  manager.free(manager.getNextOutbound(500));
}

TEST(StaticPoolPacketManager, EarliestQueueTimeIsCorrectAcrossMillisRollover) {
  StaticPoolPacketManager manager(4);
  mesh::Packet* before_wrap = manager.allocNew();
  mesh::Packet* after_wrap = manager.allocNew();
  ASSERT_NE(before_wrap, nullptr);
  ASSERT_NE(after_wrap, nullptr);

  // Add these in reverse chronological order to exercise the cached minimum.
  ASSERT_TRUE(manager.queueOutbound(after_wrap, 0, 0x00000004UL));
  ASSERT_TRUE(manager.queueOutbound(before_wrap, 0, 0xFFFFFFF5UL));

  uint32_t scheduled_for = 0;
  ASSERT_TRUE(manager.getNextOutboundTime(0xFFFFFFF0UL, scheduled_for));
  EXPECT_EQ(0xFFFFFFF5UL, scheduled_for);
  EXPECT_EQ(before_wrap, manager.getNextOutbound(0xFFFFFFF5UL));
  manager.free(before_wrap);

  ASSERT_TRUE(manager.getNextOutboundTime(0xFFFFFFF6UL, scheduled_for));
  EXPECT_EQ(0x00000004UL, scheduled_for);
  EXPECT_EQ(after_wrap, manager.getNextOutbound(0x00000004UL));
  manager.free(after_wrap);
}

TEST(Dispatcher, QueueWakeDelayIncludesSchedulesAndChannelBackoff) {
  RxReservePacketManager manager(8, 4);
  TestClock clock;
  clock.now = 100;
  TestRadio radio;
  TestDispatcher dispatcher(radio, clock, manager);
  dispatcher.begin();

  mesh::Packet* packet = dispatcher.obtainNewPacket();
  ASSERT_NE(packet, nullptr);
  packet->header = ROUTE_TYPE_DIRECT | (PAYLOAD_TYPE_RAW_CUSTOM << PH_TYPE_SHIFT);
  packet->payload[0] = 0x42;
  packet->payload_len = 1;
  ASSERT_TRUE(dispatcher.sendPacket(packet, 0, 500));

  uint32_t delay_millis = 0;
  ASSERT_TRUE(dispatcher.nextQueueWakeDelay(delay_millis));
  EXPECT_EQ(500U, delay_millis);
  EXPECT_FALSE(dispatcher.queuedWorkDue());

  clock.now = 600;
  radio.receiving = true;
  dispatcher.loop();
  ASSERT_TRUE(dispatcher.nextQueueWakeDelay(delay_millis));
  EXPECT_EQ(200U, delay_millis);
  EXPECT_FALSE(dispatcher.queuedWorkDue());

  clock.now = 800;
  radio.receiving = false;
  EXPECT_TRUE(dispatcher.queuedWorkDue());
}

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
