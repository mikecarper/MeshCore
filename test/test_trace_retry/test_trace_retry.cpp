#include <gtest/gtest.h>

#include <Ed25519.h>
#include <Mesh.h>
#include <helpers/ClockSyncUtils.h>
#include <helpers/FloodAdvertCLI.h>
#include <helpers/StaticPoolPacketManager.h>
#include <helpers/ota/OtaFormat.h>
#include <vector>

class TraceTestClock : public mesh::MillisecondClock {
public:
  unsigned long now = 0;
  unsigned long getMillis() override { return now; }
};

class TraceTestRTC : public mesh::RTCClock {
public:
  uint32_t now = 0;
  uint32_t getCurrentTime() override { return now; }
  void setCurrentTime(uint32_t time) override { now = time; }
};

class TraceTestRNG : public mesh::RNG {
public:
  uint32_t value = 0;
  void random(uint8_t* dest, size_t sz) override {
    for (size_t offset = 0; offset < sz; offset++) {
      dest[offset] = (uint8_t)(value >> (8 * (offset % sizeof(value))));
    }
  }
};

class TraceTestRadio : public mesh::Radio {
public:
  bool sending = false;
  bool complete = false;

  int recvRaw(uint8_t*, int) override { return 0; }
  uint32_t getEstAirtimeFor(int) override { return 10; }
  float packetScore(float, int) override { return 0; }
  bool startSendRaw(const uint8_t*, int) override {
    sending = true;
    return true;
  }
  bool isSendComplete() override { return complete; }
  void onSendFinished() override {
    sending = false;
    complete = false;
  }
  bool isInRecvMode() const override { return !sending; }
};

class TraceTestTables : public mesh::MeshTables {
public:
  bool wasSeen(const mesh::Packet*) override { return false; }
  void markSeen(const mesh::Packet*) override { }
  void markSent(const mesh::Packet*) override { }
  void clear(const mesh::Packet*) override { }
};

class ForwardingTestTables : public mesh::MeshTables {
public:
  bool seen = false;
  int mark_seen_calls = 0;

  bool wasSeen(const mesh::Packet*) override { return seen; }
  void markSeen(const mesh::Packet*) override {
    seen = true;
    mark_seen_calls++;
  }
  void markSent(const mesh::Packet*) override { }
  void clear(const mesh::Packet*) override { }
};

class TraceTestMesh : public mesh::Mesh {
public:
  bool forwardFloods = false;
  bool floodRetriesAllowed = true;
  bool groupPacketObserved = false;
  bool tempRadioActive = false;
  bool rejectFloods = false;

  TraceTestMesh(mesh::Radio& radio, mesh::MillisecondClock& ms, mesh::RNG& rng,
                mesh::RTCClock& rtc, mesh::PacketManager& mgr, mesh::MeshTables& tables)
    : mesh::Mesh(radio, ms, rng, rtc, mgr, tables) { }

  uint8_t airtimeFactor(const mesh::Packet* packet) const {
    return getDirectRetryPacketAirtimeFactor(packet);
  }

  uint8_t floodPathGate(const mesh::Packet* packet, uint8_t general_gate,
                        uint8_t group_data_gate) const {
    return applyGroupDataFloodRetryPathGate(packet, general_gate, group_data_gate);
  }

  uint8_t floodAttemptLimit(const mesh::Packet* packet, uint8_t role_max_attempts) const {
    return applyFloodRetryAttemptPolicy(packet, role_max_attempts);
  }

  uint32_t floodAttemptDelay(const mesh::Packet* packet, uint8_t attempt_idx = 0) {
    return getFloodRetryAttemptDelay(packet, attempt_idx);
  }

  uint32_t otaRelayDelay(const mesh::Packet* packet) {
    return getOtaRetransmitDelay(packet);
  }

  int receiveDelay(const mesh::Packet* packet, float score, uint32_t air_time) {
    return calcRxDelayForPacket(packet, score, air_time);
  }

  uint32_t cadRetryDelay() const {
    return getCADFailRetryDelay();
  }

  void completePacketSend(mesh::Packet* packet) {
    onSendComplete(packet);
  }

  void trackMessageRetry(const mesh::Packet* packet,
                         const uint8_t message_key[MAX_HASH_SIZE],
                         uint32_t message_timestamp) {
    replaceActiveMessageRetries(packet, message_key, message_timestamp);
  }

  mesh::DispatcherAction receivePacket(mesh::Packet* packet) {
    return onRecvPacket(packet);
  }

  mesh::DispatcherAction routePacket(mesh::Packet* packet) {
    return routeRecvPacket(packet);
  }

  bool allowPacketForward(const mesh::Packet*) override {
    return forwardFloods;
  }

  bool filterRecvFloodPacket(mesh::Packet*) override {
    return rejectFloods;
  }

  bool isTempRadioActive() const override {
    return tempRadioActive;
  }

  bool canTransmit(const mesh::Packet* packet) const {
    return allowPacketTransmit(packet);
  }

  bool allowFloodRetry(const mesh::Packet*) const override {
    return floodRetriesAllowed;
  }

  void onGroupPacketRecv(mesh::Packet*) override {
    groupPacketObserved = true;
  }
};

static mesh::Packet makeFloodPacket(uint8_t payload_type) {
  mesh::Packet packet;
  packet.header = ROUTE_TYPE_FLOOD | (payload_type << PH_TYPE_SHIFT);
  packet.setPathHashSizeAndCount(1, 0);
  packet.payload_len = 1;
  packet.payload[0] = 0x42;
  return packet;
}

class RetryCodingRateRadio : public TraceTestRadio {
public:
  uint8_t cr = 5;
  std::vector<uint8_t> transmitted_crs;

  bool setCodingRate(uint8_t value) override { cr = value; return true; }
  bool startSendRaw(const uint8_t* bytes, int length) override {
    transmitted_crs.push_back(cr);
    return TraceTestRadio::startSendRaw(bytes, length);
  }
};

class RetryCodingRateMesh : public TraceTestMesh {
public:
  using TraceTestMesh::TraceTestMesh;
  uint8_t base_cr = 5;
  uint8_t flood_attempts = 15;
  uint8_t direct_attempts = 15;

  uint8_t getDefaultTxCodingRate() const override { return base_cr; }
  uint8_t getFloodRetryMaxAttempts(const mesh::Packet*) const override { return flood_attempts; }
  uint8_t getDirectRetryMaxAttempts(const mesh::Packet*) const override { return direct_attempts; }
  bool allowDirectRetry(const mesh::Packet*, const uint8_t*, uint8_t) const override { return true; }

  uint8_t floodCR(const mesh::Packet& packet, uint8_t attempt) {
    mesh::Packet retry = packet;
    configureFloodRetryPacket(&retry, &packet, attempt);
    return retry.tx_cr;
  }
  uint8_t directCR(uint8_t attempt) {
    mesh::Packet original, retry;
    configureDirectRetryPacket(&retry, &original, attempt);
    return retry.tx_cr;
  }
  void disableFloodRetries() {
    flood_attempts = 0;
    floodRetriesAllowed = false;
    cancelAllFloodRetries();
  }
};

class RetryCodingRateTest : public testing::Test {
protected:
  TraceTestClock clock;
  TraceTestRTC rtc;
  TraceTestRNG rng;
  RetryCodingRateRadio radio;
  TraceTestTables tables;
  StaticPoolPacketManager manager{12};
  RetryCodingRateMesh node{radio, clock, rng, rtc, manager, tables};

  void SetUp() override { node.begin(); }

  void transmitNext() {
    clock.now += 10000;
    node.loop();
    ASSERT_TRUE(radio.sending);
    radio.complete = true;
    ++clock.now;
    node.loop();
    EXPECT_FALSE(radio.sending);
    EXPECT_EQ(node.base_cr, radio.cr);  // the next packet and RX use the normal CR
  }

  void queueFlood(bool scoped = false) {
    auto* packet = manager.allocNew();
    ASSERT_NE(nullptr, packet);
    *packet = makeFloodPacket(PAYLOAD_TYPE_GRP_TXT);
    if (scoped) {
      uint16_t codes[] = {0x1234, 0x5678};
      ASSERT_TRUE(node.sendFlood(packet, codes, 0, 3));
    } else {
      ASSERT_TRUE(node.sendFlood(packet));
    }
  }
};

