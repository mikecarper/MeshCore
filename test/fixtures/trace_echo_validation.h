// Exercise the real Mesh/Dispatcher with decoded TRACE frames and retry buffers.
class TraceEchoValidationMesh : public DualProfileTestMesh {
 public:
  using DualProfileTestMesh::DualProfileTestMesh;
  unsigned trace_callbacks = 0;
  void onTraceRecv(mesh::Packet*, uint32_t, uint32_t, uint8_t,
                   const uint8_t*, const uint8_t*, uint8_t) override {
    ++trace_callbacks;
  }
  bool waitingForEcho() const {
    uint32_t delay;
    return getNextRetryWakeDelay(delay);
  }
  bool passiveGate(const mesh::Packet* packet) const { return usePassiveChannelCheck(packet); }
};

class TraceEchoValidation : public testing::Test {
 protected:
  TraceTestClock clock;
  TraceTestRTC rtc;
  TraceTestRNG rng;
  DualProfileTestRadio radio;
  TraceTestTables tables;
  StaticPoolPacketManager manager{12};
  TraceEchoValidationMesh node{radio, clock, rng, rtc, manager, tables};
  mesh::Packet original;
  mesh::Packet echo;

  void SetUp() override {
    clock.now = 100;
    node.begin();
    node.forwardFloods = true;
    radio.config.cross = mesh::RadioCrossMode::Off;
    memset(node.self_id.pub_key, 0x42, PUB_KEY_SIZE);
  }

  void startForward(uint8_t hash_size = 1, uint8_t flags = 0, uint8_t profile = 0) {
    original = mesh::Packet();
    original.header = ROUTE_TYPE_DIRECT | (PAYLOAD_TYPE_TRACE << PH_TYPE_SHIFT);
    original.payload_len = 9 + 3 * hash_size;
    memset(original.payload, 0, sizeof(original.payload));
    memset(original.path, 0, sizeof(original.path));
    original.payload[0] = 0x15;
    original.payload[4] = 0x29;
    original.payload[8] = flags;
    memset(original.payload + 9, 0x42, hash_size);
    memset(original.payload + 9 + hash_size, 0x77, hash_size);
    memset(original.payload + 9 + 2 * hash_size, 0x99, hash_size);
    original._snr = 20;
    original.radio_profile = original.radio_origin = profile;
    original.radio_generation = original.radio_origin_generation = radio.config.generation[profile];
    original.radio_bound = true;
    original.radio_local = false;
    ASSERT_NE(ACTION_RELEASE, node.receivePacket(&original));
    ASSERT_EQ(1, original.path_len);
    echo = original;
    echo.path_len = 2;
    echo.path[1] = 8;
    echo._snr = 12;
  }

  void queueRetry() {
    node.completePacketSend(&original);
    ASSERT_EQ(1, manager.getOutboundTotal());
  }

  mesh::DispatcherAction receiveWire(const mesh::Packet& packet) {
    uint8_t wire[MAX_TRANS_UNIT];
    mesh::Packet decoded;
    const bool valid = decoded.readFrom(wire, packet.writeTo(wire));
    EXPECT_TRUE(valid);
    if (!valid) return ACTION_RELEASE;
    // Dispatcher normally supplies the receive context after decoding the wire.
    decoded.radio_profile = packet.radio_profile;
    decoded.radio_generation = packet.radio_generation;
    decoded._snr = packet._snr;
    return node.receivePacket(&decoded);
  }
};

TEST_F(TraceEchoValidation, RejectsImpossibleProgressBeforeCancellationOrCallbacks) {
  startForward();
  queueRetry();
  for (uint16_t count : {uint16_t(4), uint16_t(0x41)}) {
    mesh::Packet malformed = echo;
    malformed.path_len = count;
    EXPECT_EQ(ACTION_RELEASE, receiveWire(malformed));
    EXPECT_EQ(1, manager.getOutboundTotal());
    EXPECT_EQ(0U, node.direct_successes);
    EXPECT_EQ(0U, node.trace_callbacks);
  }
  EXPECT_EQ(ACTION_RELEASE, receiveWire(echo));
  EXPECT_EQ(0, manager.getOutboundTotal());
  EXPECT_EQ(1U, node.direct_successes);
}

TEST_F(TraceEchoValidation, RejectsIncompleteAndOversizedLayoutsBeforeObservers) {
  startForward();
  queueRetry();
  for (uint16_t length = 0; length < 9; ++length) {
    mesh::Packet malformed = echo;
    malformed.payload_len = length;
    EXPECT_EQ(ACTION_RELEASE, receiveWire(malformed));
  }
  mesh::Packet partial = echo;
  partial.payload[8] = 1; // three route bytes cannot contain whole two-byte hashes
  EXPECT_EQ(ACTION_RELEASE, receiveWire(partial));
  mesh::Packet oversized = echo;
  oversized.payload_len = sizeof(oversized.payload) + 1;
  EXPECT_EQ(ACTION_RELEASE, node.receivePacket(&oversized));
  EXPECT_EQ(1, manager.getOutboundTotal());
  EXPECT_EQ(0U, node.direct_successes);
  EXPECT_EQ(0U, node.trace_callbacks);
}

