#include <gtest/gtest.h>

#include <helpers/FloodFilterPolicy.h>

static mesh::Packet makeFloodPacket(uint8_t hash_size,
                                    const uint8_t* path,
                                    uint8_t path_hops) {
  mesh::Packet packet;
  packet.header = ROUTE_TYPE_FLOOD | (PAYLOAD_TYPE_GRP_DATA << PH_TYPE_SHIFT);
  packet.setPathHashSizeAndCount(hash_size, path_hops);
  memcpy(packet.path, path, hash_size * path_hops);
  packet.payload_len = 0;
  return packet;
}

TEST(FloodFilterBlacklist, ThreeBytePathMatchesOneExactIdInAnyPosition) {
  const uint8_t blacklist[][3] = {
    {0x10, 0x20, 0x30},
    {0xAA, 0xBB, 0xCC},
    {0x70, 0x80, 0x90},
  };
  const uint8_t path[] = {
    0x01, 0x02, 0x03,
    0x70, 0x80, 0x90,
    0x04, 0x05, 0x06,
  };
  mesh::Packet packet = makeFloodPacket(3, path, 3);

  EXPECT_TRUE(FloodFilterPolicy::pathMatchesBlacklist(
      &packet, &blacklist[0][0], 3));
}

TEST(FloodFilterBlacklist, ThreeBytePathRequiresAnExactThirdByte) {
  const uint8_t blacklist[][3] = {{0xAA, 0xBB, 0xCC}};
  const uint8_t path[] = {0xAA, 0xBB, 0xCD};
  mesh::Packet packet = makeFloodPacket(3, path, 1);

  EXPECT_FALSE(FloodFilterPolicy::pathMatchesBlacklist(
      &packet, &blacklist[0][0], 1));
}

TEST(FloodFilterBlacklist, TwoBytePathRequiresTwoMatchingEntries) {
  const uint8_t blacklist[][3] = {
    {0xAA, 0xBB, 0x01},
    {0x11, 0x22, 0x02},
  };
  const uint8_t one_match_path[] = {
    0xAA, 0xBB,
    0x33, 0x44,
  };
  const uint8_t two_match_path[] = {
    0x11, 0x22,
    0x33, 0x44,
    0xAA, 0xBB,
  };
  mesh::Packet one_match = makeFloodPacket(2, one_match_path, 2);
  mesh::Packet two_matches = makeFloodPacket(2, two_match_path, 3);

  EXPECT_FALSE(FloodFilterPolicy::pathMatchesBlacklist(
      &one_match, &blacklist[0][0], 2));
  EXPECT_TRUE(FloodFilterPolicy::pathMatchesBlacklist(
      &two_matches, &blacklist[0][0], 2));
}

TEST(FloodFilterBlacklist, OnePathEntryCountsOnceWhenIdsSharePrefix) {
  const uint8_t blacklist[][3] = {
    {0xAA, 0xBB, 0x01},
    {0xAA, 0xBB, 0x02},
  };
  const uint8_t path[] = {0xAA, 0xBB};
  mesh::Packet packet = makeFloodPacket(2, path, 1);

  EXPECT_FALSE(FloodFilterPolicy::pathMatchesBlacklist(
      &packet, &blacklist[0][0], 2));
}

TEST(FloodFilterBlacklist, RepeatedTwoBytePathEntriesCountSeparately) {
  const uint8_t blacklist[][3] = {{0xAA, 0xBB, 0x01}};
  const uint8_t path[] = {
    0xAA, 0xBB,
    0xAA, 0xBB,
  };
  mesh::Packet packet = makeFloodPacket(2, path, 2);

  EXPECT_TRUE(FloodFilterPolicy::pathMatchesBlacklist(
      &packet, &blacklist[0][0], 1));
}

TEST(FloodFilterBlacklist, OneBytePathsNeverMatch) {
  const uint8_t blacklist[][3] = {{0xAA, 0xBB, 0xCC}};
  const uint8_t path[] = {0xAA, 0xAA, 0xAA};
  mesh::Packet packet = makeFloodPacket(1, path, 3);

  EXPECT_FALSE(FloodFilterPolicy::pathMatchesBlacklist(
      &packet, &blacklist[0][0], 1));
}

