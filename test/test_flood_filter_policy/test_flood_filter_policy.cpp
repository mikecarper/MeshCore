#include <gtest/gtest.h>

#include <helpers/FloodFilterPolicy.h>
#include <helpers/RegionNameUtils.h>

struct FloodPersistenceRow {
  bool active;
};

TEST(FloodFilterPersistence, DefaultRowsTrimTheInactiveTail) {
  FloodPersistenceRow rows[63] = {};
  rows[0].active = true;
  rows[1].active = true;

  EXPECT_EQ(2, FloodFilterPolicy::forwardPersistenceCount(
                   rows, 63, false));
}

TEST(FloodFilterPersistence, SparseHighSlotPreservesEveryEarlierPosition) {
  FloodPersistenceRow rows[63] = {};
  rows[0].active = true;
  rows[47].active = true;

  EXPECT_EQ(48, FloodFilterPolicy::forwardPersistenceCount(
                    rows, 63, false));
}

TEST(FloodFilterPersistence, EmptyForwardPhaseSerializesNoRows) {
  FloodPersistenceRow rows[63] = {};
  rows[62].active = true;

  EXPECT_EQ(0, FloodFilterPolicy::forwardPersistenceCount(
                   rows, 63, true));
}

TEST(FloodFilterPersistence, OldFullCountRemainsLoadCompatible) {
  EXPECT_TRUE(FloodFilterPolicy::forwardPersistenceCountSupported(63, 63));
  EXPECT_FALSE(FloodFilterPolicy::forwardPersistenceCountSupported(64, 63));
}

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

TEST(FloodFilterBlacklist, ConfiguredBucketStopsAtEmptyTrailingIds) {
  uint8_t bucket[17][3] = {
    {0x10, 0x20, 0x30},
    {0xAA, 0xBB, 0xCC},
  };
  const uint8_t path[] = {0xAA, 0xBB, 0xCC};
  mesh::Packet packet = makeFloodPacket(3, path, 1);

  EXPECT_EQ(2, FloodFilterPolicy::configuredIdCount(
                   &bucket[0][0], 17));
  EXPECT_TRUE(FloodFilterPolicy::pathMatchesConfiguredIds(
      &packet, &bucket[0][0], 17));
}

TEST(FloodRulePrefix, SupportsOneTwoAndThreeBytePathPrefixes) {
  const uint8_t one_byte_path[] = {0x11, 0x22, 0x33};
  const uint8_t two_byte_path[] = {0x11, 0x22, 0x33, 0x44};
  const uint8_t three_byte_path[] = {
    0x11, 0x22, 0x33,
    0x44, 0x55, 0x66,
  };
  mesh::Packet one = makeFloodPacket(1, one_byte_path, 3);
  mesh::Packet two = makeFloodPacket(2, two_byte_path, 2);
  mesh::Packet three = makeFloodPacket(3, three_byte_path, 2);

  EXPECT_TRUE(FloodFilterPolicy::pathStartsWith(
      &one, 1, 2, one_byte_path));
  EXPECT_TRUE(FloodFilterPolicy::pathStartsWith(
      &two, 2, 2, two_byte_path));
  EXPECT_TRUE(FloodFilterPolicy::pathStartsWith(
      &three, 3, 2, three_byte_path));
}

TEST(FloodRulePrefix, RequiresTheExactPathWidthAndStartingSequence) {
  const uint8_t path[] = {
    0x11, 0x22,
    0x33, 0x44,
  };
  const uint8_t wrong_start[] = {0x33, 0x44};
  mesh::Packet packet = makeFloodPacket(2, path, 2);

  EXPECT_FALSE(FloodFilterPolicy::pathStartsWith(
      &packet, 1, 1, path));
  EXPECT_FALSE(FloodFilterPolicy::pathStartsWith(
      &packet, 2, 1, wrong_start));
  EXPECT_FALSE(FloodFilterPolicy::pathStartsWith(
      &packet, 2, 3, path));
}

TEST(FloodRuleIncomingScope, MatchesOriginalScopeClasses) {
  using namespace FloodFilterPolicy;
  EXPECT_TRUE(ruleIncomingScopeMatches(
      RULE_IN_ANY, false, 0, false, 0));
  EXPECT_TRUE(ruleIncomingScopeMatches(
      RULE_IN_NONE, false, 0, true, 0));
  EXPECT_FALSE(ruleIncomingScopeMatches(
      RULE_IN_NONE, true, 0x1234, true, 0));
  EXPECT_TRUE(ruleIncomingScopeMatches(
      RULE_IN_SCOPED, true, 0x1234, false, 0));
  EXPECT_TRUE(ruleIncomingScopeMatches(
      RULE_IN_ALLOWED, true, 0x1234, true, 0));
  EXPECT_TRUE(ruleIncomingScopeMatches(
      RULE_IN_UNKNOWN, true, 0x1234, false, 0));
}

