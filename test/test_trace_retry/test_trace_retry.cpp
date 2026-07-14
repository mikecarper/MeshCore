#include <gtest/gtest.h>

#include <Ed25519.h>
#include <Mesh.h>
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

class TraceTestMesh : public mesh::Mesh {
public:
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
};

static mesh::Packet* makeTrace(TraceTestMesh& node, uint32_t tag, uint32_t auth,
                               const uint8_t* route, uint8_t route_len) {
  mesh::Packet* packet = node.createTrace(tag, auth, 0);
  EXPECT_NE(packet, nullptr);
  if (packet == nullptr) return nullptr;
  EXPECT_TRUE(node.sendDirect(packet, route, route_len));
  return packet;
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

int main(int argc, char** argv) {
  ::testing::InitGoogleTest(&argc, argv);
  return RUN_ALL_TESTS();
}
