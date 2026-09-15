// Included by test_trace_retry.cpp to exercise the production Mesh/Dispatcher.
class DualProfileTestRadio : public RetryCodingRateRadio {
 public:
  mesh::RadioProfiles config;
  uint8_t selected = 0;
  int busy_profile = -1;
  bool fail_next_send = false;
  bool hold_prepare_busy = false;
  bool carrier = false, carrier_on_receive = false;
  unsigned carrier_services = 0, recoveries = 0;
  bool isCarrierWaveActive() const override { return carrier; }
  bool isInRecvMode() const override { return !carrier && !sending; }
  void loop() override { ++carrier_services; }
  bool recoverRadio(bool) override { ++recoveries; return true; }
  uint32_t estimated_airtime = 10;
  TraceTestClock* sensing_clock = nullptr;
  uint32_t sensing_delay = 0;
  bool distinguish_airtime = false;
  uint32_t getProfileAirtime(uint8_t profile, int bytes, uint8_t cr = 0) override {
    return distinguish_airtime ? (profile ? 70 : 900)
        : mesh::Radio::getProfileAirtime(profile, bytes, cr);
  }
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
    return prepareTransmitProfile(p, false);
  }
  mesh::RadioParamApplyResult prepareTransmitProfile(uint8_t p, bool reply_rx_override) override {
    if (hold_prepare_busy) return mesh::RadioParamApplyResult::BUSY;
    if (!incoming.empty()) return mesh::RadioParamApplyResult::BUSY;
    if (!config.canTransmit(p, reply_rx_override)) return mesh::RadioParamApplyResult::FAILED;
    selected = p; cr = config.params(p).cr;
    return mesh::RadioParamApplyResult::APPLIED;
  }
  bool isReceiving() override {
    if (sensing_clock) sensing_clock->now += sensing_delay;
    return selected == busy_profile;
  }
  bool isReceivingPassive(int) override { return isReceiving(); }
  bool startSendRaw(const uint8_t* bytes, int size) override {
    if (fail_next_send) { fail_next_send = false; return false; }
    transmissions.push_back(selected);
    return RetryCodingRateRadio::startSendRaw(bytes, size);
  }
  int recvRaw(uint8_t* bytes, int size) override {
    if (carrier_on_receive) { carrier = true; carrier_on_receive = false; return 0; }
    if (incoming.empty() || (int)incoming.size() > size) return 0;
    const int length = incoming.size();
    memcpy(bytes, incoming.data(), length); incoming.clear();
    return length;
  }
};