TEST_F(RetryCodingRateTest, HopZeroMatchesDirectLadderFromEveryRadioCR) {
  for (uint8_t cr : {4, 5, 6, 7, 8}) {
    node.base_cr = cr;
    for (uint8_t route : {ROUTE_TYPE_FLOOD, ROUTE_TYPE_TRANSPORT_FLOOD}) {
      auto packet = makeFloodPacket(PAYLOAD_TYPE_GRP_TXT);
      packet.header = route | (PAYLOAD_TYPE_GRP_TXT << PH_TYPE_SHIFT);
      packet.tx_cr = 8;  // a previous retry must not advance the ladder twice
      for (uint8_t hash_size : {1, 2, 3}) {
        packet.setPathHashSizeAndCount(hash_size, 0);
        for (uint8_t attempt = 1; attempt <= 15; ++attempt) {
          SCOPED_TRACE(testing::Message() << "CR" << int(cr) << " retry " << int(attempt));
          EXPECT_EQ(node.directCR(attempt), node.floodCR(packet, attempt));
        }
      }
    }
  }
}

TEST_F(RetryCodingRateTest, ForwardedFloodsKeepActiveCRAtEveryAttempt) {
  auto packet = makeFloodPacket(PAYLOAD_TYPE_GRP_TXT);
  packet.tx_cr = 8;
  for (uint8_t cr : {5, 6, 7, 8}) {
    node.base_cr = cr;
    for (uint8_t hash_size : {1, 2, 3}) {
      for (uint8_t hops : {1, 2, 8}) {
        packet.setPathHashSizeAndCount(hash_size, hops);
        for (uint8_t attempt = 1; attempt <= 15; ++attempt) {
          EXPECT_EQ(cr, node.floodCR(packet, attempt));
        }
      }
    }
  }
}

TEST_F(RetryCodingRateTest, HopZeroCR5ScheduleReachesTheRadioForEachPresetBudget) {
  // infra, rooftop, mobile hop-zero budgets; the production preset/role
  // calculations are exercised separately by test_retry_cr_presets.py.
  for (uint8_t attempts : {2, 6, 15}) {
    for (bool scoped : {false, true}) {
      node.begin();
      node.flood_attempts = attempts;
      radio.transmitted_crs.clear();
      queueFlood(scoped);
      std::vector<uint8_t> expected{5};  // initial transmission
      for (uint8_t i = 0; i <= attempts; ++i) {
        if (i > 0) expected.push_back(i == 1 ? 5 : i <= 3 ? 7 : 8);
        transmitNext();
      }
      EXPECT_EQ(expected, radio.transmitted_crs);
      EXPECT_EQ(0, manager.getOutboundTotal());
    }
  }
}

TEST_F(RetryCodingRateTest, DisabledFloodRetrySendsOnlyTheInitialPacket) {
  node.disableFloodRetries();
  queueFlood();
  transmitNext();
  EXPECT_EQ((std::vector<uint8_t>{5}), radio.transmitted_crs);
  EXPECT_EQ(0, manager.getOutboundTotal());
}

TEST_F(RetryCodingRateTest, DisablingFloodRetryMidSequenceCancelsEscalatedCopies) {
  queueFlood();
  transmitNext();
  transmitNext();
  transmitNext();
  ASSERT_EQ(1, manager.getOutboundTotal());
  EXPECT_EQ(7, manager.getOutboundByIdx(0)->tx_cr);
  node.disableFloodRetries();
  clock.now += 10000;
  node.loop();
  EXPECT_FALSE(radio.sending);
  EXPECT_EQ(0, manager.getOutboundTotal());
  EXPECT_EQ((std::vector<uint8_t>{5, 5, 7}), radio.transmitted_crs);
}

TEST_F(RetryCodingRateTest, DirectTransmissionsKeepTheirScheduleWithFloodRetryOff) {
  for (uint8_t attempts : {4, 15}) {
    for (bool flood_enabled : {true, false}) {
      node.begin();
      node.direct_attempts = attempts;
      node.floodRetriesAllowed = flood_enabled;
      node.flood_attempts = flood_enabled ? 15 : 0;
      radio.transmitted_crs.clear();
      auto* packet = node.obtainNewPacket();
      ASSERT_NE(nullptr, packet);
      packet->header = PAYLOAD_TYPE_ACK << PH_TYPE_SHIFT;
      packet->payload_len = 4;
      memset(packet->payload, 0x34, packet->payload_len);
      const uint8_t path[] = {0x12, 0x34};
      ASSERT_TRUE(node.sendDirect(packet, path, sizeof(path)));
      std::vector<uint8_t> expected{5};
      for (uint8_t i = 0; i <= attempts; ++i) {
        if (i > 0) expected.push_back(i == 1 ? 5 : i <= 3 ? 7 : 8);
        transmitNext();
      }
      EXPECT_EQ(expected, radio.transmitted_crs);
      EXPECT_EQ(0, manager.getOutboundTotal());
    }
  }
}

static mesh::Packet makeOtaManifestFragment(uint8_t format_version) {
  mesh::Packet packet = makeFloodPacket(PAYLOAD_TYPE_OTA);
  packet.payload_len = 12;
  packet.payload[0] = mesh::ota::OTA_MANIFEST;
  packet.payload[1] = 0x11;
  packet.payload[2] = 0x22;
  packet.payload[3] = 0x33;
  packet.payload[4] = 0x44;
  packet.payload[5] = 0;  // fragment index
  packet.payload[6] = 2;  // fragment count
  memcpy(packet.payload + 7, mesh::ota::MOTA_MAGIC, sizeof(mesh::ota::MOTA_MAGIC));
  packet.payload[11] = format_version;
  return packet;
}

TEST(RepeaterTransport, UnknownFloodPayloadIsRelayedWhenForwardingAllowsIt) {
  TraceTestClock clock;
  TraceTestRTC rtc;
  TraceTestRNG rng;
  TraceTestRadio radio;
  ForwardingTestTables tables;
  StaticPoolPacketManager manager(12);
  TraceTestMesh node(radio, clock, rng, rtc, manager, tables);
  node.forwardFloods = true;

  mesh::Packet packet = makeFloodPacket(0x0D);  // deliberately unassigned payload type
  mesh::DispatcherAction action = node.receivePacket(&packet);

  EXPECT_NE(ACTION_RELEASE, action);
  EXPECT_EQ(1, packet.getPathHashCount());
  EXPECT_EQ(1, tables.mark_seen_calls);
}

TEST(RepeaterTransport, UnknownFloodPayloadHonorsReceiveAndForwardingRejections) {
  TraceTestClock clock;
  TraceTestRTC rtc;
  TraceTestRNG rng;
  TraceTestRadio radio;
  ForwardingTestTables tables;
  StaticPoolPacketManager manager(12);
  TraceTestMesh node(radio, clock, rng, rtc, manager, tables);

  mesh::Packet filtered = makeFloodPacket(0x0D);
  node.forwardFloods = true;
  node.rejectFloods = true;
  EXPECT_EQ(ACTION_RELEASE, node.receivePacket(&filtered));
  EXPECT_EQ(0, tables.mark_seen_calls);

  mesh::Packet forwarding_disabled = makeFloodPacket(0x0D);
  node.rejectFloods = false;
  node.forwardFloods = false;
  EXPECT_EQ(ACTION_RELEASE, node.receivePacket(&forwarding_disabled));
  EXPECT_EQ(1, tables.mark_seen_calls);
}