TEST(FloodRuleIncomingScope, ExactScopeDoesNotCrossMatch) {
  using namespace FloodFilterPolicy;
  EXPECT_TRUE(ruleIncomingScopeMatches(
      RULE_IN_SCOPE, true, 0x1234, true, 0x1234));
  EXPECT_FALSE(ruleIncomingScopeMatches(
      RULE_IN_SCOPE, true, 0x1235, true, 0x1234));
  EXPECT_FALSE(ruleIncomingScopeMatches(
      RULE_IN_REGION, true, 0x1234, true, 0));
}

TEST(FloodRuleIncomingScope, RegionIdentityUsesCanonicalNames) {
  EXPECT_TRUE(RegionNameUtils::equivalent("usa", "#usa"));
  EXPECT_TRUE(RegionNameUtils::equivalent("BlackHole86", "BlackHole86"));
  EXPECT_FALSE(RegionNameUtils::equivalent("usa", "BlackHole86"));
}

TEST(FloodRuleRate, BlocksAtTheConfiguredPerMinuteLimit) {
  EXPECT_FALSE(FloodFilterPolicy::rateLimitReached(
      false, 1000, 0, 500, 2));
  EXPECT_FALSE(FloodFilterPolicy::rateLimitReached(
      true, 2000, 1000, 1, 2));
  EXPECT_TRUE(FloodFilterPolicy::rateLimitReached(
      true, 2000, 1000, 2, 2));
  EXPECT_TRUE(FloodFilterPolicy::rateLimitReached(
      true, 2000, 1000, 0, 0));
}

TEST(FloodRuleRate, StartsANewWindowAfterSixtySeconds) {
  EXPECT_FALSE(FloodFilterPolicy::rateLimitReached(
      true, 61000, 1000, 2, 2));
  EXPECT_TRUE(FloodFilterPolicy::rateLimitReached(
      true, 999, 0xFFFFFF00U, 2, 2));
  EXPECT_FALSE(FloodFilterPolicy::rateLimitReached(
      true, 60000, 0xFFFFFF00U, 2, 2));
}

TEST(FloodRuleAuthentication, CacheKeysCompareOnlyTheirActiveBytes) {
  uint8_t first[32] = {0};
  uint8_t same[32] = {0};
  uint8_t different[32] = {0};
  first[0] = same[0] = different[0] = 0xA5;
  first[15] = same[15] = different[15] = 0x5A;
  first[31] = 0x11;
  same[31] = 0x22;
  different[15] = 0x5B;

  EXPECT_TRUE(FloodFilterPolicy::sameChannelKey(16, first, 16, same));
  EXPECT_FALSE(FloodFilterPolicy::sameChannelKey(
      16, first, 16, different));
  EXPECT_FALSE(FloodFilterPolicy::sameChannelKey(16, first, 32, first));
  EXPECT_FALSE(FloodFilterPolicy::sameChannelKey(0, first, 0, same));
}

TEST(FloodRuleOrder, HighestPriorityWinsAndSlotBreaksTies) {
  const uint8_t priorities[] = {10, 30, 30, 5};
  uint32_t matches = 0x0F;
  uint32_t visited = 0;

  int first = FloodFilterPolicy::nextOrderedRule(
      matches, visited, priorities, 4);
  ASSERT_EQ(1, first);
  visited |= (uint32_t)1U << first;
  EXPECT_EQ(2, FloodFilterPolicy::nextOrderedRule(
                   matches, visited, priorities, 4));
}

TEST(FloodRuleOrder, StopRemovesOnlyLowerOrderedMatches) {
  const uint8_t priorities[] = {10, 30, 20, 5};
  const uint8_t stop_flags[] = {0, 0, 1, 0};

  EXPECT_EQ(0x06U, FloodFilterPolicy::truncateRulesAtStop(
                       0x0F, priorities, stop_flags, 4));
}

TEST(FloodRuleOrder, StopAtEqualPriorityUsesLowerSlotFirst) {
  const uint8_t priorities[] = {40, 40, 50};
  const uint8_t stop_flags[] = {1, 0, 0};

  EXPECT_EQ(0x05U, FloodFilterPolicy::truncateRulesAtStop(
                       0x07, priorities, stop_flags, 3));
}