class DualProfileTestMesh : public RetryCodingRateMesh {
 public:
  using RetryCodingRateMesh::RetryCodingRateMesh;
  using RetryCodingRateMesh::isPacketRadioCurrent;
  using RetryCodingRateMesh::getTransmitAirtime;
  using RetryCodingRateMesh::getTransmitProfileMask;
  using RetryCodingRateMesh::getRetransmitDelay;
  using RetryCodingRateMesh::getOtaPacketAirtime;
  using RetryCodingRateMesh::tryParsePacket;
  bool cross_filter_allows = true;
  float ota_speed = 1.0f;
  bool suppress_tx = false;
  bool allowPacketTransmit(const mesh::Packet* packet) const override {
    return !suppress_tx && RetryCodingRateMesh::allowPacketTransmit(packet);
  }
  float getOtaSpeedFactor() const override { return ota_speed; }
  unsigned cross_filter_calls = 0;
  bool allowRadioProfileCross(const mesh::Packet*) override {
    ++cross_filter_calls;
    return cross_filter_allows;
  }
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

TEST_F(DualProfileTest, OtaSpeedPacesBothCopiesAndYieldsToOrdinaryTraffic) {
  node.flood_attempts = 0;
  node.tempRadioActive = true;
  node.ota_speed = 0.05f;
  radio.config.cross = mesh::RadioCrossMode::On;
  auto* first = queue(PAYLOAD_TYPE_OTA);
  ASSERT_NE(nullptr, first);
  tick(); ASSERT_TRUE(radio.sending);
  radio.complete = true;
  tick(100); // actual OTA TX airtime: 100 ms; quiet allowance: 1900 ms
  EXPECT_EQ(radio.transmissions.size(), 1u);
  auto* normal = queue(PAYLOAD_TYPE_GRP_TXT);
  ASSERT_NE(nullptr, normal);
  tick();
  ASSERT_TRUE(radio.sending); // the OTA copy yields to normal messages
  EXPECT_EQ(radio.transmissions.size(), 2u);
  radio.complete = true; tick(100);
  tick(); // the normal dispatcher's next-TX deadline opens on the following loop
  ASSERT_TRUE(radio.sending);
  radio.complete = true; tick(100);
  EXPECT_EQ(radio.transmissions.size(), 3u); // both normal-message profiles sent
  tick(1000);
  EXPECT_EQ(radio.transmissions.size(), 3u);
  node.ota_speed = 1.0f;
  tick(101); // discard the old slow wait without losing the secondary copy
  EXPECT_EQ(radio.transmissions.size(), 4u);
  EXPECT_EQ(radio.transmissions.back(), 1u);
  radio.complete = true; tick(100);
  EXPECT_EQ(manager.getFreeCount(), 40);
}

TEST_F(DualProfileTest, OtaSpeedScalesRelayWindowButLeavesOrdinaryRelaysAlone) {
  rng.value = 2;
  auto ota_packet = makeFloodPacket(PAYLOAD_TYPE_OTA);
  ota_packet.payload[0] = mesh::ota::OTA_DATA;
  auto ordinary_packet = makeFloodPacket(PAYLOAD_TYPE_GRP_TXT);
  const auto ordinary_delay = node.getRetransmitDelay(&ordinary_packet);
  const auto normal = node.otaRelayDelay(&ota_packet);
  ASSERT_GT(normal, 0u);
  node.ota_speed = 0.5f;
  EXPECT_EQ(node.otaRelayDelay(&ota_packet), normal * 2);
  EXPECT_EQ(node.getRetransmitDelay(&ordinary_packet), ordinary_delay);
  node.ota_speed = 3.0f;
  EXPECT_EQ(node.otaRelayDelay(&ota_packet), (normal + 2) / 3);
  EXPECT_EQ(node.getRetransmitDelay(&ordinary_packet), ordinary_delay);
}

TEST_F(DualProfileTest, OtaAirtimeFollowsParticipatingProfilesInsteadOfScannerVisit) {
  radio.distinguish_airtime = true; // primary 900 ms, secondary 70 ms
  radio.config.secondary_temporary = true;
  radio.config.cross = mesh::RadioCrossMode::Off;
  EXPECT_EQ(node.getOtaPacketAirtime(), 70u);
  radio.selected = 1;
  radio.estimated_airtime = 12345;
  EXPECT_EQ(node.getOtaPacketAirtime(), 70u);
  radio.config.reply_tx = mesh::RADIO_TX_BOTH;
  EXPECT_EQ(node.getOtaPacketAirtime(), 970u);
  radio.config.secondary_temporary = false;
  radio.config.primary_temporary = true;
  radio.config.secondary.mode = mesh::RadioProfileMode::Rx;
  EXPECT_EQ(node.getOtaPacketAirtime(), 900u);
  radio.config.reply_force = true;
  EXPECT_EQ(node.getOtaPacketAirtime(), 970u);
  radio.config.secondary.mode = mesh::RadioProfileMode::Off;
  EXPECT_EQ(node.getOtaPacketAirtime(), 900u);
}

TEST_F(DualProfileTest, PacketBecomingReadyDuringCadCannotSkipItsOwnChannelCheck) {
  node.flood_attempts = 0;
  node.tempRadioActive = true;
  radio.config.cross = mesh::RadioCrossMode::Off;
  auto* normal = node.obtainNewPacket();
  *normal = makeFloodPacket(PAYLOAD_TYPE_GRP_TXT);
  normal->tx_radio = mesh::RADIO_TX_PRIMARY;
  ASSERT_TRUE(node.sendPacket(normal, 1));
  auto* ota = node.obtainNewPacket();
  *ota = makeFloodPacket(PAYLOAD_TYPE_OTA);
  ota->tx_radio = mesh::RADIO_TX_SECONDARY;
  ASSERT_TRUE(node.sendPacket(ota, 0, 20));
  radio.busy_profile = 1;
  radio.sensing_clock = &clock;
  radio.sensing_delay = 50; // secondary becomes ready during primary's clear check
  tick();
  ASSERT_EQ(radio.transmissions.size(), 1u);
  EXPECT_EQ(radio.transmissions.back(), 0u);
  radio.complete = true;
  tick(); tick();
  EXPECT_EQ(radio.transmissions.size(), 1u); // busy secondary still must wait
  radio.busy_profile = -1;
  tick(1000);
  ASSERT_EQ(radio.transmissions.size(), 2u);
  EXPECT_EQ(radio.transmissions.back(), 1u);
}

TEST_F(DualProfileTest, CadCannotPullPacedOtaAheadOfItsQuietDeadline) {
  node.flood_attempts = 0;
  node.tempRadioActive = true;
  node.ota_speed = 0.05f;
  radio.config.cross = mesh::RadioCrossMode::Off;
  ASSERT_NE(queue(PAYLOAD_TYPE_OTA), nullptr);
  tick(); radio.complete = true; tick(100); // 1900 ms quiet, ending at 2001
  auto* ota = node.obtainNewPacket();
  *ota = makeFloodPacket(PAYLOAD_TYPE_OTA);
  ota->tx_radio = mesh::RADIO_TX_SECONDARY;
  ASSERT_TRUE(node.sendPacket(ota, 0));
  auto* normal = node.obtainNewPacket();
  *normal = makeFloodPacket(PAYLOAD_TYPE_GRP_TXT);
  normal->tx_radio = mesh::RADIO_TX_PRIMARY;
  ASSERT_TRUE(node.sendPacket(normal, 1));
  radio.sensing_clock = &clock;
  radio.sensing_delay = 150; // longer than OTA's short queue deferral
  tick();
  ASSERT_EQ(radio.transmissions.size(), 2u);
  EXPECT_EQ(radio.transmissions.back(), 0u); // ordinary packet, not early OTA
  radio.complete = true; tick();
  radio.sensing_delay = 0;
  tick(100);
  EXPECT_EQ(radio.transmissions.size(), 2u);
}

TEST_F(DualProfileTest, CancelledRadioRetryDoesNotWaitForBusyHardware) {
  node.flood_attempts = 0;
  radio.config.cross = mesh::RadioCrossMode::Off;
  radio.fail_next_send = true;
  ASSERT_NE(queue(), nullptr);
  tick(); // failed start retains the packet for one radio retry
  ASSERT_EQ(manager.getFreeCount(), 39);
  node.suppress_tx = true; // OTA apply or a permission change cancels pending work
  radio.hold_prepare_busy = true;
  tick(1000);
  EXPECT_EQ(manager.getFreeCount(), 40);
  EXPECT_TRUE(radio.transmissions.empty());
}

TEST_F(DualProfileTest, CrossFilterKeepsPrimaryTransmissionAndAvoidsCopyAllocation) {
  node.cross_filter_allows = false;
  ASSERT_NE(nullptr, queue());
  ASSERT_EQ(1, manager.getOutboundTotal());
  EXPECT_EQ(0, manager.getOutboundByIdx(0)->radio_profile);
  EXPECT_EQ(1U, node.cross_filter_calls);
}

TEST_F(DualProfileTest, CrossFilterKeepsSecondaryReplyOnItsOrigin) {
  radio.config.cross = mesh::RadioCrossMode::On;
  node.cross_filter_allows = false;
  auto* reply = node.replyOn(1, radio.config.generation[1]);
  ASSERT_TRUE(node.sendPacket(reply, 0));
  ASSERT_EQ(1, manager.getOutboundTotal());
  EXPECT_EQ(1, manager.getOutboundByIdx(0)->radio_profile);
}

TEST_F(DualProfileTest, CrossFilterCannotRerouteAnRxOnlyOrigin) {
  radio.config.cross = mesh::RadioCrossMode::On;
  radio.config.secondary.mode = mesh::RadioProfileMode::Rx;
  node.cross_filter_allows = false;
  auto* reply = node.replyOn(1, radio.config.generation[1]);
  ASSERT_FALSE(node.sendPacket(reply, 0));
  EXPECT_EQ(0, manager.getOutboundTotal());
  EXPECT_EQ(40, manager.getFreeCount());
}

TEST_F(DualProfileTest, BoundCrossRetryCannotBypassChangedFilter) {
  node.cross_filter_allows = false;
  auto* retry = node.obtainNewPacket();
  ASSERT_NE(nullptr, retry);
  *retry = makeFloodPacket(PAYLOAD_TYPE_GRP_TXT);
  retry->radio_bound = true;
  retry->radio_origin = 0;
  retry->radio_profile = 1;
  ASSERT_FALSE(node.sendPacket(retry, 0));
  EXPECT_EQ(0, manager.getOutboundTotal());
  EXPECT_EQ(40, manager.getFreeCount());
}

TEST_F(DualProfileTest, SingleRadioDoesNotConsultCrossFilter) {
  radio.config.secondary.mode = mesh::RadioProfileMode::Off;
  node.cross_filter_allows = false;
  ASSERT_NE(nullptr, queue());
  EXPECT_EQ(1, manager.getOutboundTotal());
  EXPECT_EQ(0U, node.cross_filter_calls);
}

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

TEST(RadioProfiles, AutomaticPreamblesIncludeNominalSwitchingMargin) {
  DualProfileTestRadio radio;
  auto& p = radio.config;
  EXPECT_EQ(600, p.SwitchBudgetUs);
  EXPECT_EQ(300, p.LoopBudgetUs);
  const uint16_t expected[] = {64, 88, 32};
  for (int sf = 7; sf <= 9; ++sf) {
    p.secondary.params.sf = sf;
    EXPECT_EQ(expected[sf-7], p.preamble(1, 32));
    EXPECT_EQ(32, p.preamble(0, 32));
    EXPECT_EQ(0, p.preamble(1, 32) % 8);
    EXPECT_EQ(9421, p.listenUs(0));
    EXPECT_EQ(21847, p.listenUs(1));
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
  EXPECT_EQ(9421, p.listenUs(1));
  EXPECT_EQ(21847, p.listenUs(0));
  p.secondary.params.preamble = 40;
  EXPECT_EQ(30039, p.listenUs(0));
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

TEST_F(DualProfileTest, ExplicitTransmitChoicesOverrideCrossWithoutEnablingRxOnly) {
  node.flood_attempts = 0;
  for (auto cross : {mesh::RadioCrossMode::Auto, mesh::RadioCrossMode::On, mesh::RadioCrossMode::Off}) {
    radio.config.cross = cross;
    for (auto secondary : {mesh::RadioProfileMode::Off, mesh::RadioProfileMode::Rx, mesh::RadioProfileMode::RxTx}) {
      radio.config.secondary.mode = secondary;
      for (bool temporary : {false, true}) {
        radio.config.secondary_temporary = temporary;
        for (uint8_t policy = mesh::RADIO_TX_PRIMARY; policy <= mesh::RADIO_TX_OFF; ++policy) {
          auto* packet = node.obtainNewPacket(); ASSERT_NE(nullptr, packet);
          *packet = makeFloodPacket(PAYLOAD_TYPE_GRP_TXT);
          packet->tx_radio = policy;
          const uint8_t expected = mesh::explicitRadioTxMask(policy, secondary == mesh::RadioProfileMode::RxTx);
          ASSERT_EQ(expected != 0, node.sendFlood(packet));
          uint8_t actual = 0;
          while (manager.getOutboundTotal()) {
            auto* queued = manager.getOutboundByIdx(0);
            actual |= 1U << queued->radio_profile;
            EXPECT_EQ(policy, queued->tx_radio);
            EXPECT_TRUE(node.isPacketRadioCurrent(queued));
            manager.removeOutboundByIdx(0); node.releasePacket(queued);
          }
          EXPECT_EQ(expected, actual);
          EXPECT_EQ(40, manager.getFreeCount());
        }
      }
    }
  }
}

TEST_F(DualProfileTest, ExplicitSecondaryRetriesStayOnSecondaryWithCrossOff) {
  radio.config.cross = mesh::RadioCrossMode::Off;
  radio.config.secondary_temporary = true;
  auto* packet = node.obtainNewPacket(); ASSERT_NE(nullptr, packet);
  *packet = makeFloodPacket(PAYLOAD_TYPE_GRP_TXT);
  packet->tx_radio = mesh::RADIO_TX_SECONDARY;
  ASSERT_TRUE(node.sendFlood(packet));
  for (int i = 0; i < 50; ++i) { radio.complete = true; tick(1000); }
  ASSERT_GE(radio.transmissions.size(), 2U);
  for (auto profile : radio.transmissions) EXPECT_EQ(1, profile);
  EXPECT_EQ(40, manager.getFreeCount());
}

TEST_F(DualProfileTest, InfrastructureReplyChoiceMatrix) {
  for (auto cross : {mesh::RadioCrossMode::Auto, mesh::RadioCrossMode::On, mesh::RadioCrossMode::Off}) {
    radio.config.cross = cross;
    for (auto secondary : {mesh::RadioProfileMode::Off, mesh::RadioProfileMode::Rx, mesh::RadioProfileMode::RxTx}) {
      radio.config.secondary.mode = secondary;
      for (bool temporary : {false,true}) {
        radio.config.secondary_temporary = temporary;
        for (uint8_t policy=0;policy<=mesh::RADIO_TX_OFF;++policy) {
          for (bool force : {false,true}) {
            if (force && policy!=mesh::RADIO_TX_SECONDARY && policy!=mesh::RADIO_TX_BOTH) continue;
            radio.config.reply_tx=policy; radio.config.reply_force=force;
            for (uint8_t origin : {0,1}) {
              auto* packet=node.replyOn(origin,radio.config.generation[origin]);
              ASSERT_NE(nullptr,packet); ASSERT_TRUE(packet->radio_reply);
              const bool can_second=secondary==mesh::RadioProfileMode::RxTx
                  || (force && secondary==mesh::RadioProfileMode::Rx);
              const uint8_t expected=policy==mesh::RADIO_TX_AUTO ? radio.config.transmitMask(origin)
                  : (policy==mesh::RADIO_TX_OFF ? 0 : policy & (can_second ? 3 : 1));
              ASSERT_EQ(expected!=0,node.sendPacket(packet,0));
              uint8_t actual=0;
              while (manager.getOutboundTotal()) {
                auto* queued=manager.removeOutboundByIdx(0);
                actual |= 1U << queued->radio_profile;
                EXPECT_EQ(policy,queued->tx_radio);
                EXPECT_EQ(force,queued->radio_reply_force);
                EXPECT_EQ(origin,queued->radio_origin);
                EXPECT_TRUE(node.isPacketRadioCurrent(queued));
                node.releasePacket(queued);
              }
              EXPECT_EQ(expected,actual); EXPECT_EQ(40,manager.getFreeCount());
            }
          }
        }
      }
    }
  }
}

TEST_F(DualProfileTest, ReplyForceDoesNotEnableForwardedOrUnsolicitedTraffic) {
  node.flood_attempts=0;
  radio.config.cross=mesh::RadioCrossMode::Off;
  radio.config.secondary.mode=mesh::RadioProfileMode::Rx;
  radio.config.reply_tx=mesh::RADIO_TX_BOTH; radio.config.reply_force=true;
  auto raw_packet=makeFloodPacket(PAYLOAD_TYPE_GRP_TXT);
  uint8_t raw[MAX_TRANS_UNIT]; const uint8_t length=raw_packet.writeTo(raw);
  auto* received=node.replyOn(0,radio.config.generation[0]); ASSERT_NE(nullptr,received);
  received->radio_reply_force=true; received->tx_radio=mesh::RADIO_TX_BOTH;
  ASSERT_TRUE(node.tryParsePacket(received,raw,length));
  ASSERT_FALSE(received->radio_reply); ASSERT_FALSE(received->radio_reply_force);
  ASSERT_EQ(mesh::RADIO_TX_AUTO,received->tx_radio);
  ASSERT_TRUE(node.sendPacket(received,0));
  ASSERT_NE(nullptr,queue(PAYLOAD_TYPE_ADVERT));
  ASSERT_EQ(2,manager.getOutboundTotal());
  for (int i=0;i<2;++i) {
    const auto* queued=manager.getOutboundByIdx(i);
    EXPECT_EQ(0,queued->radio_profile); EXPECT_FALSE(queued->radio_reply);
  }
  for (int i=0;i<6;++i) { radio.complete=true; tick(); }
  EXPECT_EQ((std::vector<uint8_t>{0,0}),radio.transmissions);
  EXPECT_EQ(40,manager.getFreeCount());
}

TEST_F(DualProfileTest, ForcedReplySurvivesDriverRetryOnRxOnlySecondary) {
  node.flood_attempts=0;
  radio.config.cross=mesh::RadioCrossMode::Off;
  radio.config.secondary.mode=mesh::RadioProfileMode::Rx;
  radio.config.reply_tx=mesh::RADIO_TX_SECONDARY; radio.config.reply_force=true;
  auto* packet=node.replyOn(0,radio.config.generation[0]); ASSERT_NE(nullptr,packet);
  ASSERT_TRUE(node.sendPacket(packet,0));
  radio.fail_next_send=true;
  for (int i=0;i<12;++i) { radio.complete=true; tick(1000); }
  EXPECT_EQ((std::vector<uint8_t>{1}),radio.transmissions);
  EXPECT_EQ(mesh::RadioProfileMode::Rx,radio.config.secondary.mode);
  EXPECT_EQ(40,manager.getFreeCount());
}

TEST_F(DualProfileTest, BothForcedReplyCopiesKeepSeparateFloodRetries) {
  radio.config.cross=mesh::RadioCrossMode::Off;
  radio.config.secondary.mode=mesh::RadioProfileMode::Rx;
  radio.config.secondary_temporary=true;
  radio.config.reply_tx=mesh::RADIO_TX_BOTH; radio.config.reply_force=true;
  auto* packet=node.replyOn(1,radio.config.generation[1]); ASSERT_NE(nullptr,packet);
  packet->header=PAYLOAD_TYPE_GRP_TXT << PH_TYPE_SHIFT;
  ASSERT_TRUE(node.sendFlood(packet));
  for (int i=0;i<50;++i) { radio.complete=true; tick(1000); }
  unsigned counts[2]={};
  for (auto profile:radio.transmissions) ++counts[profile];
  EXPECT_GE(counts[0],2U); EXPECT_GE(counts[1],2U);
  EXPECT_EQ(40,manager.getFreeCount());
}

TEST_F(DualProfileTest, ForcedReplyRespectsCrossFiltersAndProfileExpiry) {
  radio.config.reply_tx=mesh::RADIO_TX_BOTH; radio.config.reply_force=true;
  radio.config.secondary.mode=mesh::RadioProfileMode::Rx;
  node.cross_filter_allows=false;
  auto* reply=node.replyOn(0,radio.config.generation[0]); ASSERT_NE(nullptr,reply);
  ASSERT_TRUE(node.sendPacket(reply,0)); ASSERT_EQ(1,manager.getOutboundTotal());
  EXPECT_EQ(0,manager.getOutboundByIdx(0)->radio_profile);
  node.releasePacket(manager.removeOutboundByIdx(0));
  node.cross_filter_allows=true;
  reply=node.replyOn(1,radio.config.generation[1]); ASSERT_NE(nullptr,reply);
  ASSERT_TRUE(node.sendPacket(reply,0)); ASSERT_EQ(2,manager.getOutboundTotal());
  radio.config.setSecondary({},false);
  for (int i=0;i<6;++i) { radio.complete=true; tick(); }
  EXPECT_TRUE(radio.transmissions.empty()); // both originated in an expired session
  EXPECT_EQ(40,manager.getFreeCount());
  auto* stale=node.replyOn(1,radio.config.generation[1]-1); ASSERT_NE(nullptr,stale);
  EXPECT_FALSE(node.sendPacket(stale,0));
  EXPECT_EQ(40,manager.getFreeCount());
}

TEST_F(DualProfileTest, ReplyFlagsAreLocalAndResetOnReadAndPoolReuse) {
  auto packet=makeFloodPacket(PAYLOAD_TYPE_RESPONSE);
  uint8_t before[MAX_TRANS_UNIT],after[MAX_TRANS_UNIT];
  const auto size=packet.writeTo(before);
  packet.radio_reply=packet.radio_reply_force=true;
  packet.tx_radio=mesh::RADIO_TX_BOTH;
  EXPECT_EQ(size,packet.writeTo(after)); EXPECT_EQ(0,memcmp(before,after,size));
  EXPECT_TRUE(packet.readFrom(before,size));
  EXPECT_FALSE(packet.radio_reply); EXPECT_FALSE(packet.radio_reply_force);
  EXPECT_EQ(mesh::RADIO_TX_AUTO,packet.tx_radio);
  for (int i=0;i<80;++i) {
    auto* p=node.obtainNewPacket(); ASSERT_NE(nullptr,p);
    EXPECT_FALSE(p->radio_reply); EXPECT_FALSE(p->radio_reply_force);
    p->radio_reply=p->radio_reply_force=true; node.releasePacket(p);
  }
}

TEST_F(DualProfileTest, ReplyAirtimeIncludesForcedRxProfileBeforeAdmission) {
  radio.distinguish_airtime=true;
  radio.config.secondary.mode=mesh::RadioProfileMode::Rx;
  radio.config.reply_tx=mesh::RADIO_TX_BOTH;
  auto* reply=node.replyOn(0,radio.config.generation[0]); ASSERT_NE(nullptr,reply);
  EXPECT_EQ(900U,node.getTransmitAirtime(reply));
  radio.config.reply_force=true;
  EXPECT_EQ(970U,node.getTransmitAirtime(reply));
  radio.config.reply_tx=mesh::RADIO_TX_SECONDARY;
  EXPECT_EQ(70U,node.getTransmitAirtime(reply));
  radio.config.reply_tx=mesh::RADIO_TX_OFF;
  EXPECT_EQ(0U,node.getTransmitAirtime(reply));
  node.releasePacket(reply);
}

TEST_F(DualProfileTest, ExplicitBothSurvivesCrossOffButDisablingSecondaryRetiresItsCopy) {
  node.flood_attempts = 0;
  radio.config.cross = mesh::RadioCrossMode::Off;
  auto* packet = node.obtainNewPacket(); ASSERT_NE(nullptr, packet);
  *packet = makeFloodPacket(PAYLOAD_TYPE_GRP_TXT); packet->tx_radio = mesh::RADIO_TX_BOTH;
  ASSERT_TRUE(node.sendFlood(packet));
  ASSERT_EQ(2, manager.getOutboundTotal());
  auto* copy = manager.getOutboundByIdx(1);
  EXPECT_TRUE(node.isPacketRadioCurrent(copy));
  radio.config.secondary.mode = mesh::RadioProfileMode::Rx;
  EXPECT_FALSE(node.isPacketRadioCurrent(copy));
  for (int i = 0; i < 5; ++i) { radio.complete = true; tick(); }
  EXPECT_EQ((std::vector<uint8_t>{0}), radio.transmissions);
  EXPECT_EQ(40, manager.getFreeCount());
}

TEST_F(DualProfileTest, TransmitPolicyIsLocalAndPoolReuseResetsIt) {
  auto* packet = node.obtainNewPacket(); ASSERT_NE(nullptr, packet);
  *packet = makeFloodPacket(PAYLOAD_TYPE_GRP_TXT);
  uint8_t before[MAX_TRANS_UNIT], after[MAX_TRANS_UNIT];
  const auto length = packet->writeTo(before);
  packet->tx_radio = mesh::RADIO_TX_OFF;
  EXPECT_EQ(length, packet->writeTo(after));
  EXPECT_EQ(0, memcmp(before, after, length));
  node.releasePacket(packet);
  for (int i = 0; i < 40; ++i) {
    auto* reused = node.obtainNewPacket(); ASSERT_NE(nullptr, reused);
    EXPECT_EQ(mesh::RADIO_TX_AUTO, reused->tx_radio);
    reused->tx_radio = mesh::RADIO_TX_SECONDARY;
    node.releasePacket(reused);
  }
}

TEST_F(DualProfileTest, MessageTimeoutAirtimeUsesSelectedProfilesInsteadOfScanPosition) {
  radio.distinguish_airtime = true;
  radio.config.cross = mesh::RadioCrossMode::Off;
  auto* packet = node.obtainNewPacket(); ASSERT_NE(nullptr, packet);
  *packet = makeFloodPacket(PAYLOAD_TYPE_TXT_MSG);
  for (uint8_t scan : {0, 1}) {
    radio.selected = scan;
    packet->tx_radio = mesh::RADIO_TX_SECONDARY;
    EXPECT_EQ(70U, node.getTransmitAirtime(packet));
    packet->tx_radio = mesh::RADIO_TX_PRIMARY;
    EXPECT_EQ(900U, node.getTransmitAirtime(packet));
    packet->tx_radio = mesh::RADIO_TX_BOTH;
    EXPECT_EQ(970U, node.getTransmitAirtime(packet));
    packet->tx_radio = mesh::RADIO_TX_AUTO;
    EXPECT_EQ(900U, node.getTransmitAirtime(packet));
  }
  node.releasePacket(packet);
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

TEST_F(DualProfileTest, TemporaryActivityIncludesEitherProfileAndEndsAfterBothExpire) {
  for (bool primary : {false,true}) {
    for (bool secondary : {false,true}) {
      node.tempRadioActive=primary;
      radio.config.secondary_temporary=secondary;
      EXPECT_EQ(primary || secondary,node.isAnyTempRadioActive());
    }
  }
  node.tempRadioActive=false;
  radio.config.setSecondary({},false);
  EXPECT_FALSE(node.isAnyTempRadioActive());
}

TEST_F(DualProfileTest, ReceiveOnlyNeverTransmitsSecondary) {
  radio.config.secondary.mode = mesh::RadioProfileMode::Rx;
  ASSERT_NE(nullptr, queue());
  EXPECT_EQ(1, manager.getOutboundTotal());
  tick(); EXPECT_EQ((std::vector<uint8_t>{0}), radio.transmissions);
}

TEST_F(DualProfileTest, AsyncOtaResponseAdmissionMaskMatchesTheActualQueuedCopies) {
  radio.config.cross=mesh::RadioCrossMode::Off;
  radio.config.secondary_temporary=true;
  radio.config.reply_tx=mesh::RADIO_TX_BOTH;
  for (auto mode : {mesh::RadioProfileMode::Rx,mesh::RadioProfileMode::RxTx}) {
    radio.config.secondary.mode=mode;
    for (bool force : {false,true}) {
      radio.config.reply_force=force;
      for (uint8_t type : {mesh::ota::OTA_REQ,mesh::ota::OTA_DATA,mesh::ota::OTA_PROOF,
                           mesh::ota::OTA_HAVE,mesh::ota::OTA_QUERY}) {
        auto* p=node.obtainNewPacket(); ASSERT_NE(nullptr,p);
        *p=makeFloodPacket(PAYLOAD_TYPE_OTA); p->payload[0]=type;
        p->radio_reply=mesh::ota::ota_is_response_message(type);
        ASSERT_TRUE(p->radio_local); // no RX call stack
        const uint8_t mask=node.getTransmitProfileMask(p);
        EXPECT_EQ(p->radio_reply ? ((force || mode==mesh::RadioProfileMode::RxTx) ? 3 : 1)
            : (mode==mesh::RadioProfileMode::RxTx ? 2 : 0),mask);
        ASSERT_EQ(mask!=0,node.sendPacket(p,0));
        uint8_t actual=0;
        while (manager.getOutboundTotal()) {
          auto* queued=manager.removeOutboundByIdx(0);
          actual |= 1U << queued->radio_profile;
          node.releasePacket(queued);
        }
        EXPECT_EQ(mask,actual); EXPECT_EQ(40,manager.getFreeCount());
      }
    }
  }
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

TEST_F(DualProfileTest, CarrierKeepsQueuesAndRecoveryPausedButServicesDeadline) {
  node.flood_attempts = 0;
  radio.config.cross = mesh::RadioCrossMode::Off;
  ASSERT_NE(nullptr, queue());
  const int queued = manager.getOutboundTotal();
  radio.carrier = true;
  tick(10000); tick(10000);
  EXPECT_GE(radio.carrier_services, 2u);
  EXPECT_EQ(0u, radio.recoveries);
  EXPECT_EQ(queued, manager.getOutboundTotal());
  EXPECT_TRUE(radio.transmissions.empty());
  radio.carrier = false;
  tick();
  EXPECT_EQ((std::vector<uint8_t>{0}), radio.transmissions);
  radio.complete = true; tick();
  EXPECT_EQ(40, manager.getFreeCount());
}

TEST_F(DualProfileTest, CarrierStartedInReceiveCallbackDoesNotDequeuePacket) {
  node.flood_attempts = 0;
  radio.config.cross = mesh::RadioCrossMode::Off;
  ASSERT_NE(nullptr, queue());
  const int queued = manager.getOutboundTotal();
  radio.carrier_on_receive = true;
  tick();
  EXPECT_TRUE(radio.carrier);
  EXPECT_EQ(queued, manager.getOutboundTotal());
  EXPECT_TRUE(radio.transmissions.empty());
  radio.carrier = false;
  tick();
  EXPECT_EQ((std::vector<uint8_t>{0}), radio.transmissions);
}