TEST(RepeaterTransport, OtaDiscoveryRelaysInBackgroundOnlyDuringTempRadio) {
  TraceTestClock clock;
  TraceTestRTC rtc;
  TraceTestRNG rng;
  TraceTestRadio radio;
  ForwardingTestTables tables;
  StaticPoolPacketManager manager(12);
  TraceTestMesh node(radio, clock, rng, rtc, manager, tables);
  node.forwardFloods = true;

  mesh::Packet packet = makeFloodPacket(PAYLOAD_TYPE_OTA);
  packet.payload[0] = mesh::ota::OTA_ADV;
  EXPECT_FALSE(node.canTransmit(&packet));
  EXPECT_EQ(ACTION_RELEASE, node.receivePacket(&packet));
  EXPECT_EQ(0, tables.mark_seen_calls);
  EXPECT_EQ(0, packet.getPathHashCount());

  node.tempRadioActive = true;
  EXPECT_TRUE(node.canTransmit(&packet));
  mesh::DispatcherAction action = node.receivePacket(&packet);
  EXPECT_NE(ACTION_RELEASE, action);
  EXPECT_EQ(OTA_TX_PRIORITY, (action >> 24) - 1);
  EXPECT_EQ(1, tables.mark_seen_calls);
  EXPECT_EQ(1, packet.getPathHashCount());

  node.tempRadioActive = false;
  EXPECT_FALSE(node.canTransmit(&packet));  // queued-near-expiry packets cannot leak onto the normal channel
}

TEST(RepeaterTransport, AppV2AndBootV3ShareOta0cTempRadioRelayPolicy) {
  // The package format is metadata inside an OTA_MANIFEST fragment. It must
  // never select a second mesh payload type or bypass the repeater default
  // `flood.filter.1 0x0C all suspend=tempradio` policy.
  ASSERT_EQ(0x0C, PAYLOAD_TYPE_OTA);
  const uint8_t formats[] = {
    mesh::ota::MOTA_APP_FORMAT_VER,
    mesh::ota::MOTA_BOOT_FORMAT_VER,
  };

  for (uint8_t format : formats) {
    TraceTestClock clock;
    TraceTestRTC rtc;
    TraceTestRNG rng;
    TraceTestRadio radio;
    ForwardingTestTables tables;
    StaticPoolPacketManager manager(12);
    TraceTestMesh node(radio, clock, rng, rtc, manager, tables);
    node.forwardFloods = true;

    mesh::Packet packet = makeOtaManifestFragment(format);
    ASSERT_EQ(PAYLOAD_TYPE_OTA, packet.getPayloadType());
    ASSERT_EQ(format, packet.payload[11]);
    EXPECT_EQ(ACTION_RELEASE, node.receivePacket(&packet));
    EXPECT_EQ(0, tables.mark_seen_calls);

    node.tempRadioActive = true;
    mesh::DispatcherAction action = node.receivePacket(&packet);
    EXPECT_NE(ACTION_RELEASE, action);
    EXPECT_EQ(OTA_TRANSFER_TX_PRIORITY, (action >> 24) - 1);
    EXPECT_EQ(1, tables.mark_seen_calls);
    EXPECT_EQ(1, packet.getPathHashCount());
  }
}

TEST(RepeaterTransport, OtaTransferRelaysAsPrimaryTrafficWithoutOtaManager) {
  TraceTestClock clock;
  TraceTestRTC rtc;
  TraceTestRNG rng;
  TraceTestRadio radio;
  ForwardingTestTables tables;
  StaticPoolPacketManager manager(OTA_FWD_MIN_FREE);  // exactly at the discovery-shedding threshold
  TraceTestMesh node(radio, clock, rng, rtc, manager, tables);
  node.forwardFloods = true;
  node.tempRadioActive = true;

  mesh::Packet request = makeFloodPacket(PAYLOAD_TYPE_OTA);
  request.payload[0] = mesh::ota::OTA_REQ;
  ASSERT_EQ(OTA_FWD_MIN_FREE, manager.getFreeCount());
  mesh::DispatcherAction action = node.receivePacket(&request);

  EXPECT_NE(ACTION_RELEASE, action);
  EXPECT_EQ(OTA_TRANSFER_TX_PRIORITY, (action >> 24) - 1);
  EXPECT_EQ(1, request.getPathHashCount());
}

TEST(RepeaterTransport, OtaDiscoveryRelayKeepsCollisionJitter) {
  TraceTestClock clock;
  TraceTestRTC rtc;
  TraceTestRNG rng;
  TraceTestRadio radio;                     // fixed 10 ms packet airtime
  ForwardingTestTables tables;
  StaticPoolPacketManager manager(12);
  TraceTestMesh node(radio, clock, rng, rtc, manager, tables);
  mesh::Packet packet = makeFloodPacket(PAYLOAD_TYPE_OTA);
  packet.payload[0] = mesh::ota::OTA_ADV;

  rng.value = 0;
  EXPECT_EQ(node.otaRelayDelay(&packet), 3u);  // ceil(0.25 * 10 ms)
  rng.value = 2;                              // selects the last value in [3, 5]
  EXPECT_EQ(node.otaRelayDelay(&packet), 5u);  // 0.5 * 10 ms
}

TEST(RepeaterTransport, OtaTransferRelayKeepsConfiguredCollisionJitter) {
  TraceTestClock clock;
  TraceTestRTC rtc;
  TraceTestRNG rng;
  TraceTestRadio radio;                         // fixed 10 ms packet airtime
  ForwardingTestTables tables;
  StaticPoolPacketManager manager(12);
  TraceTestMesh node(radio, clock, rng, rtc, manager, tables);
  node.tempRadioActive = true;
  node.forwardFloods = true;
  auto make_request = []() {
    mesh::Packet request = makeFloodPacket(PAYLOAD_TYPE_OTA);
    request.payload_len = 9;
    request.payload[0] = 0x06;                  // OTA_REQ
    for (uint8_t i = 1; i < request.payload_len; i++) request.payload[i] = i;
    return request;
  };

  mesh::Packet request = make_request();
  node.receivePacket(&request);                  // first request establishes the key
  rng.value = 3;
  EXPECT_EQ(node.otaRelayDelay(&request), 15u);
  for (uint32_t retry = 1; retry <= 3; retry++) {
    clock.now = retry * 3000;
    request = make_request();                    // a fresh origin copy has the same zero-hop request key
    node.receivePacket(&request);                // frequent repeats raise one level each
  }
  rng.value = 3;
  EXPECT_EQ(node.otaRelayDelay(&request), 15u);  // request pressure does not widen primary traffic

  clock.now = 100000;
  rng.value = 1;
  EXPECT_EQ(node.otaRelayDelay(&request), 5u);
}

TEST(RepeaterTransport, TempRadioOtaBypassesReceiveHoldoffAndUsesFastCadRetry) {
  TraceTestClock clock;
  TraceTestRTC rtc;
  TraceTestRNG rng;
  TraceTestRadio radio;                         // fixed 10 ms full-packet airtime
  ForwardingTestTables tables;
  StaticPoolPacketManager manager(12);
  TraceTestMesh node(radio, clock, rng, rtc, manager, tables);
  mesh::Packet packet = makeFloodPacket(PAYLOAD_TYPE_OTA);
  packet.payload[0] = mesh::ota::OTA_DATA;

  EXPECT_GT(node.receiveDelay(&packet, 0.0f, 10), 0);
  EXPECT_EQ(node.cadRetryDelay(), 120u);
  node.tempRadioActive = true;
  EXPECT_EQ(node.receiveDelay(&packet, 0.0f, 10), 0);
  EXPECT_EQ(node.cadRetryDelay(), 5u);
}

TEST(RepeaterTransport, OpaqueKnownFloodPayloadsAreRelayed) {
  TraceTestClock clock;
  TraceTestRTC rtc;
  TraceTestRNG rng;
  TraceTestRadio radio;
  StaticPoolPacketManager manager(12);

  {
    ForwardingTestTables tables;
    TraceTestMesh node(radio, clock, rng, rtc, manager, tables);
    node.forwardFloods = true;
    mesh::Packet custom = makeFloodPacket(PAYLOAD_TYPE_RAW_CUSTOM);
    EXPECT_NE(ACTION_RELEASE, node.receivePacket(&custom));
    EXPECT_EQ(1, custom.getPathHashCount());
  }

  {
    ForwardingTestTables tables;
    TraceTestMesh node(radio, clock, rng, rtc, manager, tables);
    node.forwardFloods = true;
    mesh::Packet multipart = makeFloodPacket(PAYLOAD_TYPE_MULTIPART);
    multipart.payload_len = 3;
    multipart.payload[0] = PAYLOAD_TYPE_TXT_MSG;
    EXPECT_NE(ACTION_RELEASE, node.receivePacket(&multipart));
    EXPECT_EQ(1, multipart.getPathHashCount());
  }
}