TEST(FloodFilterBlacklist, Esp32MaximumListIncludesTheLastEntry) {
  uint8_t blacklist[255][3];
  for (uint16_t i = 0; i < 255; i++) {
    blacklist[i][0] = (uint8_t)i;
    blacklist[i][1] = (uint8_t)(i ^ 0xA5);
    blacklist[i][2] = (uint8_t)(i ^ 0x5A);
  }
  const uint8_t path[] = {
    blacklist[254][0],
    blacklist[254][1],
    blacklist[254][2],
  };
  mesh::Packet packet = makeFloodPacket(3, path, 1);

  EXPECT_TRUE(FloodFilterPolicy::pathMatchesBlacklist(
      &packet, &blacklist[0][0], 255));
}

TEST(FloodFilterScope, RegionRequirementHasTheExpectedTruthTable) {
  EXPECT_TRUE(FloodFilterPolicy::scopeRuleAllowed(false, false));
  EXPECT_TRUE(FloodFilterPolicy::scopeRuleAllowed(false, true));
  EXPECT_FALSE(FloodFilterPolicy::scopeRuleAllowed(true, false));
  EXPECT_TRUE(FloodFilterPolicy::scopeRuleAllowed(true, true));
}

TEST(FloodFilterScope, SlowTimingFlagRoundTripsWithoutChangingSelector) {
  const uint8_t selector = 16;
  const uint8_t stored =
      FloodFilterPolicy::encodeScopeSelector(selector, true);

  EXPECT_EQ(selector, FloodFilterPolicy::scopeSelectorValue(stored));
  EXPECT_TRUE(FloodFilterPolicy::scopeUsesSlowTiming(stored));
  EXPECT_FALSE(FloodFilterPolicy::scopeUsesSlowTiming(
      FloodFilterPolicy::encodeScopeSelector(selector, false)));
}

TEST(FloodFilterScope, OnlyChangedFastRulesReceiveFastTrackTreatment) {
  EXPECT_TRUE(FloodFilterPolicy::fastTrackScopeChange(true, false));
  EXPECT_FALSE(FloodFilterPolicy::fastTrackScopeChange(true, true));
  EXPECT_FALSE(FloodFilterPolicy::fastTrackScopeChange(false, false));
  EXPECT_FALSE(FloodFilterPolicy::fastTrackScopeChange(false, true));
}

TEST(FloodFilterScope, SlowTimingUsesMaximumTxDelayFactor) {
  EXPECT_EQ(2, FloodFilterPolicy::SLOW_SCOPE_TX_DELAY_FACTOR);
  EXPECT_EQ(1000U, FloodFilterPolicy::slowScopeMaxDelay(100));
  EXPECT_EQ(FloodFilterPolicy::MAX_DISPATCH_DELAY,
            FloodFilterPolicy::slowScopeMaxDelay(0xFFFFFFFFU));
}

TEST(FloodFilterScope, SlowTimingDoublesRxDelayBaseWithMinimumOfTwo) {
  EXPECT_FLOAT_EQ(2.0f, FloodFilterPolicy::slowScopeRxDelayBase(0.0f));
  EXPECT_FLOAT_EQ(2.0f, FloodFilterPolicy::slowScopeRxDelayBase(0.5f));
  EXPECT_FLOAT_EQ(2.0f, FloodFilterPolicy::slowScopeRxDelayBase(1.0f));
  EXPECT_FLOAT_EQ(4.0f, FloodFilterPolicy::slowScopeRxDelayBase(2.0f));
  EXPECT_FLOAT_EQ(20.0f, FloodFilterPolicy::slowScopeRxDelayBase(10.0f));
  EXPECT_FLOAT_EQ(24.0f, FloodFilterPolicy::slowScopeRxDelayBase(12.0f));
  EXPECT_FLOAT_EQ(40.0f, FloodFilterPolicy::slowScopeRxDelayBase(20.0f));
}

TEST(FloodFilterScope, ChannelRequirementTableKeepsLegacyGlobalBehaviorWhenEmpty) {
  EXPECT_EQ(
      FloodFilterPolicy::CHANNEL_SCOPE_USE_GLOBAL,
      FloodFilterPolicy::channelScopeGate(false, true, true, false, false));
}

TEST(FloodFilterScope, ChannelRequirementTableDoesNotChangeOtherPayloadTypes) {
  EXPECT_EQ(
      FloodFilterPolicy::CHANNEL_SCOPE_USE_GLOBAL,
      FloodFilterPolicy::channelScopeGate(true, false, false, false, false));
}

TEST(FloodFilterScope, UnlistedGroupChannelsBypassOnlyTheScopeGate) {
  EXPECT_EQ(
      FloodFilterPolicy::CHANNEL_SCOPE_BYPASS,
      FloodFilterPolicy::channelScopeGate(true, true, false, false, false));
  EXPECT_EQ(
      FloodFilterPolicy::CHANNEL_SCOPE_BYPASS,
      FloodFilterPolicy::channelScopeGate(true, true, false, true, false));
}

