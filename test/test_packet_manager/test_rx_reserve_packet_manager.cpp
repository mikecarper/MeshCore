#include <gtest/gtest.h>
#include <helpers/RxReservePacketManager.h>

class TestClock : public mesh::MillisecondClock {
public:
  unsigned long now = 0;
  unsigned long getMillis() override { return now; }
};

class TestRadio : public mesh::Radio {
public:
  int begins = 0;
  uint8_t pending_rx[MAX_TRANS_UNIT];
  int pending_rx_len = 0;
  int send_starts = 0;
  bool receiving = false;
  bool in_recv_mode = true;
  int soft_recoveries = 0;
  int hard_recoveries = 0;
  int cad_set_calls = 0;
  int agc_resets = 0;
  bool cad_enabled = false;
  unsigned long last_irq = 0;
  bool recovery_result = true;
  bool send_complete = false;
  bool start_success = true;
  int send_finishes = 0;
  uint8_t sent_raw[3][MAX_TRANS_UNIT] = {};
  int sent_len[3] = {};

  void begin() override { begins++; }
  int recvRaw(uint8_t* dest, int max_len) override {
    if (pending_rx_len == 0) return 0;
    int len = pending_rx_len < max_len ? pending_rx_len : max_len;
    memcpy(dest, pending_rx, len);
    pending_rx_len = 0;
    return len;
  }
  uint32_t getEstAirtimeFor(int) override { return 1; }
  float packetScore(float, int) override { return 0; }
  bool startSendRaw(const uint8_t* bytes, int len) override {
    if (send_starts < 3) {
      sent_len[send_starts] = len;
      memcpy(sent_raw[send_starts], bytes, len);
    }
    send_starts++;
    return start_success;
  }
  bool isSendComplete() override { return send_complete; }
  void onSendFinished() override { send_finishes++; }
  bool isInRecvMode() const override { return in_recv_mode; }
  bool isReceiving() override { return receiving; }
  void setCADEnabled(bool enable) override {
    cad_set_calls++;
    cad_enabled = enable;
  }
  void resetAGC() override { agc_resets++; }
  unsigned long getLastRadioInterruptMillis() const override { return last_irq; }
  bool recoverRadio(bool hard) override {
    if (hard) {
      hard_recoveries++;
    } else {
      soft_recoveries++;
    }
    return recovery_result;
  }

  void queueRx(const uint8_t* raw, int len) {
    ASSERT_NE(raw, nullptr);
    ASSERT_GT(len, 0);
    ASSERT_LE(len, MAX_TRANS_UNIT);
    memcpy(pending_rx, raw, len);
    pending_rx_len = len;
  }
};

class TestDispatcher : public mesh::Dispatcher {
  RxReservePacketManager& manager;

protected:
  mesh::DispatcherAction onRecvPacket(mesh::Packet*) override {
    received_packets++;
    return ACTION_RELEASE;
  }
  int calcRxDelay(float score, uint32_t air_time) const override {
    return forced_rx_delay >= 0
        ? forced_rx_delay
        : mesh::Dispatcher::calcRxDelay(score, air_time);
  }
  bool shouldBypassRxDelay(const mesh::Packet*) override {
    return bypass_rx_delay;
  }
  bool getCADEnabled() const override {
    return configured_cad_enabled;
  }
  void onSendFail(mesh::Packet* packet) override {
    failed_packet = packet;
    free_count_during_failure = manager.getFreeCount();
  }
  void onSendComplete(mesh::Packet* packet) override {
    completed_packet = packet;
    completed_packets++;
  }

public:
  mesh::Packet* failed_packet = nullptr;
  mesh::Packet* completed_packet = nullptr;
  int completed_packets = 0;
  int free_count_during_failure = -1;
  int received_packets = 0;
  int forced_rx_delay = -1;
  bool bypass_rx_delay = false;
  bool configured_cad_enabled = false;
  int configured_agc_interval = 0;

  int getAGCResetInterval() const override {
    return configured_agc_interval;
  }