TEST(RTCClock, UniqueSequenceCanFollowAnIntentionalBackwardCorrection) {
  TraceTestRTC rtc;
  rtc.now = 100;
  EXPECT_EQ(100U, rtc.getCurrentTimeUnique());
  EXPECT_EQ(101U, rtc.getCurrentTimeUnique());

  rtc.setCurrentTime(50);
  rtc.resetUniqueTime(50);
  EXPECT_EQ(50U, rtc.getCurrentTimeUnique());
}

TEST(ClockSyncConsensus, EightVsEightSplitDoesNotChooseTheUpperMedian) {
  uint32_t values[16];
  for (int i = 0; i < 8; i++) values[i] = 1000;
  for (int i = 8; i < 16; i++) values[i] = 5000;

  mesh::ClockSyncConsensusResult result =
      mesh::evaluateClockSyncConsensus(values, 16, 9, 600);
  EXPECT_FALSE(result.consensus);
  EXPECT_EQ(16, result.fresh_count);
  EXPECT_EQ(8, result.agreeing_count);
  EXPECT_EQ(9, result.required_count);
}

TEST(ClockSyncConsensus, NineVsSevenStrictMajorityIsAccepted) {
  uint32_t values[16];
  for (int i = 0; i < 7; i++) values[i] = 1000;
  for (int i = 7; i < 16; i++) values[i] = 5000;

  mesh::ClockSyncConsensusResult result =
      mesh::evaluateClockSyncConsensus(values, 16, 9, 600);
  EXPECT_TRUE(result.consensus);
  EXPECT_EQ(5000U, result.estimate);
  EXPECT_EQ(9, result.agreeing_count);
  EXPECT_EQ(9, result.required_count);
}

TEST(ClockSyncConsensus, ConfiguredEightStillCannotAcceptAnEightVsEightSplit) {
  uint32_t values[16];
  for (int i = 0; i < 8; i++) values[i] = 1000;
  for (int i = 8; i < 16; i++) values[i] = 5000;

  mesh::ClockSyncConsensusResult result =
      mesh::evaluateClockSyncConsensus(values, 16, 8, 600);
  EXPECT_FALSE(result.consensus);
  EXPECT_EQ(9, result.required_count);
}

TEST(ClockSyncPathPolicy, NormalModeRequiresUniquePaths) {
  EXPECT_TRUE(mesh::clockSyncRequiresUniquePath(false));
}

TEST(ClockSyncPathPolicy, EdgeModeAllowsOnePath) {
  EXPECT_FALSE(mesh::clockSyncRequiresUniquePath(true));
}

TEST(ClockSyncDefaults, DriftCorrectionThresholdIsTenMinutes) {
  EXPECT_EQ(600U, mesh::CLOCK_SYNC_DRIFT_DEFAULT_SECONDS);
}

TEST(MeshReceiveHooks, GroupPacketIsObservedWhenForwardingIsDisabled) {
  TraceTestClock clock;
  TraceTestRTC rtc;
  TraceTestRNG rng;
  TraceTestRadio radio;
  TraceTestTables tables;
  StaticPoolPacketManager manager(12);
  TraceTestMesh node(radio, clock, rng, rtc, manager, tables);

  mesh::Packet packet;
  packet.header = ROUTE_TYPE_FLOOD | (PAYLOAD_TYPE_GRP_TXT << PH_TYPE_SHIFT);
  packet.payload_len = 1 + CIPHER_MAC_SIZE + CIPHER_BLOCK_SIZE;
  memset(packet.payload, 0, packet.payload_len);

  ASSERT_FALSE(node.forwardFloods);
  node.receivePacket(&packet);
  EXPECT_TRUE(node.groupPacketObserved);
}

static mesh::Packet* makeTrace(TraceTestMesh& node, uint32_t tag, uint32_t auth,
                               const uint8_t* route, uint8_t route_len) {
  mesh::Packet* packet = node.createTrace(tag, auth, 0);
  EXPECT_NE(packet, nullptr);
  if (packet == nullptr) return nullptr;
  EXPECT_TRUE(node.sendDirect(packet, route, route_len));
  return packet;
}

static mesh::Packet* makeDirectText(TraceTestMesh& node, uint8_t payload_marker,
                                    const uint8_t* route, uint8_t route_len) {
  mesh::Packet* packet = node.obtainNewPacket();
  EXPECT_NE(packet, nullptr);
  if (packet == nullptr) return nullptr;
  packet->header = PAYLOAD_TYPE_TXT_MSG << PH_TYPE_SHIFT;
  packet->payload_len = 3;
  packet->payload[0] = 0xA1;
  packet->payload[1] = 0xB2;
  packet->payload[2] = payload_marker;
  EXPECT_TRUE(node.sendDirect(packet, route, route_len));
  return packet;
}

static mesh::Packet* makeFloodText(TraceTestMesh& node, uint8_t payload_marker) {
  mesh::Packet* packet = node.obtainNewPacket();
  EXPECT_NE(packet, nullptr);
  if (packet == nullptr) return nullptr;
  packet->header = PAYLOAD_TYPE_TXT_MSG << PH_TYPE_SHIFT;
  packet->payload_len = 3;
  packet->payload[0] = 0xA1;
  packet->payload[1] = 0xB2;
  packet->payload[2] = payload_marker;
  EXPECT_TRUE(node.sendFlood(packet));
  return packet;
}

static void finishCurrentSend(TraceTestMesh& node, TraceTestClock& clock,
                              TraceTestRadio& radio) {
  clock.now++;
  node.loop();
  ASSERT_TRUE(radio.sending);
  radio.complete = true;
  clock.now++;
  node.loop();
  ASSERT_FALSE(radio.sending);
}

static void initSelfAdvert(TraceTestMesh& node, mesh::Packet* packet, uint8_t marker) {
  ASSERT_NE(packet, nullptr);
  if (packet == nullptr) return;
  packet->header = PAYLOAD_TYPE_ADVERT << PH_TYPE_SHIFT;
  packet->payload_len = PUB_KEY_SIZE + sizeof(uint32_t) + SIGNATURE_SIZE;
  memset(packet->payload, 0, packet->payload_len);
  memcpy(packet->payload, node.self_id.pub_key, PUB_KEY_SIZE);
  packet->payload[packet->payload_len - 1] = marker;
}

TEST(TraceRetry, TraceAndAnonymousRequestsUseThreeAirtimes) {
  TraceTestClock clock;
  TraceTestRTC rtc;
  TraceTestRNG rng;
  TraceTestRadio radio;
  TraceTestTables tables;
  StaticPoolPacketManager manager(12);
  TraceTestMesh node(radio, clock, rng, rtc, manager, tables);

  mesh::Packet trace;
  trace.header = ROUTE_TYPE_DIRECT | (PAYLOAD_TYPE_TRACE << PH_TYPE_SHIFT);
  mesh::Packet anon;
  anon.header = ROUTE_TYPE_DIRECT | (PAYLOAD_TYPE_ANON_REQ << PH_TYPE_SHIFT);
  mesh::Packet text;
  text.header = ROUTE_TYPE_DIRECT | (PAYLOAD_TYPE_TXT_MSG << PH_TYPE_SHIFT);
  mesh::Packet other;
  other.header = ROUTE_TYPE_DIRECT | (PAYLOAD_TYPE_REQ << PH_TYPE_SHIFT);

  EXPECT_EQ(3, node.airtimeFactor(&trace));
  EXPECT_EQ(3, node.airtimeFactor(&anon));
  EXPECT_EQ(7, node.airtimeFactor(&text));
  EXPECT_EQ(6, node.airtimeFactor(&other));
}

