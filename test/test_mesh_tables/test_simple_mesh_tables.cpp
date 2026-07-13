#include <gtest/gtest.h>
#define MESH_ENABLE_RECENT_REPEATERS 1
#define MAX_RECENT_REPEATERS 8
#include "helpers/SimpleMeshTables.h"

using namespace mesh;

// Build a packet that calculatePacketHash() distinguishes by payload content.
// header selects ROUTE_TYPE_FLOOD so isRouteDirect() returns false.
static Packet makeFloodPacket(uint8_t seed) {
    Packet p;
    p.header = ROUTE_TYPE_FLOOD | (PAYLOAD_TYPE_RAW_CUSTOM << PH_TYPE_SHIFT);
    p.payload[0] = seed;
    p.payload_len = 1;
    p.path_len = 0;
    return p;
}

static Packet makeDirectPacket(uint8_t seed) {
    Packet p;
    p.header = ROUTE_TYPE_DIRECT | (PAYLOAD_TYPE_RAW_CUSTOM << PH_TYPE_SHIFT);
    p.payload[0] = seed;
    p.payload_len = 1;
    p.path_len = 0;
    return p;
}

static Packet makeAckPacket(uint32_t crc, bool direct = true) {
    Packet p;
    p.header = (direct ? ROUTE_TYPE_DIRECT : ROUTE_TYPE_FLOOD)
        | (PAYLOAD_TYPE_ACK << PH_TYPE_SHIFT);
    memcpy(p.payload, &crc, sizeof(crc));
    p.payload_len = sizeof(crc);
    p.path_len = 0;
    return p;
}

// ── wasSeen: pure query ───────────────────────────────────────────────────────

TEST(SimpleMeshTables, WasSeen_ReturnsFalseForUnseen) {
    SimpleMeshTables t;
    Packet p = makeFloodPacket(0x01);
    EXPECT_FALSE(t.wasSeen(&p));
}

// wasSeen shouldn't change state
TEST(SimpleMeshTables, WasSeen_IsPureQuery_DoesNotInsert) {
    SimpleMeshTables t;
    Packet p = makeFloodPacket(0x01);
    EXPECT_FALSE(t.wasSeen(&p));
    EXPECT_FALSE(t.wasSeen(&p));
}

// ── markSeen + wasSeen ───────────────────────────────────────────────────────

TEST(SimpleMeshTables, MarkSeen_MakesWasSeenReturnTrue) {
    SimpleMeshTables t;
    Packet p = makeFloodPacket(0x01);
    t.markSeen(&p);
    EXPECT_TRUE(t.wasSeen(&p));
}

TEST(SimpleMeshTables, MarkSeen_DoesNotAffectOtherPackets) {
    SimpleMeshTables t;
    Packet p1 = makeFloodPacket(0x01);
    Packet p2 = makeFloodPacket(0x02);
    t.markSeen(&p1);
    EXPECT_FALSE(t.wasSeen(&p2));
}

// Canonical pattern used at every onRecvPacket call site:
//   if (!wasSeen(pkt)) { markSeen(pkt); process(pkt); }
TEST(SimpleMeshTables, QueryThenMark_WorksCorrectly) {
    SimpleMeshTables t;
    Packet p = makeFloodPacket(0x01);
    EXPECT_FALSE(t.wasSeen(&p));
    t.markSeen(&p);
    EXPECT_TRUE(t.wasSeen(&p));
}

// ── dup stats ────────────────────────────────────────────────────────────────

TEST(SimpleMeshTables, WasSeen_IncrementsFloodDupStat) {
    SimpleMeshTables t;
    Packet p = makeFloodPacket(0x01);
    t.markSeen(&p);
    t.wasSeen(&p);
    EXPECT_EQ(1u, t.getNumFloodDups());
    EXPECT_EQ(0u, t.getNumDirectDups());
}

TEST(SimpleMeshTables, WasSeen_IncrementsDirectDupStat) {
    SimpleMeshTables t;
    Packet p = makeDirectPacket(0x01);
    t.markSeen(&p);
    t.wasSeen(&p);
    EXPECT_EQ(0u, t.getNumFloodDups());
    EXPECT_EQ(1u, t.getNumDirectDups());
}

