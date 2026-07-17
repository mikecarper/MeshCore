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
  bool parse(mesh::Packet* packet, const uint8_t* raw, int len) {
    return tryParsePacket(packet, raw, len);
  }
};

TEST(Packet, ReadFromRejectsTruncatedHeadersAndPaths) {
  mesh::Packet packet;
  const uint8_t one_byte[] = {ROUTE_TYPE_DIRECT | (PAYLOAD_TYPE_RAW_CUSTOM << PH_TYPE_SHIFT)};
  EXPECT_FALSE(packet.readFrom(one_byte, sizeof(one_byte)));

  const uint8_t short_transport[] = {
    ROUTE_TYPE_TRANSPORT_DIRECT | (PAYLOAD_TYPE_RAW_CUSTOM << PH_TYPE_SHIFT), 0
  };
  EXPECT_FALSE(packet.readFrom(short_transport, sizeof(short_transport)));

  const uint8_t missing_path_byte[] = {
    ROUTE_TYPE_DIRECT | (PAYLOAD_TYPE_RAW_CUSTOM << PH_TYPE_SHIFT), 1
  };
  EXPECT_FALSE(packet.readFrom(missing_path_byte, sizeof(missing_path_byte)));
}

TEST(Packet, ReadFromAcceptsACompletePacketWithEmptyPayload) {
  mesh::Packet packet;
  const uint8_t raw[] = {
    ROUTE_TYPE_DIRECT | (PAYLOAD_TYPE_RAW_CUSTOM << PH_TYPE_SHIFT), 0
  };
  ASSERT_TRUE(packet.readFrom(raw, sizeof(raw)));
  EXPECT_EQ(0U, packet.path_len);
  EXPECT_EQ(0U, packet.payload_len);
}

TEST(Dispatcher, ParserRejectsTruncatedHeadersAndPaths) {
  RxReservePacketManager manager(4, 1);
  TestClock clock;
  TestRadio radio;
  TestDispatcher dispatcher(radio, clock, manager);
  mesh::Packet packet;

  const uint8_t one_byte[] = {ROUTE_TYPE_DIRECT | (PAYLOAD_TYPE_RAW_CUSTOM << PH_TYPE_SHIFT)};
  EXPECT_FALSE(dispatcher.parse(&packet, one_byte, sizeof(one_byte)));

  const uint8_t short_transport[] = {
    ROUTE_TYPE_TRANSPORT_DIRECT | (PAYLOAD_TYPE_RAW_CUSTOM << PH_TYPE_SHIFT), 0
  };
  EXPECT_FALSE(dispatcher.parse(&packet, short_transport, sizeof(short_transport)));

  const uint8_t missing_path_byte[] = {
    ROUTE_TYPE_DIRECT | (PAYLOAD_TYPE_RAW_CUSTOM << PH_TYPE_SHIFT), 1
  };
  EXPECT_FALSE(dispatcher.parse(&packet, missing_path_byte, sizeof(missing_path_byte)));
}

TEST(Dispatcher, ParserAcceptsACompletePacketWithEmptyPayload) {
  RxReservePacketManager manager(4, 1);
  TestClock clock;
  TestRadio radio;
  TestDispatcher dispatcher(radio, clock, manager);
  mesh::Packet packet;
  const uint8_t raw[] = {
    ROUTE_TYPE_DIRECT | (PAYLOAD_TYPE_RAW_CUSTOM << PH_TYPE_SHIFT), 0
  };

  ASSERT_TRUE(dispatcher.parse(&packet, raw, sizeof(raw)));
  EXPECT_EQ(0U, packet.path_len);
  EXPECT_EQ(0U, packet.payload_len);
}

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

static void setFloodIdentity(mesh::Packet* packet, uint8_t route, uint8_t seed) {
  packet->header = route | (PAYLOAD_TYPE_GRP_TXT << PH_TYPE_SHIFT);
  packet->path_len = 0;
  packet->payload[0] = seed;
  packet->payload_len = 1;
}

static uint8_t acceptFloodScope(const mesh::Packet*, void*) {
  return 1;
}

TEST(StaticPoolPacketManager, ScopedRxDelayCopyRequiresLocalValidator) {
  StaticPoolPacketManager manager(4);
  mesh::Packet* unscoped = manager.allocNew();
  mesh::Packet* scoped = manager.allocNew();
  ASSERT_NE(unscoped, nullptr);
  ASSERT_NE(scoped, nullptr);
  setFloodIdentity(unscoped, ROUTE_TYPE_FLOOD, 0x41);
  setFloodIdentity(scoped, ROUTE_TYPE_TRANSPORT_FLOOD, 0x41);
  scoped->transport_codes[0] = 0x1234;

  manager.queueInbound(unscoped, 100);
  manager.queueInbound(scoped, 200);

  EXPECT_EQ(unscoped, manager.getNextInbound(100));
  EXPECT_EQ(ROUTE_TYPE_FLOOD, unscoped->getRouteType());
  manager.free(unscoped);
  manager.free(manager.getNextInbound(200));
}