TEST(FloodRetry, GroupDataUsesTheStricterPathGate) {
  TraceTestClock clock;
  TraceTestRTC rtc;
  TraceTestRNG rng;
  TraceTestRadio radio;
  TraceTestTables tables;
  StaticPoolPacketManager manager(12);
  TraceTestMesh node(radio, clock, rng, rtc, manager, tables);

  mesh::Packet group_data;
  group_data.header = ROUTE_TYPE_FLOOD | (PAYLOAD_TYPE_GRP_DATA << PH_TYPE_SHIFT);
  mesh::Packet group_text;
  group_text.header = ROUTE_TYPE_FLOOD | (PAYLOAD_TYPE_GRP_TXT << PH_TYPE_SHIFT);

  EXPECT_EQ(1, node.floodPathGate(&group_data, 2, 1));
  EXPECT_EQ(1, node.floodPathGate(&group_data, FLOOD_RETRY_PATH_GATE_DISABLED, 1));
  EXPECT_EQ(1, node.floodPathGate(&group_data, 1, 3));
  EXPECT_EQ(2, node.floodPathGate(&group_data, 2, FLOOD_RETRY_PATH_GATE_DISABLED));
  EXPECT_EQ(0, node.floodPathGate(&group_data, 0, FLOOD_RETRY_PATH_GATE_DISABLED));
  EXPECT_EQ(2, node.floodPathGate(&group_text, 2, 1));
}

TEST(FloodRetry, PayloadAndPathPolicyCapsEveryFloodType) {
  TraceTestClock clock;
  TraceTestRTC rtc;
  TraceTestRNG rng;
  TraceTestRadio radio;
  TraceTestTables tables;
  StaticPoolPacketManager manager(12);
  TraceTestMesh node(radio, clock, rng, rtc, manager, tables);

  for (uint8_t type = 0; type <= PH_TYPE_MASK; type++) {
    SCOPED_TRACE(static_cast<int>(type));
    mesh::Packet packet;
    packet.header = ROUTE_TYPE_FLOOD | (type << PH_TYPE_SHIFT);
    packet.setPathHashSizeAndCount(1, 0);

    uint8_t origin_limit;
    if (type == PAYLOAD_TYPE_REQ || type == PAYLOAD_TYPE_OTA) {
      origin_limit = 0;
    } else if (type == PAYLOAD_TYPE_GRP_TXT || type == PAYLOAD_TYPE_RESPONSE
               || type == PAYLOAD_TYPE_TXT_MSG || type == PAYLOAD_TYPE_ANON_REQ
               || type == PAYLOAD_TYPE_PATH) {
      origin_limit = 15;
    } else {
      origin_limit = 1;
    }
    EXPECT_EQ(origin_limit, node.floodAttemptLimit(&packet, 15));
    EXPECT_EQ(0, node.floodAttemptLimit(&packet, 0));

    packet.setPathHashSizeAndCount(1, 1);
    uint8_t transit_limit;
    if (type == PAYLOAD_TYPE_REQ || type == PAYLOAD_TYPE_OTA) {
      transit_limit = 0;
    } else if (type == PAYLOAD_TYPE_GRP_TXT) {
      transit_limit = 15;
    } else if (type == PAYLOAD_TYPE_RESPONSE || type == PAYLOAD_TYPE_TXT_MSG
               || type == PAYLOAD_TYPE_ANON_REQ || type == PAYLOAD_TYPE_PATH) {
      transit_limit = 2;
    } else {
      transit_limit = 1;
    }
    EXPECT_EQ(transit_limit, node.floodAttemptLimit(&packet, 15));
  }
}

TEST(FloodRetry, PayloadPolicyOnlyCapsAndNeverRaisesRoleCount) {
  TraceTestClock clock;
  TraceTestRTC rtc;
  TraceTestRNG rng;
  TraceTestRadio radio;
  TraceTestTables tables;
  StaticPoolPacketManager manager(12);
  TraceTestMesh node(radio, clock, rng, rtc, manager, tables);

  mesh::Packet login_response;
  login_response.header = ROUTE_TYPE_FLOOD | (PAYLOAD_TYPE_RESPONSE << PH_TYPE_SHIFT);
  login_response.setPathHashSizeAndCount(1, 0);
  EXPECT_EQ(7, node.floodAttemptLimit(&login_response, 7));
  EXPECT_EQ(15, node.floodAttemptLimit(&login_response, 255));

  login_response.setPathHashSizeAndCount(1, 3);
  EXPECT_EQ(1, node.floodAttemptLimit(&login_response, 1));
  EXPECT_EQ(2, node.floodAttemptLimit(&login_response, 7));

  mesh::Packet group_text;
  group_text.header = ROUTE_TYPE_FLOOD | (PAYLOAD_TYPE_GRP_TXT << PH_TYPE_SHIFT);
  group_text.setPathHashSizeAndCount(1, 3);
  EXPECT_EQ(7, node.floodAttemptLimit(&group_text, 7));
  EXPECT_EQ(15, node.floodAttemptLimit(&group_text, 255));
}

TEST(FloodRetry, OriginAdvertRetryHasAnExtraOneMinuteDelay) {
  TraceTestClock clock;
  TraceTestRTC rtc;
  TraceTestRNG rng;
  TraceTestRadio radio;
  TraceTestTables tables;
  StaticPoolPacketManager manager(12);
  TraceTestMesh node(radio, clock, rng, rtc, manager, tables);

  mesh::Packet origin_advert;
  initSelfAdvert(node, &origin_advert, 0x10);
  origin_advert.header |= ROUTE_TYPE_FLOOD;
  origin_advert.setPathHashSizeAndCount(1, 0);
  mesh::Packet forwarded_advert = origin_advert;
  forwarded_advert.setPathHashSizeAndCount(1, 1);
  mesh::Packet foreign_origin_advert = origin_advert;
  foreign_origin_advert.payload[0] ^= 0xFF;
  mesh::Packet origin_group_text;
  origin_group_text.header = ROUTE_TYPE_FLOOD | (PAYLOAD_TYPE_GRP_TXT << PH_TYPE_SHIFT);
  origin_group_text.setPathHashSizeAndCount(1, 0);

  uint32_t ordinary_delay = node.floodAttemptDelay(&origin_group_text);
  EXPECT_EQ(ordinary_delay, node.floodAttemptDelay(&forwarded_advert));
  EXPECT_EQ(ordinary_delay, node.floodAttemptDelay(&foreign_origin_advert));
  EXPECT_EQ(ordinary_delay + 60000UL, node.floodAttemptDelay(&origin_advert));
}

TEST(FloodRetry, NewSelfAdvertReplacesTheOlderQueuedRetry) {
  TraceTestClock clock;
  TraceTestRTC rtc;
  TraceTestRNG rng;
  TraceTestRadio radio;
  TraceTestTables tables;
  StaticPoolPacketManager manager(12);
  TraceTestMesh node(radio, clock, rng, rtc, manager, tables);
  node.begin();

  mesh::Packet* old_advert = manager.allocNew();
  ASSERT_NE(old_advert, nullptr);
  initSelfAdvert(node, old_advert, 0x11);
  ASSERT_TRUE(node.sendFlood(old_advert));
  ASSERT_EQ(1, manager.getOutboundTotal());

  clock.now = 1;
  node.loop();
  ASSERT_TRUE(radio.sending);
  radio.complete = true;
  clock.now = 2;
  node.loop();
  ASSERT_EQ(1, manager.getOutboundTotal());
  mesh::Packet* old_retry = manager.getOutboundByIdx(0);
  ASSERT_NE(old_retry, nullptr);
  EXPECT_NE(old_advert, old_retry);

  mesh::Packet* group_data = manager.allocNew();
  ASSERT_NE(group_data, nullptr);
  group_data->header = PAYLOAD_TYPE_GRP_DATA << PH_TYPE_SHIFT;
  group_data->payload_len = 1;
  group_data->payload[0] = 0x33;
  ASSERT_TRUE(node.sendFlood(group_data));
  clock.now = 3;
  node.loop();
  ASSERT_TRUE(radio.sending);
  radio.complete = true;
  clock.now = 4;
  node.loop();
  ASSERT_EQ(2, manager.getOutboundTotal());
  mesh::Packet* group_retry = NULL;
  for (int i = 0; i < manager.getOutboundTotal(); i++) {
    mesh::Packet* queued = manager.getOutboundByIdx(i);
    if (queued != old_retry) group_retry = queued;
  }
  ASSERT_NE(group_retry, nullptr);

  mesh::Packet* new_advert = manager.allocNew();
  ASSERT_NE(new_advert, nullptr);
  initSelfAdvert(node, new_advert, 0x22);
  ASSERT_TRUE(node.sendFlood(new_advert));

  ASSERT_EQ(2, manager.getOutboundTotal());
  bool found_new_advert = false;
  bool found_group_retry = false;
  for (int i = 0; i < manager.getOutboundTotal(); i++) {
    mesh::Packet* queued = manager.getOutboundByIdx(i);
    found_new_advert |= queued == new_advert;
    found_group_retry |= queued == group_retry;
    EXPECT_NE(old_retry, queued);
  }
  EXPECT_TRUE(found_new_advert);
  EXPECT_TRUE(found_group_retry);
}

