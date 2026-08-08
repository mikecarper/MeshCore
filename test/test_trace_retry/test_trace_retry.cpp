#include <gtest/gtest.h>

#include <Ed25519.h>
#include <Mesh.h>
#include <helpers/ClockSyncUtils.h>
#include <helpers/StaticPoolPacketManager.h>

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
  void random(uint8_t* dest, size_t sz) override { memset(dest, 0, sz); }
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

TEST(RepeaterTransport, OtaFloodRelaysWithoutOtaManagerOnlyDuringTempRadio) {
  TraceTestClock clock;
  TraceTestRTC rtc;
  TraceTestRNG rng;
  TraceTestRadio radio;
  ForwardingTestTables tables;
  StaticPoolPacketManager manager(12);
  TraceTestMesh node(radio, clock, rng, rtc, manager, tables);
  node.forwardFloods = true;

  mesh::Packet packet = makeFloodPacket(PAYLOAD_TYPE_OTA);
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
    if (type == PAYLOAD_TYPE_REQ) {
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
    if (type == PAYLOAD_TYPE_REQ) {
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

int main(int argc, char** argv) {
  ::testing::InitGoogleTest(&argc, argv);
  return RUN_ALL_TESTS();
}
