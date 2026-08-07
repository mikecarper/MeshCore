#include <gtest/gtest.h>

#include <helpers/LogicalMessageCache.h>

static void makeFingerprint(uint8_t fingerprint[MAX_HASH_SIZE], uint8_t value) {
  memset(fingerprint, value, MAX_HASH_SIZE);
}

TEST(LogicalMessageCache, MatchesFingerprintAndSourceTimestamp) {
  mesh::LogicalMessageCache<4> cache;
  uint8_t first[MAX_HASH_SIZE];
  uint8_t second[MAX_HASH_SIZE];
  makeFingerprint(first, 0x11);
  makeFingerprint(second, 0x22);

  ASSERT_TRUE(cache.remember(first, 100U, 500U));

  uint32_t mapped = 0;
  EXPECT_TRUE(cache.find(first, 100U, &mapped));
  EXPECT_EQ(500U, mapped);
  EXPECT_FALSE(cache.find(first, 101U));
  EXPECT_FALSE(cache.find(second, 100U));
}

TEST(LogicalMessageCache, RepeatedRememberUpdatesMappedTimestamp) {
  mesh::LogicalMessageCache<2> cache;
  uint8_t fingerprint[MAX_HASH_SIZE];
  makeFingerprint(fingerprint, 0x33);

  ASSERT_TRUE(cache.remember(fingerprint, 7U, 70U));
  ASSERT_TRUE(cache.remember(fingerprint, 7U, 71U));

  uint32_t mapped = 0;
  EXPECT_TRUE(cache.find(fingerprint, 7U, &mapped));
  EXPECT_EQ(71U, mapped);
}

TEST(LogicalMessageCache, RoundRobinReplacementIsBounded) {
  mesh::LogicalMessageCache<2> cache;
  uint8_t first[MAX_HASH_SIZE];
  uint8_t second[MAX_HASH_SIZE];
  uint8_t third[MAX_HASH_SIZE];
  makeFingerprint(first, 0x41);
  makeFingerprint(second, 0x42);
  makeFingerprint(third, 0x43);

  ASSERT_TRUE(cache.remember(first, 1U));
  ASSERT_TRUE(cache.remember(second, 2U));
  ASSERT_TRUE(cache.remember(third, 3U));

  EXPECT_FALSE(cache.find(first, 1U));
  EXPECT_TRUE(cache.find(second, 2U));
  EXPECT_TRUE(cache.find(third, 3U));
}

TEST(LogicalMessageCache, ClearForgetsAllEntries) {
  mesh::LogicalMessageCache<2> cache;
  uint8_t fingerprint[MAX_HASH_SIZE];
  makeFingerprint(fingerprint, 0x55);
  ASSERT_TRUE(cache.remember(fingerprint, 9U, 90U));

  cache.clear();

  EXPECT_FALSE(cache.find(fingerprint, 9U));
}

TEST(LogicalMessageCache, ExactOlderRetrySurvivesNewerMessages) {
  mesh::LogicalMessageCache<4> cache;
  uint8_t first[MAX_HASH_SIZE];
  uint8_t second[MAX_HASH_SIZE];
  makeFingerprint(first, 0x61);
  makeFingerprint(second, 0x62);
  uint32_t latest = 0;

  EXPECT_EQ(mesh::LogicalMessageCache<4>::ReplayDecision::NewMessage,
            cache.classifyAndRemember(first, 100U, latest));
  EXPECT_EQ(mesh::LogicalMessageCache<4>::ReplayDecision::NewMessage,
            cache.classifyAndRemember(second, 101U, latest));
  EXPECT_EQ(101U, latest);

  EXPECT_EQ(mesh::LogicalMessageCache<4>::ReplayDecision::ExactRetry,
            cache.classifyAndRemember(first, 100U, latest));
  EXPECT_EQ(101U, latest);
}

TEST(LogicalMessageCache, RejectsOldUnseenAndSameTimestampMismatches) {
  mesh::LogicalMessageCache<4> cache;
  uint8_t accepted[MAX_HASH_SIZE];
  uint8_t mismatched[MAX_HASH_SIZE];
  makeFingerprint(accepted, 0x71);
  makeFingerprint(mismatched, 0x72);
  uint32_t latest = 200U;

  EXPECT_EQ(mesh::LogicalMessageCache<4>::ReplayDecision::StaleOrMismatched,
            cache.classifyAndRemember(mismatched, 199U, latest));
  EXPECT_EQ(mesh::LogicalMessageCache<4>::ReplayDecision::StaleOrMismatched,
            cache.classifyAndRemember(mismatched, 200U, latest));
  EXPECT_EQ(mesh::LogicalMessageCache<4>::ReplayDecision::NewMessage,
            cache.classifyAndRemember(accepted, 201U, latest));
}

TEST(LogicalMessageCache, RejectsNullFingerprint) {
  mesh::LogicalMessageCache<2> cache;
  EXPECT_FALSE(cache.remember(NULL, 1U, 2U));
  EXPECT_FALSE(cache.find(NULL, 1U));
}

int main(int argc, char** argv) {
  ::testing::InitGoogleTest(&argc, argv);
  return RUN_ALL_TESTS();
}