TEST(FloodRuleOrder, NoStopPreservesEveryMatch) {
  const uint8_t priorities[] = {1, 200, 3, 99};
  const uint8_t stop_flags[] = {0, 0, 0, 0};

  EXPECT_EQ(0x0BU, FloodFilterPolicy::truncateRulesAtStop(
                       0x0B, priorities, stop_flags, 4));
}

TEST(FloodRuleOrder, MissingRegionTargetDoesNotStopLowerSafetyRules) {
  EXPECT_TRUE(FloodFilterPolicy::stopActionApplies(true, false, false));
  EXPECT_TRUE(FloodFilterPolicy::stopActionApplies(true, true, true));
  EXPECT_FALSE(FloodFilterPolicy::stopActionApplies(true, true, false));
  EXPECT_FALSE(FloodFilterPolicy::stopActionApplies(false, true, true));

  const uint8_t priorities[] = {200, 100};
  const uint8_t stop_flags[] = {
      (uint8_t)(FloodFilterPolicy::stopActionApplies(true, true, false)
                    ? 1 : 0),
      0,
  };
  EXPECT_EQ(0x03U, FloodFilterPolicy::truncateRulesAtStop(
                       0x03, priorities, stop_flags, 2));
}

TEST(FloodRuleOrder, ThirtyOneSlotTableIncludesTheLastSlot) {
  uint8_t priorities[31] = {0};
  uint8_t stop_flags[31] = {0};
  priorities[30] = 200;
  stop_flags[30] = 1;
  const uint32_t matches = ((uint32_t)1U << 30) | 1U;

  EXPECT_EQ(30, FloodFilterPolicy::nextOrderedRule(
                    matches, (uint32_t)0, priorities, 31));
  EXPECT_EQ((uint32_t)1U << 30,
            FloodFilterPolicy::truncateRulesAtStop(
                matches, priorities, stop_flags, 31));
}

TEST(FloodRuleOrder, ThirtyTwoSlotTableIncludesTheLastSlot) {
  uint8_t priorities[32] = {0};
  uint8_t stop_flags[32] = {0};
  priorities[31] = 200;
  stop_flags[31] = 1;
  const uint32_t matches = ((uint32_t)1U << 31) | 1U;

  EXPECT_EQ(31, FloodFilterPolicy::nextOrderedRule(
                    matches, (uint32_t)0, priorities, 32));
  EXPECT_EQ((uint32_t)1U << 31,
            FloodFilterPolicy::truncateRulesAtStop(
                matches, priorities, stop_flags, 32));
}

TEST(FloodRuleOrder, SixtyThreeSlotTableIncludesTheLastSlot) {
  uint8_t priorities[63] = {0};
  uint8_t stop_flags[63] = {0};
  priorities[62] = 200;
  stop_flags[62] = 1;
  const uint64_t matches = ((uint64_t)1U << 62) | 1U;

  EXPECT_EQ(62, FloodFilterPolicy::nextOrderedRule(
                    matches, (uint64_t)0, priorities, 63));
  EXPECT_EQ((uint64_t)1U << 62,
            FloodFilterPolicy::truncateRulesAtStop(
                matches, priorities, stop_flags, 63));
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
      FloodFilterPolicy::encodeScopeSelector(
          selector, true, FloodFilterPolicy::SCOPE_PATH_BLACKLIST);

  EXPECT_EQ(selector, FloodFilterPolicy::scopeSelectorValue(stored));
  EXPECT_TRUE(FloodFilterPolicy::scopeUsesSlowTiming(stored));
  EXPECT_TRUE(FloodFilterPolicy::scopeRequiresBlacklistPath(stored));
  EXPECT_FALSE(FloodFilterPolicy::scopeUsesSlowTiming(
      FloodFilterPolicy::encodeScopeSelector(selector, false)));
  EXPECT_FALSE(FloodFilterPolicy::scopeRequiresBlacklistPath(
      FloodFilterPolicy::encodeScopeSelector(
          selector, true, FloodFilterPolicy::SCOPE_PATH_NONE)));
}