TEST(FloodRetry, DisabledRetryIsRecheckedAfterInitialTxAndBeforeDelayedTx) {
  {
    TraceTestClock clock;
    TraceTestRTC rtc;
    TraceTestRNG rng;
    TraceTestRadio radio;
    TraceTestTables tables;
    StaticPoolPacketManager manager(12);
    TraceTestMesh node(radio, clock, rng, rtc, manager, tables);
    node.begin();

    mesh::Packet* packet = manager.allocNew();
    ASSERT_NE(packet, nullptr);
    packet->header = PAYLOAD_TYPE_GRP_DATA << PH_TYPE_SHIFT;
    packet->payload_len = 1;
    packet->payload[0] = 0x44;
    ASSERT_TRUE(node.sendFlood(packet));
    node.floodRetriesAllowed = false;

    clock.now = 1;
    node.loop();
    ASSERT_TRUE(radio.sending);
    radio.complete = true;
    clock.now = 2;
    node.loop();
    EXPECT_EQ(0, manager.getOutboundTotal());
  }

  {
    TraceTestClock clock;
    TraceTestRTC rtc;
    TraceTestRNG rng;
    TraceTestRadio radio;
    TraceTestTables tables;
    StaticPoolPacketManager manager(12);
    TraceTestMesh node(radio, clock, rng, rtc, manager, tables);
    node.begin();

    mesh::Packet* packet = manager.allocNew();
    ASSERT_NE(packet, nullptr);
    packet->header = PAYLOAD_TYPE_GRP_DATA << PH_TYPE_SHIFT;
    packet->payload_len = 1;
    packet->payload[0] = 0x55;
    ASSERT_TRUE(node.sendFlood(packet));
    clock.now = 1;
    node.loop();
    ASSERT_TRUE(radio.sending);
    radio.complete = true;
    clock.now = 2;
    node.loop();
    ASSERT_EQ(1, manager.getOutboundTotal());

    node.floodRetriesAllowed = false;
    clock.now = 1000;
    node.loop();
    EXPECT_FALSE(radio.sending);
    EXPECT_EQ(0, manager.getOutboundTotal());
  }
}

TEST(FloodRetry, RecentForwardedAdvertWithHeardEchoIsNotForwardedAgain) {
  TraceTestClock clock;
  TraceTestRTC rtc;
  TraceTestRNG rng;
  TraceTestRadio radio;
  TraceTestTables tables;
  StaticPoolPacketManager manager(12);
  TraceTestMesh node(radio, clock, rng, rtc, manager, tables);
  node.begin();
  node.forwardFloods = true;
  rtc.now = 100000;
  clock.now = 1000;

  mesh::Packet forwarded;
  forwarded.header = ROUTE_TYPE_FLOOD | (PAYLOAD_TYPE_ADVERT << PH_TYPE_SHIFT);
  forwarded.setPathHashSizeAndCount(1, 1);
  forwarded.path[0] = 0x42;
  forwarded.payload_len = PUB_KEY_SIZE + sizeof(uint32_t) + SIGNATURE_SIZE;
  memset(forwarded.payload, 0x5A, forwarded.payload_len);
  uint32_t emitted_timestamp = rtc.now - 60;
  memcpy(&forwarded.payload[PUB_KEY_SIZE], &emitted_timestamp, sizeof(emitted_timestamp));
  node.completePacketSend(&forwarded);

  mesh::Packet echo = forwarded;
  echo.setPathHashSizeAndCount(1, 2);
  node.receivePacket(&echo);

  mesh::Packet repeated = forwarded;
  EXPECT_EQ(ACTION_RELEASE, node.routePacket(&repeated));

  rtc.now = emitted_timestamp + (6UL * 60UL * 60UL);
  repeated = forwarded;
  EXPECT_NE(ACTION_RELEASE, node.routePacket(&repeated));
}

TEST(FloodRetry, ForwardedAdvertEchoMayReturnThroughAnotherBranch) {
  TraceTestClock clock;
  TraceTestRTC rtc;
  TraceTestRNG rng;
  TraceTestRadio radio;
  TraceTestTables tables;
  StaticPoolPacketManager manager(12);
  TraceTestMesh node(radio, clock, rng, rtc, manager, tables);
  node.begin();
  node.forwardFloods = true;
  rtc.now = 100000;

  mesh::Packet forwarded;
  forwarded.header = ROUTE_TYPE_FLOOD | (PAYLOAD_TYPE_ADVERT << PH_TYPE_SHIFT);
  forwarded.setPathHashSizeAndCount(1, 1);
  forwarded.path[0] = 0x24;
  forwarded.payload_len = PUB_KEY_SIZE + sizeof(uint32_t) + SIGNATURE_SIZE;
  memset(forwarded.payload, 0xA5, forwarded.payload_len);
  uint32_t emitted_timestamp = rtc.now - 60;
  memcpy(&forwarded.payload[PUB_KEY_SIZE], &emitted_timestamp, sizeof(emitted_timestamp));
  node.completePacketSend(&forwarded);

  mesh::Packet other_branch = forwarded;
  other_branch.setPathHashSizeAndCount(1, 2);
  other_branch.path[0] ^= 0xFF;
  node.receivePacket(&other_branch);

  mesh::Packet repeated = forwarded;
  EXPECT_EQ(ACTION_RELEASE, node.routePacket(&repeated));
}

TEST(TraceRetry, NewTraceReplacesQueuedRetryButAdvancedOldTraceStillQueues) {
  TraceTestClock clock;
  TraceTestRTC rtc;
  TraceTestRNG rng;
  TraceTestRadio radio;
  TraceTestTables tables;
  StaticPoolPacketManager manager(12);
  TraceTestMesh node(radio, clock, rng, rtc, manager, tables);
  node.begin();

  const uint8_t route[] = {0x11, 0x22, 0x33};
  mesh::Packet* old_trace = makeTrace(node, 0x11111111, 0xAAAAAAAA, route, sizeof(route));
  ASSERT_NE(old_trace, nullptr);
  ASSERT_EQ(1, manager.getOutboundTotal());

  clock.now = 1;
  node.loop();
  ASSERT_TRUE(radio.sending);
  radio.complete = true;
  clock.now = 2;
  node.loop();
  ASSERT_EQ(1, manager.getOutboundTotal());  // old TRACE retry

  mesh::Packet* new_trace = makeTrace(node, 0x22222222, 0xBBBBBBBB, route, sizeof(route));
  ASSERT_NE(new_trace, nullptr);
  ASSERT_EQ(1, manager.getOutboundTotal());
  EXPECT_EQ(new_trace, manager.getOutboundByIdx(0));

  // A packet from the older run that has already advanced is a different
  // retry stage. It must remain queueable instead of being treated as the
  // stale same-hop retry that the newer run replaced.
  mesh::Packet* returning_old = node.createTrace(0x11111111, 0xAAAAAAAA, 0);
  ASSERT_NE(returning_old, nullptr);
  memcpy(&returning_old->payload[returning_old->payload_len], route, sizeof(route));
  returning_old->payload_len += sizeof(route);
  returning_old->header |= ROUTE_TYPE_DIRECT;
  returning_old->path_len = 1;
  returning_old->path[0] = 4;
  ASSERT_TRUE(node.sendPacket(returning_old, 1));
  EXPECT_EQ(2, manager.getOutboundTotal());
}

