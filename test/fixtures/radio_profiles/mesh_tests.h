// Included by test_trace_retry.cpp to exercise the production Mesh/Dispatcher.
class DualProfileTestRadio : public RetryCodingRateRadio {
 public:
  mesh::RadioProfiles config;
  uint8_t selected = 0;
  int busy_profile = -1;
  bool fail_next_send = false;
  uint32_t estimated_airtime = 10;
  std::vector<uint8_t> incoming;
  std::vector<uint8_t> transmissions;
  DualProfileTestRadio() {
    config.primary.freq = 909.5f; config.primary.bw = 62.5f;
    config.primary.sf = 7; config.primary.cr = 5;
    config.secondary.params = config.primary;
    config.secondary.params.freq = 910.5f;
    config.secondary.params.bw = 500;
    config.secondary.mode = mesh::RadioProfileMode::RxTx;
  }
  mesh::RadioProfiles* profiles() override { return &config; }
  const mesh::RadioProfiles* profiles() const override { return &config; }
  uint32_t getEstAirtimeFor(int) override { return estimated_airtime; }
  uint8_t receiveProfile() const override { return selected; }
  mesh::RadioParamApplyResult prepareTransmitProfile(uint8_t p) override {
    if (!incoming.empty()) return mesh::RadioParamApplyResult::BUSY;
    if (!config.canTransmit(p)) return mesh::RadioParamApplyResult::FAILED;
    selected = p; cr = config.params(p).cr;
    return mesh::RadioParamApplyResult::APPLIED;
  }
  bool isReceiving() override { return selected == busy_profile; }
  bool isReceivingPassive(int) override { return isReceiving(); }
  bool startSendRaw(const uint8_t* bytes, int size) override {
    if (fail_next_send) { fail_next_send = false; return false; }
    transmissions.push_back(selected);
    return RetryCodingRateRadio::startSendRaw(bytes, size);
  }
  int recvRaw(uint8_t* bytes, int size) override {
    if (incoming.empty() || (int)incoming.size() > size) return 0;
    const int length = incoming.size();
    memcpy(bytes, incoming.data(), length); incoming.clear();
    return length;
  }
};

class DualProfileTestMesh : public RetryCodingRateMesh {
 public:
  using RetryCodingRateMesh::RetryCodingRateMesh;
  mesh::Packet* replyOn(uint8_t profile, uint32_t generation) {
    ReceiveProfileScope context(*this, profile, generation);
    auto* packet = obtainNewPacket();
    if (!packet) return nullptr;
    packet->header = (PAYLOAD_TYPE_RESPONSE << PH_TYPE_SHIFT) | ROUTE_TYPE_DIRECT;
    packet->setPathHashSizeAndCount(1, 0);
    packet->payload_len = 1; packet->payload[0] = 0x5a;
    return packet;
  }
};

class DualProfileTest : public testing::Test {
 protected:
  TraceTestClock clock;
  TraceTestRTC rtc;
  TraceTestRNG rng;
  DualProfileTestRadio radio;
  TraceTestTables tables;
  StaticPoolPacketManager manager{40};
  DualProfileTestMesh node{radio, clock, rng, rtc, manager, tables};
  void SetUp() override { node.begin(); node.flood_attempts = 2; }
  mesh::Packet* queue(uint8_t type = PAYLOAD_TYPE_GRP_TXT) {
    auto* p = node.obtainNewPacket();
    if (!p) return nullptr;
    *p = makeFloodPacket(type);
    if (!node.sendFlood(p)) return nullptr;
    return p;
  }
  void tick(uint32_t ms = 1) { clock.now += ms; node.loop(); }
};