  TestDispatcher(TestRadio& radio, TestClock& clock, RxReservePacketManager& mgr)
    : mesh::Dispatcher(radio, clock, mgr), manager(mgr) { }

  bool nextQueueWakeDelay(uint32_t& delay_millis) const {
    return getNextQueueWakeDelay(delay_millis);
  }
  bool queuedWorkDue() const { return hasQueuedWorkDue(); }
  bool parse(mesh::Packet* packet, const uint8_t* raw, int len) {
    return tryParsePacket(packet, raw, len);
  }
  void makeRadioAvailable(bool available) { setRadioAvailable(available); }
};

TEST(Dispatcher, UnavailableRadioDoesNotBlockStartupAndRejectsSends) {
  RxReservePacketManager manager(4, 1);
  TestClock clock;
  TestRadio radio;
  TestDispatcher dispatcher(radio, clock, manager);

  dispatcher.makeRadioAvailable(false);
  dispatcher.begin();
  dispatcher.loop();
  EXPECT_EQ(0, radio.begins);
  EXPECT_EQ(0, radio.cad_set_calls);

  mesh::Packet* packet = dispatcher.obtainNewPacket();
  ASSERT_NE(packet, nullptr);
  packet->header = ROUTE_TYPE_DIRECT | (PAYLOAD_TYPE_RAW_CUSTOM << PH_TYPE_SHIFT);
  packet->payload[0] = 0x42;
  packet->payload_len = 1;
  EXPECT_FALSE(dispatcher.sendPacket(packet, 0));
  EXPECT_EQ(4, manager.getFreeCount());
  EXPECT_EQ(packet, dispatcher.failed_packet);

  dispatcher.makeRadioAvailable(true);
  EXPECT_EQ(1, radio.begins);
  clock.now = 1;
  dispatcher.loop();
  EXPECT_EQ(1, radio.cad_set_calls);
}

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

TEST(Dispatcher, FloodPacketWaitsForConfiguredRxDelayWithoutBypass) {
  RxReservePacketManager manager(4, 1);
  TestClock clock;
  TestRadio radio;
  TestDispatcher dispatcher(radio, clock, manager);
  dispatcher.forced_rx_delay = 1000;
  dispatcher.begin();
  const uint8_t raw[] = {
    ROUTE_TYPE_FLOOD | (PAYLOAD_TYPE_GRP_DATA << PH_TYPE_SHIFT), 0, 0x42
  };

  clock.now = 100;
  radio.queueRx(raw, sizeof(raw));
  dispatcher.loop();
  EXPECT_EQ(0, dispatcher.received_packets);

  clock.now = 1099;
  dispatcher.loop();
  EXPECT_EQ(0, dispatcher.received_packets);

  clock.now = 1100;
  dispatcher.loop();
  EXPECT_EQ(1, dispatcher.received_packets);
}

TEST(Dispatcher, ScopeRewriteHookBypassesConfiguredRxDelay) {
  RxReservePacketManager manager(4, 1);
  TestClock clock;
  TestRadio radio;
  TestDispatcher dispatcher(radio, clock, manager);
  dispatcher.forced_rx_delay = 1000;
  dispatcher.bypass_rx_delay = true;
  dispatcher.begin();
  const uint8_t raw[] = {
    ROUTE_TYPE_FLOOD | (PAYLOAD_TYPE_GRP_DATA << PH_TYPE_SHIFT), 0, 0x42
  };

  clock.now = 100;
  radio.queueRx(raw, sizeof(raw));
  dispatcher.loop();

  EXPECT_EQ(1, dispatcher.received_packets);
  EXPECT_EQ(4, manager.getFreeCount());
}