TEST(MessageRetry, DifferentTimestampReplacesQueuedDirectRetry) {
  TraceTestClock clock;
  TraceTestRTC rtc;
  TraceTestRNG rng;
  TraceTestRadio radio;
  TraceTestTables tables;
  StaticPoolPacketManager manager(12);
  TraceTestMesh node(radio, clock, rng, rtc, manager, tables);
  node.begin();

  const uint8_t route[] = {0x11, 0x22};
  const uint8_t message_key[MAX_HASH_SIZE] = {
    0x10, 0x20, 0x30, 0x40, 0x50, 0x60, 0x70, 0x80
  };
  mesh::Packet* old_message = makeDirectText(node, 0x01, route, sizeof(route));
  ASSERT_NE(old_message, nullptr);
  node.trackMessageRetry(old_message, message_key, 100U);
  finishCurrentSend(node, clock, radio);
  ASSERT_EQ(1, manager.getOutboundTotal());
  mesh::Packet* old_retry = manager.getOutboundByIdx(0);

  mesh::Packet* new_message = makeDirectText(node, 0x02, route, sizeof(route));
  ASSERT_NE(new_message, nullptr);
  ASSERT_EQ(2, manager.getOutboundTotal());
  node.trackMessageRetry(new_message, message_key, 101U);

  ASSERT_EQ(1, manager.getOutboundTotal());
  EXPECT_EQ(new_message, manager.getOutboundByIdx(0));
  EXPECT_NE(old_retry, manager.getOutboundByIdx(0));

  finishCurrentSend(node, clock, radio);
  ASSERT_EQ(1, manager.getOutboundTotal());
  EXPECT_NE(old_retry, manager.getOutboundByIdx(0));
}

TEST(MessageRetry, SameTimestampKeepsExistingRetrySequence) {
  TraceTestClock clock;
  TraceTestRTC rtc;
  TraceTestRNG rng;
  TraceTestRadio radio;
  TraceTestTables tables;
  StaticPoolPacketManager manager(12);
  TraceTestMesh node(radio, clock, rng, rtc, manager, tables);
  node.begin();

  const uint8_t route[] = {0x31, 0x32};
  const uint8_t message_key[MAX_HASH_SIZE] = {
    0x81, 0x71, 0x61, 0x51, 0x41, 0x31, 0x21, 0x11
  };
  mesh::Packet* old_message = makeDirectText(node, 0x11, route, sizeof(route));
  ASSERT_NE(old_message, nullptr);
  node.trackMessageRetry(old_message, message_key, 200U);
  finishCurrentSend(node, clock, radio);
  ASSERT_EQ(1, manager.getOutboundTotal());
  mesh::Packet* old_retry = manager.getOutboundByIdx(0);

  mesh::Packet* same_timestamp = makeDirectText(node, 0x12, route, sizeof(route));
  ASSERT_NE(same_timestamp, nullptr);
  node.trackMessageRetry(same_timestamp, message_key, 200U);

  ASSERT_EQ(2, manager.getOutboundTotal());
  bool found_old_retry = false;
  bool found_new_message = false;
  for (int i = 0; i < manager.getOutboundTotal(); i++) {
    found_old_retry |= manager.getOutboundByIdx(i) == old_retry;
    found_new_message |= manager.getOutboundByIdx(i) == same_timestamp;
  }
  EXPECT_TRUE(found_old_retry);
  EXPECT_TRUE(found_new_message);
}

TEST(MessageRetry, ReplacementWorksAcrossFloodAndDirectRoutes) {
  TraceTestClock clock;
  TraceTestRTC rtc;
  TraceTestRNG rng;
  TraceTestRadio radio;
  TraceTestTables tables;
  StaticPoolPacketManager manager(12);
  TraceTestMesh node(radio, clock, rng, rtc, manager, tables);
  node.begin();

  const uint8_t message_key[MAX_HASH_SIZE] = {
    0x08, 0x18, 0x28, 0x38, 0x48, 0x58, 0x68, 0x78
  };
  mesh::Packet* old_flood = makeFloodText(node, 0x21);
  ASSERT_NE(old_flood, nullptr);
  node.trackMessageRetry(old_flood, message_key, 300U);
  finishCurrentSend(node, clock, radio);
  ASSERT_EQ(1, manager.getOutboundTotal());
  mesh::Packet* old_retry = manager.getOutboundByIdx(0);

  const uint8_t route[] = {0x41, 0x42};
  mesh::Packet* new_direct = makeDirectText(node, 0x22, route, sizeof(route));
  ASSERT_NE(new_direct, nullptr);
  ASSERT_EQ(2, manager.getOutboundTotal());
  node.trackMessageRetry(new_direct, message_key, 301U);

  ASSERT_EQ(1, manager.getOutboundTotal());
  EXPECT_EQ(new_direct, manager.getOutboundByIdx(0));
  EXPECT_NE(old_retry, manager.getOutboundByIdx(0));
}

class AdvertLimitedTestMesh : public TraceTestMesh {
public:
  mesh::StaticFloodAdvertLimiter<4> limiter;
  unsigned advert_callbacks = 0;
  unsigned forwarding_checks = 0;
  using TraceTestMesh::TraceTestMesh;
  mesh::FloodAdvertLimiter* getFloodAdvertLimiter() override { return &limiter; }
  bool allowPacketForward(const mesh::Packet* packet) override {
    ++forwarding_checks;
    return TraceTestMesh::allowPacketForward(packet);
  }
  void onAdvertRecv(mesh::Packet*, const mesh::Identity&, uint32_t,
                    const uint8_t*, size_t) override { ++advert_callbacks; }
};

class AdvertReceiveLimit : public ::testing::Test {
protected:
  TraceTestClock clock;
  TraceTestRTC rtc;
  TraceTestRNG rng;
  TraceTestRadio radio;
  ForwardingTestTables tables;
  StaticPoolPacketManager manager{12};
  AdvertLimitedTestMesh node{radio, clock, rng, rtc, manager, tables};

  void SetUp() override {
    node.begin();
    node.forwardFloods = true;
    node.floodRetriesAllowed = false;
    g_mock_ed25519_verify_result = true;
    g_mock_ed25519_verify_calls = 0;
  }
  void TearDown() override { g_mock_ed25519_verify_result = true; }
  mesh::Packet advert(unsigned seq, uint8_t hops = 8, uint8_t hash_size = 1) {
    mesh::Packet packet = makeFloodPacket(PAYLOAD_TYPE_ADVERT);
    packet.payload_len = PUB_KEY_SIZE + 4 + SIGNATURE_SIZE;
    memset(packet.payload, 0, packet.payload_len);
    packet.payload[0] = 0xBA;
    memcpy(packet.payload + PUB_KEY_SIZE, &seq, 4);
    memset(packet.path, 0x45, sizeof(packet.path));
    packet.setPathHashSizeAndCount(hash_size, hops);
    return packet;
  }
  mesh::DispatcherAction receive(unsigned seq, uint8_t hops = 8, bool seen = false) {
    tables.seen = seen;
    auto packet = advert(seq, hops);
    return node.receivePacket(&packet);
  }
};

TEST_F(AdvertReceiveLimit, StopsOnlyForwardingAndKeepsLocalAdvertCallbacks) {
  EXPECT_NE(ACTION_RELEASE, receive(1));
  EXPECT_NE(ACTION_RELEASE, receive(2));
  EXPECT_EQ(ACTION_RELEASE, receive(3));
  EXPECT_EQ(3U, node.advert_callbacks);
  EXPECT_EQ(2U, node.forwarding_checks); // before side-effectful rule counters
  tables.seen = false;
  auto message = makeFloodPacket(PAYLOAD_TYPE_RAW_CUSTOM);
  EXPECT_NE(ACTION_RELEASE, node.receivePacket(&message));
}

TEST_F(AdvertReceiveLimit, CliListsTheActualReceiveLimitWithoutChangingForwarding) {
  char reply[160];
  ASSERT_NE(ACTION_RELEASE, receive(1));
  ASSERT_TRUE(mesh::cli::handleFloodAdvertGet(&node.limiter, "get flood.advert", reply, clock.now));
  EXPECT_STREQ("> no rate-limited adverts", reply);
  ASSERT_NE(ACTION_RELEASE, receive(2));
  ASSERT_TRUE(mesh::cli::handleFloodAdvertGet(&node.limiter, "get flood.advert", reply, clock.now));
  EXPECT_NE(nullptr, strstr(reply, "BA0000000000 quota wait=10800s"));
  ASSERT_EQ(ACTION_RELEASE, receive(3));
  ASSERT_TRUE(mesh::cli::handleFloodAdvertGet(&node.limiter, "get flood.advert key 1", reply, clock.now));
  EXPECT_NE(nullptr, strstr(reply, "sent=2/2 hops=8"));
  EXPECT_EQ(3U, node.advert_callbacks);
  EXPECT_EQ(2U, node.forwarding_checks);
  // An authenticated shorter duplicate changes the actual allowance and list.
  ASSERT_EQ(ACTION_RELEASE, receive(1, 1, true));
  ASSERT_TRUE(mesh::cli::handleFloodAdvertGet(&node.limiter, "get flood.advert", reply, clock.now));
  EXPECT_STREQ("> no rate-limited adverts", reply);
  EXPECT_NE(ACTION_RELEASE, receive(4));
}