TEST(RadioProfiles, CrossPolicyAndReceiveOnlyMatrix) {
  mesh::RadioProfiles p;
  for (int a = 0; a < 2; ++a) for (int b = 0; b < 2; ++b) {
    p.primary_temporary = a; p.secondary_temporary = b;
    for (int policy = 0; policy < 3; ++policy) {
      p.cross = (mesh::RadioCrossMode)policy;
      const bool cross = policy == 1 || (policy == 0 && a == b);
      p.secondary.mode = mesh::RadioProfileMode::RxTx;
      EXPECT_EQ(cross ? 3 : 1, p.transmitMask(0));
      EXPECT_EQ(cross ? 3 : 2, p.transmitMask(1));
      p.secondary.mode = mesh::RadioProfileMode::Rx;
      EXPECT_EQ(1, p.transmitMask(0));
      EXPECT_EQ(cross ? 1 : 0, p.transmitMask(1));
      p.secondary.mode = mesh::RadioProfileMode::Off;
      EXPECT_EQ(1, p.transmitMask(0));
    }
  }
}

TEST(RadioProfiles, AutomaticPreamblesIncludeMeasuredSwitchingMargin) {
  DualProfileTestRadio radio;
  auto& p = radio.config;
  const uint16_t expected[] = {120, 88, 48};
  for (int sf = 7; sf <= 9; ++sf) {
    p.secondary.params.sf = sf;
    EXPECT_EQ(expected[sf-7], p.preamble(1, 32));
    EXPECT_EQ(32, p.preamble(0, 32));
    EXPECT_EQ(0, p.preamble(1, 32) % 8);
    EXPECT_EQ(9831, p.listenUs(0));
    EXPECT_EQ(6937, p.listenUs(1));
    EXPECT_LE(2 * (p.listenUs(0) + p.listenUs(1) + 2 * p.SwitchBudgetUs + p.LoopBudgetUs),
        p.preamble(0, 32) * p.symbolUs(p.primary));
    EXPECT_LE(p.listenUs(1) + 2 * p.SwitchBudgetUs + p.LoopBudgetUs
        + p.AcquisitionSymbols * p.symbolUs(p.primary), p.preamble(0, 32) * p.symbolUs(p.primary));
  }
  p.secondary.params.preamble = 40;
  EXPECT_EQ(40, p.preamble(1, 32));
  p.secondary.mode = mesh::RadioProfileMode::Off;
  EXPECT_EQ(16, p.preamble(0, 16));
}

TEST(RadioProfiles, SlowerProfileDeterminesOrderAndFasterReceiveWindow) {
  DualProfileTestRadio radio;
  auto& p = radio.config;
  EXPECT_EQ(0, p.slowerProfile());
  std::swap(p.primary, p.secondary.params);
  EXPECT_EQ(1, p.slowerProfile());
  EXPECT_EQ(9831, p.listenUs(1));
  EXPECT_EQ(6937, p.listenUs(0));
  p.secondary.params.preamble = 40;
  EXPECT_EQ(15129, p.listenUs(0));
  p.secondary.params.preamble = 8;
  EXPECT_FALSE(p.automaticPreambleFits()); // no safe time for the faster channel
}

TEST_F(DualProfileTest, TransmitsIdenticalMessageOnBothProfilesOnce) {
  node.flood_attempts = 0;
  ASSERT_NE(nullptr, queue());
  ASSERT_EQ(2, manager.getOutboundTotal());
  auto* a = manager.getOutboundByIdx(0);
  auto* b = manager.getOutboundByIdx(1);
  EXPECT_EQ(a->payload_len, b->payload_len);
  EXPECT_EQ(0, memcmp(a->payload, b->payload, a->payload_len));
  tick(); ASSERT_TRUE(radio.sending);
  radio.complete = true; tick();
  radio.complete = true; tick();
  radio.complete = true; tick();
  EXPECT_EQ((std::vector<uint8_t>{0, 1}), radio.transmissions);
  EXPECT_EQ(40, manager.getFreeCount());
}