TEST(Dispatcher, ConfiguredCadStateIsPropagatedToTheRadio) {
  RxReservePacketManager manager(4, 1);
  TestClock clock;
  TestRadio radio;
  TestDispatcher dispatcher(radio, clock, manager);
  dispatcher.configured_cad_enabled = true;
  dispatcher.begin();

  clock.now = 1;
  dispatcher.loop();
  EXPECT_EQ(1, radio.cad_set_calls);
  EXPECT_TRUE(radio.cad_enabled);

  dispatcher.configured_cad_enabled = false;
  clock.now = 2002;
  dispatcher.loop();
  EXPECT_EQ(2, radio.cad_set_calls);
  EXPECT_FALSE(radio.cad_enabled);
}

TEST(Dispatcher, FirstAgcResetWaitsForConfiguredIntervalAfterStartup) {
  RxReservePacketManager manager(4, 1);
  TestClock clock;
  clock.now = 1000;
  TestRadio radio;
  TestDispatcher dispatcher(radio, clock, manager);
  dispatcher.begin();

  // Repeater preferences are loaded after Dispatcher::begin().  The first loop
  // must arm from that persisted value rather than treating a zero deadline as
  // already expired.
  dispatcher.configured_agc_interval = 8000;
  dispatcher.loop();
  EXPECT_EQ(0, radio.agc_resets);

  clock.now = 8999;
  dispatcher.loop();
  EXPECT_EQ(0, radio.agc_resets);

  clock.now = 9001;
  dispatcher.loop();
  EXPECT_EQ(1, radio.agc_resets);
}

TEST(Dispatcher, FirstAgcResetWaitsAfterDelayedRadioActivation) {
  RxReservePacketManager manager(4, 1);
  TestClock clock;
  TestRadio radio;
  TestDispatcher dispatcher(radio, clock, manager);
  dispatcher.configured_agc_interval = 8000;
  dispatcher.makeRadioAvailable(false);
  dispatcher.begin();

  clock.now = 2000;
  dispatcher.makeRadioAvailable(true);
  dispatcher.loop();
  EXPECT_EQ(0, radio.agc_resets);

  clock.now = 9999;
  dispatcher.loop();
  EXPECT_EQ(0, radio.agc_resets);

  clock.now = 10001;
  dispatcher.loop();
  EXPECT_EQ(1, radio.agc_resets);
}

TEST(Dispatcher, EnablingAgcResetAtRuntimeArmsWithoutImmediateReset) {
  RxReservePacketManager manager(4, 1);
  TestClock clock;
  TestRadio radio;
  TestDispatcher dispatcher(radio, clock, manager);
  dispatcher.begin();
  dispatcher.loop();

  clock.now = 5000;
  dispatcher.configured_agc_interval = 8000;
  dispatcher.loop();
  EXPECT_EQ(0, radio.agc_resets);

  clock.now = 13001;
  dispatcher.loop();
  EXPECT_EQ(1, radio.agc_resets);
}

TEST(Dispatcher, LengtheningPositiveAgcIntervalRearmsTheDeadline) {
  RxReservePacketManager manager(4, 1);
  TestClock clock;
  clock.now = 1000;
  TestRadio radio;
  TestDispatcher dispatcher(radio, clock, manager);
  dispatcher.configured_agc_interval = 8000;
  dispatcher.begin();
  dispatcher.loop();

  clock.now = 2000;
  dispatcher.configured_agc_interval = 240000;
  dispatcher.loop();

  // The former 8-second deadline must no longer fire.
  clock.now = 9001;
  dispatcher.loop();
  EXPECT_EQ(0, radio.agc_resets);

  clock.now = 242001;
  dispatcher.loop();
  EXPECT_EQ(1, radio.agc_resets);
}

TEST(Dispatcher, ShorteningPositiveAgcIntervalRearmsTheDeadline) {
  RxReservePacketManager manager(4, 1);
  TestClock clock;
  clock.now = 1000;
  TestRadio radio;
  TestDispatcher dispatcher(radio, clock, manager);
  dispatcher.configured_agc_interval = 240000;
  dispatcher.begin();
  dispatcher.loop();

  clock.now = 2000;
  dispatcher.configured_agc_interval = 8000;
  dispatcher.loop();

  clock.now = 9999;
  dispatcher.loop();
  EXPECT_EQ(0, radio.agc_resets);

  clock.now = 10001;
  dispatcher.loop();
  EXPECT_EQ(1, radio.agc_resets);
}