TEST_F(AdvertReceiveLimit, VerifiedShorterDuplicateRaisesAllowanceWithoutRelayingIt) {
  receive(1);
  receive(2);
  EXPECT_EQ(ACTION_RELEASE, receive(1, 1, true));
  EXPECT_EQ(3U, g_mock_ed25519_verify_calls);
  EXPECT_EQ(2U, node.advert_callbacks);
  for (unsigned seq = 3; seq <= 10; ++seq) EXPECT_NE(ACTION_RELEASE, receive(seq));
  EXPECT_EQ(ACTION_RELEASE, receive(11));
}

TEST_F(AdvertReceiveLimit, ForgedShorterDuplicateCannotRaiseAllowance) {
  receive(1);
  receive(2);
  g_mock_ed25519_verify_result = false;
  EXPECT_EQ(ACTION_RELEASE, receive(1, 0, true));
  g_mock_ed25519_verify_result = true;
  EXPECT_EQ(ACTION_RELEASE, receive(3));
  EXPECT_EQ(3U, node.advert_callbacks);
}

TEST_F(AdvertReceiveLimit, InvalidSelfAndMalformedAdvertsCannotCreateAbuseHistory) {
  g_mock_ed25519_verify_result = false;
  for (unsigned seq = 0; seq < 20; ++seq) EXPECT_EQ(ACTION_RELEASE, receive(seq));
  g_mock_ed25519_verify_result = true;
  auto self = advert(30);
  memcpy(self.payload, node.self_id.pub_key, PUB_KEY_SIZE);
  tables.seen = false;
  EXPECT_EQ(ACTION_RELEASE, node.receivePacket(&self));
  auto malformed = advert(31);
  malformed.payload_len = PUB_KEY_SIZE;
  EXPECT_EQ(ACTION_RELEASE, node.receivePacket(&malformed));
  EXPECT_NE(ACTION_RELEASE, receive(40));
  EXPECT_NE(ACTION_RELEASE, receive(41));
  EXPECT_EQ(2U, node.advert_callbacks);
}

TEST_F(AdvertReceiveLimit, AdvertDuplicatesStaySuppressedAfterGeneralSeenCacheEviction) {
  receive(1);
  for (unsigned i = 0; i < 300; ++i) EXPECT_EQ(ACTION_RELEASE, receive(1));
  EXPECT_NE(ACTION_RELEASE, receive(2));
  EXPECT_EQ(ACTION_RELEASE, receive(3));
}

TEST_F(AdvertReceiveLimit, UnsignedTrailingDataCannotManufactureDistinctAdverts) {
  for (unsigned seq = 0; seq < 20; ++seq) {
    auto packet = advert(1);
    packet.payload_len += MAX_ADVERT_DATA_SIZE + 1;
    memset(packet.payload + PUB_KEY_SIZE + 4 + SIGNATURE_SIZE, 0, MAX_ADVERT_DATA_SIZE + 1);
    packet.payload[packet.payload_len - 1] = seq;
    tables.seen = false;
    EXPECT_EQ(ACTION_RELEASE, node.receivePacket(&packet));
  }
  EXPECT_EQ(0U, g_mock_ed25519_verify_calls);
  EXPECT_EQ(0U, node.advert_callbacks);
  EXPECT_NE(ACTION_RELEASE, receive(2));
  EXPECT_NE(ACTION_RELEASE, receive(3));
  // The maximum supported signed app data remains valid.
  auto largest = advert(4);
  largest.payload_len += MAX_ADVERT_DATA_SIZE;
  memset(largest.payload + PUB_KEY_SIZE + 4 + SIGNATURE_SIZE, 0, MAX_ADVERT_DATA_SIZE);
  tables.seen = false;
  EXPECT_EQ(ACTION_RELEASE, node.receivePacket(&largest)); // quota, not parser rejection
  EXPECT_EQ(3U, node.advert_callbacks);
}

TEST_F(AdvertReceiveLimit, UsesHopCountNotPathBytesAndIgnoresRtcJumps) {
  for (uint8_t size = 1; size <= 3; ++size) {
    node.limiter.reset();
    for (unsigned seq = 1; seq <= 3; ++seq) {
      auto packet = advert(seq, 8, size);
      tables.seen = false;
      rtc.now = seq == 2 ? UINT32_MAX : 1;
      auto action = node.receivePacket(&packet);
      if (seq <= 2) EXPECT_NE(ACTION_RELEASE, action);
      else EXPECT_EQ(ACTION_RELEASE, action);
    }
  }
}

TEST_F(AdvertReceiveLimit, DirectAdvertsAreNotFloodQuotaOrAbuseEvidence) {
  for (unsigned seq = 0; seq < 20; ++seq) {
    auto packet = advert(seq, 0);
    packet.header = ROUTE_TYPE_DIRECT | (PAYLOAD_TYPE_ADVERT << PH_TYPE_SHIFT);
    tables.seen = false;
    EXPECT_EQ(ACTION_RELEASE, node.receivePacket(&packet));
  }
  EXPECT_NE(ACTION_RELEASE, receive(30));
  EXPECT_NE(ACTION_RELEASE, receive(31));
}

TEST_F(AdvertReceiveLimit, SuppressedTrafficStillEscalatesAndRemainsLocallyVisible) {
  for (unsigned seq = 1; seq <= 3; ++seq) receive(seq);
  clock.now = mesh::FloodAdvertLimiter::WINDOW_MS;
  for (unsigned seq = 4; seq <= 6; ++seq) receive(seq);
  auto packet = advert(7);
  ASSERT_TRUE(node.limiter.isBad(packet.payload, clock.now));
  EXPECT_EQ(ACTION_RELEASE, receive(7));
  EXPECT_EQ(7U, node.advert_callbacks);
  node.begin(); // reboot clears both abuse and ordinary quota history
  EXPECT_FALSE(node.limiter.isBad(packet.payload, clock.now));
  EXPECT_NE(ACTION_RELEASE, receive(8));
  EXPECT_NE(ACTION_RELEASE, receive(9));
}

TEST_F(AdvertReceiveLimit, SeenVerifiedDuplicateRefreshesLastHeardWithoutSignatureWork) {
  for (unsigned source = 0; source < 4; ++source) {
    auto packet = advert(1);
    packet.payload[0] += source;
    tables.seen = false;
    clock.now = source;
    ASSERT_NE(ACTION_RELEASE, node.receivePacket(&packet));
  }
  clock.now = 10;
  ASSERT_EQ(ACTION_RELEASE, receive(1, 8, true));
  EXPECT_EQ(4U, g_mock_ed25519_verify_calls);
  auto new_source = advert(1);
  new_source.payload[0] += 4;
  tables.seen = false;
  clock.now = 11;
  EXPECT_NE(ACTION_RELEASE, node.receivePacket(&new_source));
  auto oldest = advert(1);
  oldest.payload[0] += 1;
  uint8_t hash[MAX_HASH_SIZE];
  oldest.calculatePacketHash(hash);
  EXPECT_EQ(mesh::FloodAdvertLimiter::Decision::Capacity,
            node.limiter.check(oldest.payload, hash, clock.now));
  auto refreshed = advert(1);
  refreshed.calculatePacketHash(hash);
  EXPECT_EQ(mesh::FloodAdvertLimiter::Decision::Duplicate,
            node.limiter.check(refreshed.payload, hash, clock.now));
}

int main(int argc, char** argv) {
  ::testing::InitGoogleTest(&argc, argv);
  return RUN_ALL_TESTS();
}
