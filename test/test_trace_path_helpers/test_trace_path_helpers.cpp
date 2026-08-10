#include <gtest/gtest.h>

#include <helpers/TracePathHelpers.h>

TEST(TracePathHelpers, BuildsOneByteRoundTripThroughEndpoint) {
  const uint8_t saved[] = {0x11, 0x22};
  const uint8_t endpoint[] = {0x33};
  uint8_t route[8] = {0};
  mesh::RoundTripTracePath result;

  ASSERT_TRUE(mesh::buildRoundTripTracePath(
      saved, 2, endpoint, true, route, sizeof(route), result));

  const uint8_t expected[] = {0x11, 0x22, 0x33, 0x22, 0x11};
  EXPECT_EQ(1, result.hash_size);
  EXPECT_EQ(5, result.hop_count);
  EXPECT_EQ(sizeof(expected), result.byte_len);
  EXPECT_EQ(0, memcmp(expected, route, sizeof(expected)));
  EXPECT_EQ(0, mesh::traceFlagsForHashSize(result.hash_size));
}

TEST(TracePathHelpers, OmitsNonForwardingEndpoint) {
  const uint8_t saved[] = {0x11, 0x22};
  uint8_t route[8] = {0};
  mesh::RoundTripTracePath result;

  ASSERT_TRUE(mesh::buildRoundTripTracePath(
      saved, 2, nullptr, false, route, sizeof(route), result));

  const uint8_t expected[] = {0x11, 0x22, 0x22, 0x11};
  EXPECT_EQ(4, result.hop_count);
  EXPECT_EQ(sizeof(expected), result.byte_len);
  EXPECT_EQ(0, memcmp(expected, route, sizeof(expected)));
}

TEST(TracePathHelpers, PreservesTwoByteHashes) {
  const uint8_t saved[] = {0x11, 0x12, 0x21, 0x22};
  const uint8_t endpoint[] = {0x31, 0x32};
  uint8_t route[16] = {0};
  mesh::RoundTripTracePath result;

  ASSERT_TRUE(mesh::buildRoundTripTracePath(
      saved, static_cast<uint8_t>((1 << 6) | 2), endpoint, true,
      route, sizeof(route), result));

  const uint8_t expected[] = {
      0x11, 0x12, 0x21, 0x22, 0x31, 0x32, 0x21, 0x22, 0x11, 0x12};
  EXPECT_EQ(2, result.hash_size);
  EXPECT_EQ(5, result.hop_count);
  EXPECT_EQ(sizeof(expected), result.byte_len);
  EXPECT_EQ(0, memcmp(expected, route, sizeof(expected)));
  EXPECT_EQ(1, mesh::traceFlagsForHashSize(result.hash_size));
}

TEST(TracePathHelpers, SafelyDownConvertsThreeByteHashes) {
  const uint8_t saved[] = {
      0x11, 0x12, 0x13, 0x21, 0x22, 0x23};
  const uint8_t endpoint[] = {0x31, 0x32, 0x33};
  uint8_t route[16] = {0};
  mesh::RoundTripTracePath result;

  ASSERT_TRUE(mesh::buildRoundTripTracePath(
      saved, static_cast<uint8_t>((2 << 6) | 2), endpoint, true,
      route, sizeof(route), result));

  const uint8_t expected[] = {
      0x11, 0x12, 0x21, 0x22, 0x31, 0x32, 0x21, 0x22, 0x11, 0x12};
  EXPECT_EQ(2, result.hash_size);
  EXPECT_EQ(5, result.hop_count);
  EXPECT_EQ(sizeof(expected), result.byte_len);
  EXPECT_EQ(0, memcmp(expected, route, sizeof(expected)));
}

TEST(TracePathHelpers, RejectsUnknownEmptyAndOversizedRoutes) {
  const uint8_t saved[] = {0x11, 0x22};
  const uint8_t endpoint[] = {0x33};
  uint8_t route[4] = {0};
  mesh::RoundTripTracePath result;

  EXPECT_FALSE(mesh::buildRoundTripTracePath(
      saved, 0xFF, endpoint, true, route, sizeof(route), result));
  EXPECT_FALSE(mesh::buildRoundTripTracePath(
      nullptr, 0, nullptr, false, route, sizeof(route), result));
  EXPECT_FALSE(mesh::buildRoundTripTracePath(
      saved, 2, endpoint, true, route, sizeof(route), result));
}