TEST_F(DualProfileTest, DefaultSeparatesNormalTrafficFromTemporaryOta) {
  radio.config.secondary_temporary = true;
  ASSERT_NE(nullptr, queue());
  ASSERT_EQ(1, manager.getOutboundTotal());
  EXPECT_EQ(0, manager.getOutboundByIdx(0)->radio_profile);
  ASSERT_NE(nullptr, queue(PAYLOAD_TYPE_OTA));
  ASSERT_EQ(2, manager.getOutboundTotal());
  EXPECT_EQ(1, manager.getOutboundByIdx(1)->radio_profile);
}

TEST_F(DualProfileTest, ReceiveOnlyNeverTransmitsSecondary) {
  radio.config.secondary.mode = mesh::RadioProfileMode::Rx;
  ASSERT_NE(nullptr, queue());
  EXPECT_EQ(1, manager.getOutboundTotal());
  tick(); EXPECT_EQ((std::vector<uint8_t>{0}), radio.transmissions);
}

TEST_F(DualProfileTest, ReceiveOnlyTemporaryOtaDoesNotLeakOntoNormalChannel) {
  radio.config.secondary_temporary = true;
  radio.config.secondary.mode = mesh::RadioProfileMode::Rx;
  EXPECT_EQ(nullptr, queue(PAYLOAD_TYPE_OTA));
  EXPECT_EQ(0, manager.getOutboundTotal());
  EXPECT_EQ(40, manager.getFreeCount());
  tick(); EXPECT_TRUE(radio.transmissions.empty());
}

TEST_F(DualProfileTest, ExplicitCrossAllowsReceiveOnlyTemporaryOtaOnPrimary) {
  radio.config.secondary_temporary = true;
  radio.config.secondary.mode = mesh::RadioProfileMode::Rx;
  radio.config.cross = mesh::RadioCrossMode::On;
  ASSERT_NE(nullptr, queue(PAYLOAD_TYPE_OTA));
  ASSERT_EQ(1, manager.getOutboundTotal());
  EXPECT_EQ(1, manager.getOutboundByIdx(0)->radio_origin);
  EXPECT_EQ(0, manager.getOutboundByIdx(0)->radio_profile);
  tick(); EXPECT_EQ((std::vector<uint8_t>{0}), radio.transmissions);
}

TEST_F(DualProfileTest, BusyPrimaryDoesNotBlockSecondaryQueue) {
  node.flood_attempts = 0;
  radio.busy_profile = 0;
  ASSERT_NE(nullptr, queue());
  tick(); EXPECT_FALSE(radio.sending);
  tick(); EXPECT_EQ((std::vector<uint8_t>{1}), radio.transmissions);
}

TEST_F(DualProfileTest, PacketArrivingAfterFailedTransmitCannotDeadlockRadioRetry) {
  node.flood_attempts = 0;
  radio.config.cross = mesh::RadioCrossMode::Off;
  radio.fail_next_send = true;
  ASSERT_NE(nullptr, queue());
  tick(); ASSERT_FALSE(radio.sending);
  radio.incoming = {uint8_t(ROUTE_TYPE_DIRECT | (PAYLOAD_TYPE_GRP_TXT << PH_TYPE_SHIFT)), 0, 0x42};
  tick(500);
  EXPECT_TRUE(radio.incoming.empty());
  EXPECT_EQ(1, radio.config.rx_packets[0]);
  EXPECT_EQ((std::vector<uint8_t>{0}), radio.transmissions);
  radio.complete = true; tick();
  EXPECT_EQ(40, manager.getFreeCount());
}

TEST_F(DualProfileTest, AirtimeBudgetWaitDoesNotPinReceiverToQueuedTransmitProfile) {
  node.flood_attempts = 0;
  radio.config.cross = mesh::RadioCrossMode::Off;
  radio.estimated_airtime = 100000000;
  ASSERT_NE(nullptr, queue());
  radio.selected = 1; // scanner is currently on the other channel
  tick();
  EXPECT_FALSE(radio.sending);
  EXPECT_EQ(1, radio.selected);
  tick();
  EXPECT_EQ(1, radio.selected);
}