TEST(StaticPoolPacketManager, ScopedRxDelayCopyUpgradesQueuedUnscopedCopy) {
  StaticPoolPacketManager manager(4);
  manager.setFloodScopePreference(acceptFloodScope, NULL);
  mesh::Packet* unscoped = manager.allocNew();
  mesh::Packet* scoped = manager.allocNew();
  ASSERT_NE(unscoped, nullptr);
  ASSERT_NE(scoped, nullptr);
  setFloodIdentity(unscoped, ROUTE_TYPE_FLOOD, 0x42);
  setFloodIdentity(scoped, ROUTE_TYPE_TRANSPORT_FLOOD, 0x42);
  scoped->transport_codes[0] = 0x1234;
  scoped->transport_codes[1] = 0x5678;

  manager.queueInbound(unscoped, 100);
  manager.queueInbound(scoped, 200);

  EXPECT_EQ(ROUTE_TYPE_FLOOD, unscoped->getRouteType());
  EXPECT_EQ(unscoped, manager.getNextInbound(100));
  EXPECT_EQ(ROUTE_TYPE_TRANSPORT_FLOOD, unscoped->getRouteType());
  EXPECT_EQ(0x1234, unscoped->transport_codes[0]);
  EXPECT_EQ(0x5678, unscoped->transport_codes[1]);
  manager.free(unscoped);
  manager.free(manager.getNextInbound(200));
}

TEST(StaticPoolPacketManager, QueuedScopedCopyUpgradesNewEarlierUnscopedCopy) {
  StaticPoolPacketManager manager(4);
  manager.setFloodScopePreference(acceptFloodScope, NULL);
  mesh::Packet* scoped = manager.allocNew();
  mesh::Packet* unscoped = manager.allocNew();
  ASSERT_NE(scoped, nullptr);
  ASSERT_NE(unscoped, nullptr);
  setFloodIdentity(scoped, ROUTE_TYPE_TRANSPORT_FLOOD, 0x24);
  setFloodIdentity(unscoped, ROUTE_TYPE_FLOOD, 0x24);
  scoped->transport_codes[0] = 0xABCD;
  scoped->transport_codes[1] = 0;

  manager.queueInbound(scoped, 200);
  manager.queueInbound(unscoped, 100);

  EXPECT_EQ(ROUTE_TYPE_FLOOD, unscoped->getRouteType());
  EXPECT_EQ(unscoped, manager.getNextInbound(100));
  EXPECT_EQ(ROUTE_TYPE_TRANSPORT_FLOOD, unscoped->getRouteType());
  EXPECT_EQ(0xABCD, unscoped->transport_codes[0]);
  manager.free(unscoped);
  manager.free(manager.getNextInbound(200));
}

TEST(StaticPoolPacketManager, RxDelayScopeDoesNotCrossPacketIdentity) {
  StaticPoolPacketManager manager(4);
  mesh::Packet* scoped = manager.allocNew();
  mesh::Packet* different = manager.allocNew();
  ASSERT_NE(scoped, nullptr);
  ASSERT_NE(different, nullptr);
  setFloodIdentity(scoped, ROUTE_TYPE_TRANSPORT_FLOOD, 0x11);
  setFloodIdentity(different, ROUTE_TYPE_FLOOD, 0x12);
  scoped->transport_codes[0] = 0xCAFE;

  manager.queueInbound(scoped, 200);
  manager.queueInbound(different, 100);

  EXPECT_EQ(ROUTE_TYPE_FLOOD, different->getRouteType());
  EXPECT_EQ(different, manager.getNextInbound(100));
  manager.free(different);
  manager.free(manager.getNextInbound(200));
}