TEST(FloodFilterScope, ListedChannelsRequireAnAllowedIncomingScope) {
  EXPECT_EQ(
      FloodFilterPolicy::CHANNEL_SCOPE_REQUIRED_REJECTED,
      FloodFilterPolicy::channelScopeGate(true, true, true, false, true));
  EXPECT_EQ(
      FloodFilterPolicy::CHANNEL_SCOPE_REQUIRED_REJECTED,
      FloodFilterPolicy::channelScopeGate(true, true, true, true, false));
  EXPECT_EQ(
      FloodFilterPolicy::CHANNEL_SCOPE_REQUIRED_ALLOWED,
      FloodFilterPolicy::channelScopeGate(true, true, true, true, true));
}

TEST(FloodFilterScope, AddsScopeToUnscopedFloodWithoutChangingPayloadType) {
  mesh::Packet packet;
  packet.header = ROUTE_TYPE_FLOOD | (PAYLOAD_TYPE_GRP_TXT << PH_TYPE_SHIFT);
  packet.transport_codes[0] = 0;
  packet.transport_codes[1] = 0;

  EXPECT_TRUE(FloodFilterPolicy::setTransportScope(&packet, 0x1234));
  EXPECT_EQ(ROUTE_TYPE_TRANSPORT_FLOOD, packet.getRouteType());
  EXPECT_EQ(PAYLOAD_TYPE_GRP_TXT, packet.getPayloadType());
  EXPECT_EQ(0x1234, packet.transport_codes[0]);
  EXPECT_EQ(0, packet.transport_codes[1]);
}

TEST(FloodFilterScope, ReplacesExistingScopeAndClearsSecondaryCode) {
  mesh::Packet packet;
  packet.header =
      ROUTE_TYPE_TRANSPORT_FLOOD | (PAYLOAD_TYPE_GRP_DATA << PH_TYPE_SHIFT);
  packet.transport_codes[0] = 0x1111;
  packet.transport_codes[1] = 0x2222;

  EXPECT_TRUE(FloodFilterPolicy::setTransportScope(&packet, 0x3333));
  EXPECT_EQ(0x3333, packet.transport_codes[0]);
  EXPECT_EQ(0, packet.transport_codes[1]);
}

TEST(FloodFilterScope, ExistingExactScopeIsANoOp) {
  mesh::Packet packet;
  packet.header =
      ROUTE_TYPE_TRANSPORT_FLOOD | (PAYLOAD_TYPE_GRP_DATA << PH_TYPE_SHIFT);
  packet.transport_codes[0] = 0x3333;
  packet.transport_codes[1] = 0;

  EXPECT_FALSE(FloodFilterPolicy::setTransportScope(&packet, 0x3333));
  EXPECT_EQ(0x3333, packet.transport_codes[0]);
  EXPECT_EQ(0, packet.transport_codes[1]);
}

TEST(FloodFilterScope, MatchingPrimaryStillChangesWhenSecondaryIsPresent) {
  mesh::Packet packet;
  packet.header =
      ROUTE_TYPE_TRANSPORT_FLOOD | (PAYLOAD_TYPE_GRP_DATA << PH_TYPE_SHIFT);
  packet.transport_codes[0] = 0x3333;
  packet.transport_codes[1] = 0x4444;

  EXPECT_TRUE(FloodFilterPolicy::setTransportScope(&packet, 0x3333));
  EXPECT_EQ(0x3333, packet.transport_codes[0]);
  EXPECT_EQ(0, packet.transport_codes[1]);
}

TEST(FloodFilterScope, DirectPacketsAreNeverConvertedToFlood) {
  mesh::Packet packet;
  packet.header =
      ROUTE_TYPE_DIRECT | (PAYLOAD_TYPE_GRP_DATA << PH_TYPE_SHIFT);
  packet.transport_codes[0] = 0x1111;
  packet.transport_codes[1] = 0x2222;

  EXPECT_FALSE(FloodFilterPolicy::setTransportScope(&packet, 0x3333));
  EXPECT_EQ(ROUTE_TYPE_DIRECT, packet.getRouteType());
  EXPECT_EQ(0x1111, packet.transport_codes[0]);
  EXPECT_EQ(0x2222, packet.transport_codes[1]);
}

int main(int argc, char** argv) {
  ::testing::InitGoogleTest(&argc, argv);
  return RUN_ALL_TESTS();
}