TEST_F(DualProfileTest, DirectRetriesKeepSeparateCodingRatesAndEchoOwnership) {
  node.direct_attempts = 2;
  radio.config.secondary.params.cr = 7;
  const uint8_t route[] = {0x11, 0x22};
  ASSERT_NE(nullptr, makeDirectText(node, 0x55, route, sizeof(route)));
  tick(); radio.complete = true; tick();
  radio.complete = true; tick();
  radio.complete = true; tick();
  ASSERT_EQ(2, manager.getOutboundTotal());
  auto* a = manager.getOutboundByIdx(0);
  auto* b = manager.getOutboundByIdx(1);
  ASSERT_NE(a->radio_profile, b->radio_profile);
  EXPECT_EQ((std::vector<uint8_t>{5, 7}), radio.transmitted_crs);
  mesh::Packet echo = *a;
  echo.radio_bound = false; echo.radio_local = false;
  echo.setPathHashCount(a->getPathHashCount() - 1);
  node.receivePacket(&echo);
  ASSERT_EQ(1, manager.getOutboundTotal());
  EXPECT_EQ(b, manager.getOutboundByIdx(0));
}

TEST_F(DualProfileTest, ExpiredSecondaryPacketsAreDiscarded) {
  node.flood_attempts = 0;
  radio.config.secondary_temporary = true;
  ASSERT_NE(nullptr, queue(PAYLOAD_TYPE_OTA));
  radio.config.setSecondary({}, false);
  tick();
  EXPECT_TRUE(radio.transmissions.empty());
  EXPECT_EQ(0, manager.getOutboundTotal());
  EXPECT_EQ(40, manager.getFreeCount());
}

TEST_F(DualProfileTest, EchoOnOneProfileDoesNotCancelOtherProfileRetry) {
  ASSERT_NE(nullptr, queue());
  tick(); radio.complete = true; tick();
  radio.complete = true; tick();
  radio.complete = true; tick();
  ASSERT_EQ(2, manager.getOutboundTotal());
  auto* first = manager.getOutboundByIdx(0);
  auto* second = manager.getOutboundByIdx(1);
  EXPECT_NE(first->radio_profile, second->radio_profile);
  mesh::Packet echo = *first;
  echo.radio_bound = false; echo.radio_local = false;
  echo.setPathHashCount(first->getPathHashCount() + 1);
  node.receivePacket(&echo);
  ASSERT_EQ(1, manager.getOutboundTotal());
  EXPECT_EQ(second, manager.getOutboundByIdx(0));
  EXPECT_NE(echo.radio_profile, second->radio_profile);
}

TEST_F(DualProfileTest, DeferredReplyRetainsItsReceiveProfileAndSession) {
  radio.config.secondary_temporary = true;
  auto* reply = node.replyOn(1, radio.config.generation[1]);
  ASSERT_NE(nullptr, reply);
  ASSERT_TRUE(node.sendPacket(reply, 0));
  ASSERT_EQ(1, manager.getOutboundTotal());
  EXPECT_EQ(1, manager.getOutboundByIdx(0)->radio_profile);
  auto* local = node.obtainNewPacket();
  ASSERT_NE(nullptr, local);
  EXPECT_TRUE(local->radio_local); // the scope did not leak into later local work
  node.releasePacket(local);
  auto* stale = node.replyOn(1, radio.config.generation[1]);
  radio.config.setSecondary({}, false);
  EXPECT_FALSE(node.sendPacket(stale, 0));
  tick();
  EXPECT_EQ(40, manager.getFreeCount());
  EXPECT_TRUE(radio.transmissions.empty());
}

TEST_F(DualProfileTest, ChangedPolicyRemovesCrossCopiesBeforeTransmission) {
  node.flood_attempts = 0;
  ASSERT_NE(nullptr, queue());
  radio.config.cross = mesh::RadioCrossMode::Off;
  tick();
  radio.complete = true; tick();
  EXPECT_EQ((std::vector<uint8_t>{0}), radio.transmissions);
  EXPECT_EQ(40, manager.getFreeCount());
}