// ── clear ────────────────────────────────────────────────────────────────────

TEST(SimpleMeshTables, Clear_RemovesSeenPacket) {
    SimpleMeshTables t;
    Packet p = makeFloodPacket(0x01);
    t.markSeen(&p);
    ASSERT_TRUE(t.wasSeen(&p));
    t.clear(&p);
    EXPECT_FALSE(t.wasSeen(&p));
}

TEST(SimpleMeshTables, AckCrcZeroIsAValidUnseenValue) {
    SimpleMeshTables t;
    Packet p = makeAckPacket(0);

    EXPECT_FALSE(t.wasSeen(&p));
    t.markSeen(&p);
    EXPECT_TRUE(t.wasSeen(&p));
}

TEST(SimpleMeshTables, AckDedupUsesCrcAndIgnoresOptionalSuffix) {
    SimpleMeshTables t;
    Packet first = makeAckPacket(0xC3B2A141);
    Packet repeated = first;
    first.payload[4] = 0x10;
    first.payload[5] = 0x20;
    first.payload_len = 6;
    repeated.payload[4] = 0x99;
    repeated.payload[5] = 0x88;
    repeated.payload_len = 6;

    t.markSeen(&first);
    EXPECT_TRUE(t.wasSeen(&repeated));
}

TEST(SimpleMeshTables, AckTrafficDoesNotEvictGeneralPacketHashes) {
    SimpleMeshTables t;
    Packet retained = makeFloodPacket(0x5A);
    t.markSeen(&retained);

    for (int i = 0; i < MAX_PACKET_HASHES + 1; i++) {
        Packet ack = makeAckPacket((uint32_t)i);
        t.markSeen(&ack);
    }

    EXPECT_TRUE(t.wasSeen(&retained));
}

TEST(SimpleMeshTables, ShortAckPayloadFallsBackToSafeGeneralDedup) {
    SimpleMeshTables t;
    Packet p = makeAckPacket(0x7B);
    p.payload_len = 1;

    EXPECT_FALSE(t.wasSeen(&p));
    t.markSeen(&p);
    EXPECT_TRUE(t.wasSeen(&p));
    t.clear(&p);
    EXPECT_FALSE(t.wasSeen(&p));
}

TEST(RouteHashPrefixes, MatchesSharedOneTwoOrThreeBytes) {
    const uint8_t configured[] = {0x86, 0x0c, 0xca};
    const uint8_t one_byte[] = {0x86};
    const uint8_t two_bytes[] = {0x86, 0x0c};
    const uint8_t three_bytes[] = {0x86, 0x0c, 0xca};
    const uint8_t mismatch[] = {0x86, 0x0d};

    EXPECT_TRUE(routeHashPrefixesOverlap(configured, 3, one_byte, 1));
    EXPECT_TRUE(routeHashPrefixesOverlap(configured, 3, two_bytes, 2));
    EXPECT_TRUE(routeHashPrefixesOverlap(configured, 3, three_bytes, 3));
    EXPECT_TRUE(routeHashPrefixesOverlap(one_byte, 1, configured, 3));
    EXPECT_FALSE(routeHashPrefixesOverlap(configured, 3, mismatch, 2));
}

TEST(SimpleMeshTables, ShortFailurePrefixUpdatesOverlappingLongEntry) {
    SimpleMeshTables t;
    const uint8_t configured[] = {0x86, 0x0c, 0xca};
    const uint8_t observed[] = {0x86};

    ASSERT_TRUE(t.setRecentRepeater(configured, 3, 12));
    ASSERT_TRUE(t.decrementRecentRepeaterSnrX4(observed, 1, 1));
    const auto* info = t.findRecentRepeaterByHash(configured, 3);
    ASSERT_NE(nullptr, info);
    EXPECT_EQ(11, info->snr_x4);
    EXPECT_EQ(1, t.getRecentRepeaterCount());
}

int main(int argc, char** argv) {
    ::testing::InitGoogleTest(&argc, argv);
    return RUN_ALL_TESTS();
}