TEST(Dispatcher, DisablingAndReenablingAgcStartsANewFullInterval) {
  RxReservePacketManager manager(4, 1);
  TestClock clock;
  TestRadio radio;
  TestDispatcher dispatcher(radio, clock, manager);
  dispatcher.configured_agc_interval = 8000;
  dispatcher.begin();
  dispatcher.loop();

  clock.now = 4000;
  dispatcher.configured_agc_interval = 0;
  dispatcher.loop();

  clock.now = 10000;
  dispatcher.configured_agc_interval = 8000;
  dispatcher.loop();
  clock.now = 17999;
  dispatcher.loop();
  EXPECT_EQ(0, radio.agc_resets);

  clock.now = 18001;
  dispatcher.loop();
  EXPECT_EQ(1, radio.agc_resets);
}

TEST(Dispatcher, AgcResetRepeatsAtTheConfiguredInterval) {
  RxReservePacketManager manager(4, 1);
  TestClock clock;
  TestRadio radio;
  TestDispatcher dispatcher(radio, clock, manager);
  dispatcher.configured_agc_interval = 100;
  dispatcher.begin();
  dispatcher.loop();

  clock.now = 101;
  dispatcher.loop();
  EXPECT_EQ(1, radio.agc_resets);

  clock.now = 202;
  dispatcher.loop();
  EXPECT_EQ(2, radio.agc_resets);
}

TEST(Dispatcher, RadioReactivationStartsANewFullAgcInterval) {
  RxReservePacketManager manager(4, 1);
  TestClock clock;
  TestRadio radio;
  TestDispatcher dispatcher(radio, clock, manager);
  dispatcher.configured_agc_interval = 8000;
  dispatcher.begin();
  dispatcher.loop();

  clock.now = 7000;
  dispatcher.makeRadioAvailable(false);
  clock.now = 12000;
  dispatcher.makeRadioAvailable(true);
  dispatcher.loop();

  clock.now = 19999;
  dispatcher.loop();
  EXPECT_EQ(0, radio.agc_resets);

  clock.now = 20001;
  dispatcher.loop();
  EXPECT_EQ(1, radio.agc_resets);
}