TEST(FloodFilterScope, PathQualifiedScopeRequiresBlacklistMatch) {
  const uint8_t selector = 16;
  const uint8_t general =
      FloodFilterPolicy::encodeScopeSelector(
          selector, false, FloodFilterPolicy::SCOPE_PATH_NONE);
  const uint8_t qualified =
      FloodFilterPolicy::encodeScopeSelector(
          selector, false, FloodFilterPolicy::SCOPE_PATH_BLACKLIST);

  EXPECT_TRUE(FloodFilterPolicy::scopePathQualifierMatches(general, false));
  EXPECT_TRUE(FloodFilterPolicy::scopePathQualifierMatches(general, true));
  EXPECT_FALSE(FloodFilterPolicy::scopePathQualifierMatches(qualified, false));
  EXPECT_TRUE(FloodFilterPolicy::scopePathQualifierMatches(qualified, true));

  EXPECT_FALSE(FloodFilterPolicy::scopePathPassMatches(general, true, true));
  EXPECT_TRUE(FloodFilterPolicy::scopePathPassMatches(general, false, false));
  EXPECT_TRUE(FloodFilterPolicy::scopePathPassMatches(qualified, true, true));
  EXPECT_FALSE(FloodFilterPolicy::scopePathPassMatches(
      qualified, true, false));
  EXPECT_FALSE(FloodFilterPolicy::scopePathPassMatches(
      qualified, false, true));
}

TEST(FloodFilterScope, EveryBridgeBucketRoundTripsWithoutChangingSelector) {
  const uint8_t selector = 32;
  for (uint8_t bucket = 0;
       bucket < FloodFilterPolicy::SCOPE_PATH_BRIDGE_BUCKET_COUNT;
       bucket++) {
    uint8_t path_selector = (uint8_t)(
        FloodFilterPolicy::SCOPE_PATH_BRIDGE_BUCKET_BASE + bucket);
    uint8_t stored = FloodFilterPolicy::encodeScopeSelector(
        selector, true, path_selector);

    EXPECT_EQ(selector, FloodFilterPolicy::scopeSelectorValue(stored));
    EXPECT_EQ(path_selector,
              FloodFilterPolicy::scopePathSelectorValue(stored));
    EXPECT_EQ(bucket, FloodFilterPolicy::scopeBridgeBucketIndex(stored));
    EXPECT_TRUE(FloodFilterPolicy::scopeRequiresPath(stored));
    EXPECT_FALSE(FloodFilterPolicy::scopeRequiresBlacklistPath(stored));
    EXPECT_TRUE(FloodFilterPolicy::scopeUsesSlowTiming(stored));
  }
}

TEST(FloodFilterScope, DirectChannelTargetsRoundTripEveryMatcherClass) {
  const uint8_t selectors[] = {0, 1, 2, 16, 32};
  for (uint8_t selector : selectors) {
    uint8_t direct = FloodFilterPolicy::encodeChannelScopeTargetSelector(
        selector, true);
    uint8_t stored = FloodFilterPolicy::encodeScopeSelector(
        direct, true, FloodFilterPolicy::SCOPE_PATH_BRIDGE_BUCKET_BASE + 5);

    EXPECT_NE(FloodFilterPolicy::CHANNEL_SCOPE_INVALID_SELECTOR, direct);
    EXPECT_TRUE(FloodFilterPolicy::channelScopeUsesDirectTarget(stored));
    EXPECT_EQ(selector,
              FloodFilterPolicy::channelScopeMatchSelectorValue(stored));
    EXPECT_TRUE(FloodFilterPolicy::scopeUsesSlowTiming(stored));
    EXPECT_EQ(FloodFilterPolicy::SCOPE_PATH_BRIDGE_BUCKET_BASE + 5,
              FloodFilterPolicy::scopePathSelectorValue(stored));
  }
}

TEST(FloodFilterScope, RegionChannelTargetsKeepLegacySelectorValues) {
  const uint8_t selectors[] = {0, 1, 2, 16, 32};
  for (uint8_t selector : selectors) {
    uint8_t region = FloodFilterPolicy::encodeChannelScopeTargetSelector(
        selector, false);

    EXPECT_EQ(selector, region);
    EXPECT_FALSE(FloodFilterPolicy::channelScopeUsesDirectTarget(region));
    EXPECT_EQ(selector,
              FloodFilterPolicy::channelScopeMatchSelectorValue(region));
  }
}

TEST(FloodFilterScope, DirectChannelTargetRejectsUnknownMatcher) {
  EXPECT_EQ(FloodFilterPolicy::CHANNEL_SCOPE_INVALID_SELECTOR,
            FloodFilterPolicy::encodeChannelScopeTargetSelector(7, true));
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