TEST(StaticPoolPacketManager, RxDelayNeverAddsScopeToFloodTrace) {
  StaticPoolPacketManager manager(4);
  manager.setFloodScopePreference(acceptFloodScope, NULL);
  mesh::Packet* unscoped = manager.allocNew();
  mesh::Packet* scoped = manager.allocNew();
  ASSERT_NE(unscoped, nullptr);
  ASSERT_NE(scoped, nullptr);
  setFloodIdentity(unscoped, ROUTE_TYPE_FLOOD, 0x13);
  setFloodIdentity(scoped, ROUTE_TYPE_TRANSPORT_FLOOD, 0x13);
  unscoped->header = ROUTE_TYPE_FLOOD | (PAYLOAD_TYPE_TRACE << PH_TYPE_SHIFT);
  scoped->header = ROUTE_TYPE_TRANSPORT_FLOOD | (PAYLOAD_TYPE_TRACE << PH_TYPE_SHIFT);
  scoped->transport_codes[0] = 0xCAFE;

  manager.queueInbound(unscoped, 100);
  manager.queueInbound(scoped, 200);

  EXPECT_EQ(unscoped, manager.getNextInbound(100));
  EXPECT_EQ(ROUTE_TYPE_FLOOD, unscoped->getRouteType());
  manager.free(unscoped);
  manager.free(manager.getNextInbound(200));
}

static uint8_t testFloodScopePreference(const mesh::Packet* packet, void*) {
  return (uint8_t)(packet->transport_codes[0] & 0xFF);
}

TEST(StaticPoolPacketManager, RxDelayRejectsUnknownShorterScope) {
  StaticPoolPacketManager manager(4);
  manager.setFloodScopePreference(testFloodScopePreference, NULL);
  mesh::Packet* known = manager.allocNew();
  mesh::Packet* unknown = manager.allocNew();
  ASSERT_NE(known, nullptr);
  ASSERT_NE(unknown, nullptr);
  setFloodIdentity(known, ROUTE_TYPE_TRANSPORT_FLOOD, 0x30);
  setFloodIdentity(unknown, ROUTE_TYPE_TRANSPORT_FLOOD, 0x30);
  known->setPathHashSizeAndCount(1, 4);
  unknown->setPathHashSizeAndCount(1, 1);
  known->transport_codes[0] = 3;
  unknown->transport_codes[0] = 0;  // rejected by the local validator

  manager.queueInbound(known, 100);
  manager.queueInbound(unknown, 200);

  EXPECT_EQ(known, manager.getNextInbound(100));
  EXPECT_EQ(3, known->transport_codes[0]);
  manager.free(known);
  manager.free(manager.getNextInbound(200));
}

TEST(StaticPoolPacketManager, RxDelayShorterScopedPathBeatsNarrowerScope) {
  StaticPoolPacketManager manager(4);
  manager.setFloodScopePreference(testFloodScopePreference, NULL);
  mesh::Packet* narrower = manager.allocNew();
  mesh::Packet* shorter = manager.allocNew();
  ASSERT_NE(narrower, nullptr);
  ASSERT_NE(shorter, nullptr);
  setFloodIdentity(narrower, ROUTE_TYPE_TRANSPORT_FLOOD, 0x31);
  setFloodIdentity(shorter, ROUTE_TYPE_TRANSPORT_FLOOD, 0x31);
  narrower->setPathHashSizeAndCount(1, 4);
  shorter->setPathHashSizeAndCount(1, 2);
  narrower->transport_codes[0] = 9;  // higher local preference
  shorter->transport_codes[0] = 1;

  manager.queueInbound(narrower, 100);
  manager.queueInbound(shorter, 200);

  EXPECT_EQ(narrower, manager.getNextInbound(100));
  EXPECT_EQ(1, narrower->transport_codes[0]);
  manager.free(narrower);
  manager.free(manager.getNextInbound(200));
}

TEST(StaticPoolPacketManager, RxDelayEqualPathsPreferNarrowerLocalScope) {
  StaticPoolPacketManager manager(4);
  manager.setFloodScopePreference(testFloodScopePreference, NULL);
  mesh::Packet* shallow = manager.allocNew();
  mesh::Packet* narrower = manager.allocNew();
  ASSERT_NE(shallow, nullptr);
  ASSERT_NE(narrower, nullptr);
  setFloodIdentity(shallow, ROUTE_TYPE_TRANSPORT_FLOOD, 0x32);
  setFloodIdentity(narrower, ROUTE_TYPE_TRANSPORT_FLOOD, 0x32);
  shallow->setPathHashSizeAndCount(1, 3);
  narrower->setPathHashSizeAndCount(1, 3);
  shallow->transport_codes[0] = 1;
  narrower->transport_codes[0] = 9;  // higher local preference

  manager.queueInbound(shallow, 100);
  manager.queueInbound(narrower, 200);

  EXPECT_EQ(shallow, manager.getNextInbound(100));
  EXPECT_EQ(9, shallow->transport_codes[0]);
  manager.free(shallow);
  manager.free(manager.getNextInbound(200));
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