TEST_F(TraceEchoValidation, WrongHistoryKeepsRetryButWellFormedTraceStillReachesDestination) {
  startForward();
  queueRetry();
  mesh::Packet different = echo;
  different.path[0] ^= 0x40;
  different.path_len = 3;
  different.path[2] = 16;
  EXPECT_EQ(ACTION_RELEASE, receiveWire(different));
  EXPECT_EQ(1, manager.getOutboundTotal());
  EXPECT_EQ(0U, node.direct_successes);
  EXPECT_EQ(1U, node.trace_callbacks);
  echo.path_len = 3;
  echo.path[2] = 16;
  EXPECT_EQ(ACTION_RELEASE, receiveWire(echo));
  EXPECT_EQ(0, manager.getOutboundTotal());
  EXPECT_EQ(1U, node.direct_successes);
  EXPECT_EQ(2U, node.trace_callbacks);
}

TEST_F(TraceEchoValidation, PayloadProfileAndActualForwardProgressMustMatch) {
  startForward();
  queueRetry();
  mesh::Packet wrong = echo;
  wrong.payload[0] ^= 1;
  receiveWire(wrong);
  wrong = echo;
  wrong.radio_profile = 1;
  wrong.radio_generation = radio.config.generation[1];
  receiveWire(wrong);
  wrong = original;
  receiveWire(wrong);
  EXPECT_EQ(1, manager.getOutboundTotal());
  EXPECT_EQ(0U, node.direct_successes);
  receiveWire(echo);
  EXPECT_EQ(0, manager.getOutboundTotal());
  EXPECT_EQ(1U, node.direct_successes);
}

TEST_F(TraceEchoValidation, PrefixIsRecordedBeforeTheInitialTransmissionCompletes) {
  startForward();
  mesh::Packet different = echo;
  different.path[0] ^= 1;
  receiveWire(different);
  EXPECT_EQ(0U, node.direct_successes);
  queueRetry();
  receiveWire(echo);
  EXPECT_EQ(0, manager.getOutboundTotal());
  EXPECT_EQ(1U, node.direct_successes);
}

TEST_F(TraceEchoValidation, InFlightRetryFinishesWithoutAnotherCopyAfterAValidEcho) {
  startForward();
  queueRetry();
  clock.now = 10000;
  node.loop();
  ASSERT_TRUE(radio.sending);
  mesh::Packet different = echo;
  different.path[0] ^= 1;
  receiveWire(different);
  EXPECT_EQ(0U, node.direct_successes);
  receiveWire(echo);
  EXPECT_EQ(1U, node.direct_successes);
  EXPECT_TRUE(radio.sending);
  radio.complete = true;
  ++clock.now;
  node.loop();
  EXPECT_FALSE(radio.sending);
  EXPECT_EQ(0, manager.getOutboundTotal());
}

TEST_F(TraceEchoValidation, FinalEchoWaitValidatesPrefixWithoutRetainingAPacket) {
  node.direct_attempts = 1;
  startForward();
  queueRetry();
  clock.now = 10000;
  node.loop();
  ASSERT_TRUE(radio.sending);
  radio.complete = true;
  ++clock.now;
  node.loop();
  ASSERT_FALSE(radio.sending);
  ASSERT_EQ(0, manager.getOutboundTotal());
  ASSERT_TRUE(node.waitingForEcho());
  EXPECT_EQ(12, manager.getFreeCount());
  mesh::Packet different = echo;
  different.path[0] ^= 1;
  receiveWire(different);
  EXPECT_EQ(0U, node.direct_successes);
  EXPECT_TRUE(node.waitingForEcho());
  mesh::Packet malformed = echo;
  malformed.path_len = 4;
  receiveWire(malformed);
  EXPECT_TRUE(node.waitingForEcho());
  receiveWire(echo);
  EXPECT_EQ(1U, node.direct_successes);
  EXPECT_FALSE(node.waitingForEcho());
  EXPECT_EQ(12, manager.getFreeCount());
}

TEST_F(TraceEchoValidation, ValidEchoCanRevisitThisNodeAndStillBeForwarded) {
  startForward();
  // The route revisits this node after one other relay.
  original.payload[11] = echo.payload[11] = 0x42;
  // Re-register the changed route before it is sent.
  node.begin();
  original.path_len = 0;
  ASSERT_NE(ACTION_RELEASE, node.receivePacket(&original));
  echo.path[0] = original.path[0];
  queueRetry();
  EXPECT_NE(ACTION_RELEASE, receiveWire(echo));
  EXPECT_EQ(1U, node.direct_successes);
  EXPECT_EQ(0, manager.getOutboundTotal());
}

TEST_F(TraceEchoValidation, TerminalHopUsesOneNormalSendWithoutARetry) {
  original.header = ROUTE_TYPE_DIRECT | (PAYLOAD_TYPE_TRACE << PH_TYPE_SHIFT);
  original.payload_len = 10;
  memset(original.payload, 0, original.payload_len);
  original.payload[9] = 0x42;
  original._snr = 20;
  ASSERT_NE(ACTION_RELEASE, node.receivePacket(&original));
  EXPECT_FALSE(node.passiveGate(&original));
  node.completePacketSend(&original);
  EXPECT_EQ(0, manager.getOutboundTotal());
}

TEST_F(TraceEchoValidation, LegacyAndLinearHashWidthsKeepTheirExistingInterpretation) {
  for (const auto& spec : {std::pair<uint8_t,uint8_t>{1,0}, {2,1}, {3,2}, {4,2}, {4,3}, {8,3}}) {
    SCOPED_TRACE(testing::Message() << "width=" << unsigned(spec.first) << " flags=" << unsigned(spec.second));
    startForward(spec.first, spec.second);
    queueRetry();
    receiveWire(echo);
    EXPECT_EQ(0, manager.getOutboundTotal());
  }
  EXPECT_EQ(6U, node.direct_successes);
}