TEST(TracePathHelpers, ParsesOneBytePrefixesSeparatedBySpaces) {
  uint8_t route[8] = {0};
  mesh::RawTracePath result;

  ASSERT_EQ(mesh::RawTracePathParseResult::Valid,
            mesh::parseRawTracePath("1 11 22 3a 22 11", route,
                                    sizeof(route), 63, result));

  const uint8_t expected[] = {0x11, 0x22, 0x3A, 0x22, 0x11};
  EXPECT_EQ(1, result.hash_size);
  EXPECT_EQ(5, result.hop_count);
  EXPECT_EQ(sizeof(expected), result.byte_len);
  EXPECT_EQ(0, memcmp(expected, route, sizeof(expected)));
}

TEST(TracePathHelpers, ParsesTwoBytePrefixesWithMixedSeparators) {
  uint8_t route[16] = {0};
  mesh::RawTracePath result;

  ASSERT_EQ(mesh::RawTracePathParseResult::Valid,
            mesh::parseRawTracePath("2 1122, 3344 ,AAbb,3344 1122",
                                    route, sizeof(route), 63, result));

  const uint8_t expected[] = {
      0x11, 0x22, 0x33, 0x44, 0xAA, 0xBB, 0x33, 0x44, 0x11, 0x22};
  EXPECT_EQ(2, result.hash_size);
  EXPECT_EQ(5, result.hop_count);
  EXPECT_EQ(sizeof(expected), result.byte_len);
  EXPECT_EQ(0, memcmp(expected, route, sizeof(expected)));
}

TEST(TracePathHelpers, ParsesFourBytePrefixesSeparatedByCommas) {
  uint8_t route[16] = {0};
  mesh::RawTracePath result;

  ASSERT_EQ(mesh::RawTracePathParseResult::Valid,
            mesh::parseRawTracePath("4,01234567,89abcdef,01234567,",
                                    route, sizeof(route), 63, result));

  const uint8_t expected[] = {
      0x01, 0x23, 0x45, 0x67, 0x89, 0xAB, 0xCD, 0xEF,
      0x01, 0x23, 0x45, 0x67};
  EXPECT_EQ(4, result.hash_size);
  EXPECT_EQ(3, result.hop_count);
  EXPECT_EQ(sizeof(expected), result.byte_len);
  EXPECT_EQ(0, memcmp(expected, route, sizeof(expected)));
  EXPECT_EQ(2, mesh::traceFlagsForHashSize(result.hash_size));
}

TEST(TracePathHelpers, RejectsThreeByteAndMalformedRawPaths) {
  uint8_t route[8] = {0};
  mesh::RawTracePath result;

  EXPECT_EQ(mesh::RawTracePathParseResult::InvalidHashSize,
            mesh::parseRawTracePath("3 112233", route, sizeof(route), 63,
                                    result));
  EXPECT_EQ(mesh::RawTracePathParseResult::MissingHashSize,
            mesh::parseRawTracePath("  ", route, sizeof(route), 63, result));
  EXPECT_EQ(mesh::RawTracePathParseResult::MissingPrefixes,
            mesh::parseRawTracePath("2 , ", route, sizeof(route), 63,
                                    result));
  EXPECT_EQ(mesh::RawTracePathParseResult::InvalidPrefix,
            mesh::parseRawTracePath("2 123 4567", route, sizeof(route), 63,
                                    result));
  EXPECT_EQ(mesh::RawTracePathParseResult::InvalidPrefix,
            mesh::parseRawTracePath("1 GG", route, sizeof(route), 63,
                                    result));
}

TEST(TracePathHelpers, EnforcesRawPathHopAndByteLimits) {
  uint8_t route[8] = {0};
  mesh::RawTracePath result;

  EXPECT_EQ(mesh::RawTracePathParseResult::TooManyHops,
            mesh::parseRawTracePath("1 11 22 33", route, sizeof(route), 2,
                                    result));
  EXPECT_EQ(mesh::RawTracePathParseResult::RouteTooLong,
            mesh::parseRawTracePath("4 11223344 55667788 99AABBCC", route,
                                    sizeof(route), 63, result));
  EXPECT_EQ(0xFF, mesh::traceFlagsForHashSize(3));
}

int main(int argc, char** argv) {
  ::testing::InitGoogleTest(&argc, argv);
  return RUN_ALL_TESTS();
}