TEST(Dispatcher, AgcResetDeadlineSurvivesMillisRollover) {
  RxReservePacketManager manager(4, 1);
  TestClock clock;
  clock.now = ~0UL - 0xFFUL;
  TestRadio radio;
  TestDispatcher dispatcher(radio, clock, manager);
  dispatcher.configured_agc_interval = 512;
  dispatcher.begin();
  dispatcher.loop();

  clock.now = 0xFFUL;
  dispatcher.loop();
  EXPECT_EQ(0, radio.agc_resets);

  clock.now = 0x101UL;
  dispatcher.loop();
  EXPECT_EQ(1, radio.agc_resets);
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

TEST(Dispatcher, QueueWakeDelayIncludesSchedulesAndAdaptiveChannelBackoff) {
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

  mesh::Packet* second = dispatcher.obtainNewPacket();
  ASSERT_NE(second, nullptr);
  second->header = ROUTE_TYPE_DIRECT | (PAYLOAD_TYPE_RAW_CUSTOM << PH_TYPE_SHIFT);
  second->payload[0] = 0x43;
  second->payload_len = 1;
  ASSERT_TRUE(dispatcher.sendPacket(second, 0));

  clock.now = 801;
  dispatcher.loop();
  ASSERT_TRUE(dispatcher.nextQueueWakeDelay(delay_millis));
  EXPECT_EQ(100U, delay_millis);
  EXPECT_FALSE(dispatcher.queuedWorkDue());

  clock.now = 901;
  radio.receiving = false;
  EXPECT_TRUE(dispatcher.queuedWorkDue());
}

TEST(Dispatcher, GrowingQueueShortensCADBusyCeiling) {
  RxReservePacketManager manager(8, 1);
  TestClock clock;
  clock.now = 100;
  TestRadio radio;
  radio.receiving = true;
  TestDispatcher dispatcher(radio, clock, manager);
  dispatcher.begin();

  mesh::Packet* first = dispatcher.obtainNewPacket();
  ASSERT_NE(first, nullptr);
  first->header = ROUTE_TYPE_DIRECT | (PAYLOAD_TYPE_RAW_CUSTOM << PH_TYPE_SHIFT);
  first->payload[0] = 0x40;
  first->payload_len = 1;
  ASSERT_TRUE(dispatcher.sendPacket(first, 0));
  clock.now = 101;
  dispatcher.loop();
  EXPECT_EQ(0, radio.send_starts);

  for (uint8_t i = 1; i < 4; i++) {
    mesh::Packet* packet = dispatcher.obtainNewPacket();
    ASSERT_NE(packet, nullptr);
    packet->header = ROUTE_TYPE_DIRECT | (PAYLOAD_TYPE_RAW_CUSTOM << PH_TYPE_SHIFT);
    packet->payload[0] = (uint8_t)(0x40 + i);
    packet->payload_len = 1;
    ASSERT_TRUE(dispatcher.sendPacket(packet, 0));
  }
  EXPECT_EQ(4, manager.getOutboundCount(clock.now));

  // Four ready packets reduce the normal four-second CAD ceiling to one
  // second, measured from the original busy observation.
  clock.now = 1102;
  dispatcher.loop();
  EXPECT_EQ(1, radio.send_starts);
  EXPECT_EQ(3, manager.getOutboundCount(clock.now));
  EXPECT_TRUE(dispatcher.getErrFlags() & ERR_EVENT_CAD_TIMEOUT);
}

TEST(Dispatcher, SilentRadioEscalatesFromSoftToHardRecovery) {
  RxReservePacketManager manager(8, 4);
  TestClock clock;
  TestRadio radio;
  TestDispatcher dispatcher(radio, clock, manager);
  dispatcher.begin();

  clock.now = 30UL * 60UL * 1000UL;
  dispatcher.loop();
  EXPECT_EQ(1, radio.soft_recoveries);
  EXPECT_EQ(0, radio.hard_recoveries);

  clock.now = 12UL * 60UL * 60UL * 1000UL;
  dispatcher.loop();
  EXPECT_EQ(1, radio.soft_recoveries);
  EXPECT_EQ(1, radio.hard_recoveries);
}

TEST(Dispatcher, RadioOutsideReceiveModeEscalatesOnSecondAttempt) {
  RxReservePacketManager manager(8, 4);
  TestClock clock;
  TestRadio radio;
  radio.in_recv_mode = false;
  TestDispatcher dispatcher(radio, clock, manager);
  dispatcher.begin();

  clock.now = 8001;
  dispatcher.loop();
  EXPECT_EQ(1, radio.soft_recoveries);
  EXPECT_EQ(0, radio.hard_recoveries);

  clock.now = 16002;
  dispatcher.loop();
  EXPECT_EQ(1, radio.soft_recoveries);
  EXPECT_EQ(1, radio.hard_recoveries);
}

TEST(Dispatcher, TimedOutTransmitRecoversAndRetriesSamePacket) {
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
  ASSERT_TRUE(dispatcher.sendPacket(packet, 0));

  clock.now = 101;
  dispatcher.loop();
  ASSERT_EQ(1, radio.send_starts);
  ASSERT_EQ(0, radio.hard_recoveries);

  clock.now = 103;
  dispatcher.loop();

  EXPECT_EQ(1, radio.send_finishes);
  EXPECT_EQ(1, radio.hard_recoveries);
  EXPECT_EQ(nullptr, dispatcher.failed_packet);
  EXPECT_EQ(7, manager.getFreeCount());
  EXPECT_TRUE(dispatcher.getErrFlags() & ERR_EVENT_RADIO_WATCHDOG);

  uint32_t wake_delay = 0;
  ASSERT_TRUE(dispatcher.nextQueueWakeDelay(wake_delay));
  EXPECT_EQ(200U, wake_delay);

  clock.now = 304;
  dispatcher.loop();
  ASSERT_EQ(2, radio.send_starts);
  ASSERT_EQ(radio.sent_len[0], radio.sent_len[1]);
  EXPECT_EQ(0, memcmp(radio.sent_raw[0], radio.sent_raw[1], radio.sent_len[0]));

  radio.send_complete = true;
  clock.now = 305;
  dispatcher.loop();

  EXPECT_EQ(2, radio.send_finishes);
  EXPECT_EQ(packet, dispatcher.completed_packet);
  EXPECT_EQ(1, dispatcher.completed_packets);
  EXPECT_EQ(nullptr, dispatcher.failed_packet);
  EXPECT_EQ(8, manager.getFreeCount());
}

TEST(Dispatcher, SecondTimedOutTransmitFailsWithoutThirdAttempt) {
  RxReservePacketManager manager(8, 4);
  TestClock clock;
  clock.now = 100;
  TestRadio radio;
  TestDispatcher dispatcher(radio, clock, manager);
  dispatcher.begin();

  mesh::Packet* packet = dispatcher.obtainNewPacket();
  ASSERT_NE(packet, nullptr);
  packet->header = ROUTE_TYPE_DIRECT | (PAYLOAD_TYPE_RAW_CUSTOM << PH_TYPE_SHIFT);
  packet->payload[0] = 0x43;
  packet->payload_len = 1;
  ASSERT_TRUE(dispatcher.sendPacket(packet, 0));

  clock.now = 101;
  dispatcher.loop();
  ASSERT_EQ(1, radio.send_starts);

  clock.now = 103;
  dispatcher.loop();
  ASSERT_EQ(1, radio.hard_recoveries);

  clock.now = 304;
  dispatcher.loop();
  ASSERT_EQ(2, radio.send_starts);

  clock.now = 306;
  dispatcher.loop();

  EXPECT_EQ(2, radio.send_finishes);
  EXPECT_EQ(2, radio.hard_recoveries);
  EXPECT_EQ(packet, dispatcher.failed_packet);
  EXPECT_EQ(8, manager.getFreeCount());

  clock.now = 1000;
  dispatcher.loop();
  EXPECT_EQ(2, radio.send_starts);
}

TEST(Dispatcher, StartFailureRetriesSamePacketWithoutApp) {
  RxReservePacketManager manager(8, 4);
  TestClock clock;
  clock.now = 100;
  TestRadio radio;
  radio.start_success = false;
  TestDispatcher dispatcher(radio, clock, manager);
  dispatcher.begin();

  mesh::Packet* packet = dispatcher.obtainNewPacket();
  ASSERT_NE(packet, nullptr);
  packet->header = ROUTE_TYPE_DIRECT | (PAYLOAD_TYPE_RAW_CUSTOM << PH_TYPE_SHIFT);
  packet->payload[0] = 0x44;
  packet->payload_len = 1;
  ASSERT_TRUE(dispatcher.sendPacket(packet, 0));

  clock.now = 101;
  dispatcher.loop();
  ASSERT_EQ(1, radio.send_starts);
  EXPECT_EQ(nullptr, dispatcher.failed_packet);
  EXPECT_EQ(7, manager.getFreeCount());

  radio.start_success = true;
  clock.now = 302;
  dispatcher.loop();
  ASSERT_EQ(2, radio.send_starts);
  ASSERT_EQ(radio.sent_len[0], radio.sent_len[1]);
  EXPECT_EQ(0, memcmp(radio.sent_raw[0], radio.sent_raw[1], radio.sent_len[0]));

  radio.send_complete = true;
  clock.now = 303;
  dispatcher.loop();

  EXPECT_EQ(packet, dispatcher.completed_packet);
  EXPECT_EQ(1, dispatcher.completed_packets);
  EXPECT_EQ(nullptr, dispatcher.failed_packet);
  EXPECT_EQ(8, manager.getFreeCount());
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
