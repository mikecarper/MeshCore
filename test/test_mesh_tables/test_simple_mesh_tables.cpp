#include <gtest/gtest.h>
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

static Packet makeMultipartAckPacket(uint32_t crc, uint8_t remaining = 1, bool direct = true) {
    Packet p;
    p.header = (direct ? ROUTE_TYPE_DIRECT : ROUTE_TYPE_FLOOD)
        | (PAYLOAD_TYPE_MULTIPART << PH_TYPE_SHIFT);
    p.payload[0] = (remaining << 4) | PAYLOAD_TYPE_ACK;
    memcpy(&p.payload[1], &crc, sizeof(crc));
    p.payload_len = sizeof(crc) + 1;
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

TEST(SimpleMeshTables, TransportScopeDoesNotChangeDuplicateIdentity) {
    SimpleMeshTables t;
    Packet unscoped = makeFloodPacket(0x35);
    Packet scoped = unscoped;
    scoped.header = (scoped.header & (uint8_t)~PH_ROUTE_MASK) | ROUTE_TYPE_TRANSPORT_FLOOD;
    scoped.transport_codes[0] = 0x1234;
    scoped.transport_codes[1] = 0x5678;

    t.markSeen(&unscoped);
    EXPECT_TRUE(t.wasSeen(&scoped));
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

TEST(SimpleMeshTables, AckDedupUsesTheCompletePayloadIdentity) {
    SimpleMeshTables t;
    Packet first = makeAckPacket(0xC3B2A141);
    Packet repeated = first;
    Packet different_attempt = first;
    first.payload[4] = 0x10;
    first.payload[5] = 0x20;
    first.payload_len = 6;
    repeated.payload[4] = 0x10;
    repeated.payload[5] = 0x20;
    repeated.payload_len = 6;
    different_attempt.payload[4] = 0x14;
    different_attempt.payload[5] = 0x20;
    different_attempt.payload_len = 6;

    t.markSeen(&first);
    EXPECT_TRUE(t.wasSeen(&repeated));
    EXPECT_FALSE(t.wasSeen(&different_attempt));
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

TEST(SimpleMeshTables, MultipartAckTrafficDoesNotEvictGeneralPacketHashes) {
    SimpleMeshTables t;
    Packet retained = makeFloodPacket(0x5B);
    t.markSeen(&retained);

    for (int i = 0; i < MAX_PACKET_HASHES + 1; i++) {
        Packet ack = makeMultipartAckPacket((uint32_t)i, (uint8_t)(i & 0x0F));
        t.markSeen(&ack);
    }

    EXPECT_TRUE(t.wasSeen(&retained));
}

TEST(SimpleMeshTables, NormalAndMultipartAckTransmissionsAreDistinct) {
    SimpleMeshTables t;
    Packet normal = makeAckPacket(0x10203040);
    Packet multipart = makeMultipartAckPacket(0x10203040);

    t.markSeen(&normal);
    EXPECT_FALSE(t.wasSeen(&multipart));
    t.markSeen(&multipart);
    EXPECT_TRUE(t.wasSeen(&multipart));
    EXPECT_TRUE(t.wasSeen(&normal));
}

TEST(SimpleMeshTables, MultipartRemainingCountIsPartOfItsIdentity) {
    SimpleMeshTables t;
    Packet first = makeMultipartAckPacket(0x55667788, 1);
    Packet second = makeMultipartAckPacket(0x55667788, 0);

    t.markSeen(&first);
    EXPECT_FALSE(t.wasSeen(&second));
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
    SimpleMeshTables::RecentRepeaterInfo storage[MAX_RECENT_REPEATERS];
    SimpleMeshTables t(storage, MAX_RECENT_REPEATERS);
    const uint8_t configured[] = {0x86, 0x0c, 0xca};
    const uint8_t observed[] = {0x86};

    ASSERT_TRUE(t.setRecentRepeater(configured, 3, 12));
    ASSERT_TRUE(t.decrementRecentRepeaterSnrX4(observed, 1, 1));
    const auto* info = t.findRecentRepeaterByHash(configured, 3);
    ASSERT_NE(nullptr, info);
    EXPECT_EQ(11, info->snr_x4);
    EXPECT_EQ(1, t.getRecentRepeaterCount());
}

TEST(SimpleMeshTables, RecentRepeatersExpireOnlyAfterTwentyFourHours) {
    SimpleMeshTables::RecentRepeaterInfo storage[MAX_RECENT_REPEATERS];
    SimpleMeshTables t(storage, MAX_RECENT_REPEATERS);
    const uint8_t first[] = {0x86, 0x0c, 0xca};
    const uint8_t second[] = {0x71, 0xce, 0x82};
    constexpr uint32_t twenty_four_hours = 24UL * 60UL * 60UL * 1000UL;

    ASSERT_TRUE(t.setRecentRepeater(first, 3, 12));
    ASSERT_TRUE(t.setRecentRepeater(second, 3, 8));
    EXPECT_EQ(0, t.expireRecentRepeaters(twenty_four_hours, twenty_four_hours));
    EXPECT_EQ(2, t.getRecentRepeaterCount());

    EXPECT_EQ(2, t.expireRecentRepeaters(twenty_four_hours + 1, twenty_four_hours));
    EXPECT_EQ(0, t.getRecentRepeaterCount());
    EXPECT_EQ(nullptr, t.findRecentRepeaterByHash(first, 3));
}

TEST(SimpleMeshTables, FullRecentRepeaterTableStillEvictsDeterministically) {
    SimpleMeshTables::RecentRepeaterInfo storage[2];
    SimpleMeshTables t(storage, 2);
    const uint8_t first[] = {0x10};
    const uint8_t second[] = {0x20};
    const uint8_t replacement[] = {0x30};

    ASSERT_TRUE(t.setRecentRepeater(first, 1, 4));
    ASSERT_TRUE(t.setRecentRepeater(second, 1, 8));
    ASSERT_TRUE(t.setRecentRepeater(replacement, 1, 12));

    EXPECT_EQ(nullptr, t.findRecentRepeaterByHash(first, 1));
    EXPECT_NE(nullptr, t.findRecentRepeaterByHash(second, 1));
    EXPECT_NE(nullptr, t.findRecentRepeaterByHash(replacement, 1));
    EXPECT_EQ(2, t.getRecentRepeaterCount());
}

TEST(SimpleMeshTables, ExpirationKeepsOccupiedEntriesPacked) {
    SimpleMeshTables::RecentRepeaterInfo storage[4];
    SimpleMeshTables t(storage, 4);
    const uint8_t first[] = {0x10};
    const uint8_t expired[] = {0x20};
    const uint8_t third[] = {0x30};
    const uint8_t added[] = {0x40};

    ASSERT_TRUE(t.setRecentRepeater(first, 1, 4));
    ASSERT_TRUE(t.setRecentRepeater(expired, 1, 8));
    ASSERT_TRUE(t.setRecentRepeater(third, 1, 12));
    storage[0].last_heard_millis = 100;
    storage[1].last_heard_millis = 0;
    storage[2].last_heard_millis = 100;

    EXPECT_EQ(1, t.expireRecentRepeaters(101, 50));
    EXPECT_EQ(2, t.getRecentRepeaterCount());
    EXPECT_NE(nullptr, t.findRecentRepeaterByHash(first, 1));
    EXPECT_EQ(nullptr, t.findRecentRepeaterByHash(expired, 1));
    EXPECT_NE(nullptr, t.findRecentRepeaterByHash(third, 1));

    ASSERT_TRUE(t.setRecentRepeater(added, 1, 16));
    EXPECT_EQ(3, t.getRecentRepeaterCount());
    EXPECT_NE(nullptr, t.findRecentRepeaterByHash(added, 1));
    EXPECT_EQ(0, storage[3].prefix_len);
}

int main(int argc, char** argv) {
    ::testing::InitGoogleTest(&argc, argv);
    return RUN_ALL_TESTS();
}
