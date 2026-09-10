#include <gtest/gtest.h>
#include <helpers/FloodAdvertLimiter.h>
#include <helpers/FloodAdvertCLI.h>
#include <array>

using Limiter = mesh::FloodAdvertLimiter;
using Decision = Limiter::Decision;
static constexpr uint32_t W = Limiter::WINDOW_MS;
static constexpr uint32_t H12 = Limiter::BAD_INTERVAL_MS;
static constexpr uint32_t D7 = Limiter::RECOVERY_MS;

class FloodAdvertLimit : public ::testing::Test {
protected:
  mesh::StaticFloodAdvertLimiter<4> limiter;
  std::array<uint8_t, PUB_KEY_SIZE> key{{0xBA, 0xDB, 0xEE, 0x5C}};

  Decision receive(unsigned seq, uint8_t hops, uint32_t now,
                   const uint8_t* source = nullptr, bool forward = true) {
    uint8_t hash[MAX_HASH_SIZE] = {};
    memcpy(hash, &seq, sizeof(seq));
    if (!source) source = key.data();
    limiter.observe(source, hash, hops, now);
    Decision result = limiter.check(source, hash, now);
    if (forward && result == Decision::Allow) limiter.commit(source, hash, now);
    return result;
  }
  void burst(uint32_t now, uint8_t hops = 8, unsigned count = 3) {
    for (unsigned i = 0; i < count; ++i) receive(i, hops, now);
  }
  void bad(uint32_t start = 0) {
    burst(start);
    EXPECT_FALSE(limiter.isBad(key.data(), start));
    burst(start + W);
    ASSERT_TRUE(limiter.isBad(key.data(), start + W));
  }
};

TEST_F(FloodAdvertLimit, EveryHopAllowanceAndSaturation) {
  const uint8_t expected[] = {10, 10, 9, 8, 6, 5, 4, 3, 2, 2, 2};
  for (uint8_t hops = 0; hops < sizeof(expected); ++hops) {
    limiter.reset();
    EXPECT_EQ(expected[hops], Limiter::allowance(hops));
    for (unsigned i = 0; i < expected[hops]; ++i) EXPECT_EQ(Decision::Allow, receive(i, hops, 0));
    EXPECT_EQ(Decision::Quota, receive(expected[hops], hops, 0));
  }
  EXPECT_EQ(2, Limiter::allowance(63));
  EXPECT_EQ(144U, sizeof(Limiter::Entry));
}

TEST_F(FloodAdvertLimit, PrefixIsSixBytesAndCollisionsShareQuota) {
  auto collision = key;
  collision[31] = 1;
  EXPECT_EQ(Decision::Allow, receive(1, 8, 0));
  EXPECT_EQ(Decision::Allow, receive(2, 8, 100, collision.data()));
  EXPECT_EQ(Decision::Quota, receive(3, 8, 200, collision.data()));
  auto separate = key;
  separate[5] ^= 1;
  EXPECT_EQ(Decision::Allow, receive(4, 8, 200, separate.data()));
  EXPECT_EQ(Decision::Allow, receive(5, 8, W, collision.data()));
}

TEST_F(FloodAdvertLimit, ShortestPathRaisesQuotaWithoutResettingCountOrEpoch) {
  EXPECT_EQ(Decision::Allow, receive(1, 8, 0));
  EXPECT_EQ(Decision::Allow, receive(2, 9, 1));
  EXPECT_EQ(Decision::Quota, receive(3, 10, 2));
  uint8_t hash[MAX_HASH_SIZE] = {1};
  ASSERT_TRUE(limiter.needsShorterPath(key.data(), 1, 10));
  limiter.observe(key.data(), hash, 1, 10, true);
  EXPECT_EQ(Decision::Duplicate, limiter.check(key.data(), hash, 10));
  for (unsigned i = 4; i < 12; ++i) EXPECT_EQ(Decision::Allow, receive(i, 8, 10));
  EXPECT_EQ(Decision::Quota, receive(12, 8, W - 1));
  EXPECT_EQ(Decision::Allow, receive(13, 8, W));
  EXPECT_FALSE(limiter.isBad(key.data(), W));
}

TEST_F(FloodAdvertLimit, DuplicateCopiesDoNotConsumeQuotaOrBecomeAbuse) {
  for (uint32_t window = 0; window < 10; ++window) {
    for (int copy = 0; copy < 300; ++copy) {
      Decision result = receive(1, 8, window * W);
      EXPECT_EQ(copy == 0 ? Decision::Allow : Decision::Duplicate, result);
    }
    EXPECT_FALSE(limiter.isBad(key.data(), window * W));
    EXPECT_EQ(Decision::Allow, receive(2, 8, window * W));
  }
}

TEST_F(FloodAdvertLimit, AtTheLimitIsNotAbuseAndSingleBurstDoesNotEscalate) {
  for (unsigned window = 0; window < 5; ++window) {
    burst(window * W, 8, 2);
    EXPECT_FALSE(limiter.isBad(key.data(), window * W));
  }
  limiter.reset();
  burst(0);
  burst(2 * W); // a whole quiet window interrupts continuing abuse
  EXPECT_FALSE(limiter.isBad(key.data(), 2 * W));
}

TEST_F(FloodAdvertLimit, BadListIsFullKeyNotPrefixAndUsesTwelveHourSpacing) {
  bad();
  auto collision = key;
  collision[31] = 1;
  EXPECT_FALSE(limiter.isBad(collision.data(), W));
  EXPECT_EQ(Decision::BadList, receive(4, 8, 2 * W));
  EXPECT_EQ(Decision::Allow, receive(1, 8, 2 * W, collision.data()));
  EXPECT_EQ(Decision::BadList, receive(5, 8, W + H12 - 1));
  EXPECT_EQ(Decision::Allow, receive(6, 8, W + H12));
  EXPECT_EQ(Decision::BadList, receive(7, 8, W + H12 + 1));
}

TEST_F(FloodAdvertLimit, AggregatePrefixExcessDoesNotBlameAnInnocentFullKey) {
  auto collision = key;
  collision[31] = 1;
  for (unsigned window = 0; window < 3; ++window) {
    burst(window * W, 8, 2);
    receive(1, 8, window * W, collision.data());
    receive(2, 8, window * W, collision.data());
    EXPECT_FALSE(limiter.isBad(key.data(), window * W));
    EXPECT_FALSE(limiter.isBad(collision.data(), window * W));
  }
}

TEST_F(FloodAdvertLimit, ShortestPrefixPathAlsoDefinesTheNormalRecoveryLevel) {
  auto collision = key;
  collision[31] = 1;
  for (unsigned window = 0; window < 3; ++window) {
    receive(1, 1, window * W, collision.data());
    burst(window * W, 8, 3);
    EXPECT_FALSE(limiter.isBad(key.data(), window * W));
  }
}

TEST_F(FloodAdvertLimit, RecoveryRequiresSevenDaysBelowNormalNotBelowPunitiveLimit) {
  bad();
  // Two received adverts each three hours is within the normal 8-hop budget,
  // even though many cannot be forwarded under the punitive 12-hour limit.
  for (uint32_t now = 2 * W; now < 2 * W + D7; now += W) {
    EXPECT_TRUE(limiter.isBad(key.data(), now));
    burst(now, 8, 2);
  }
  EXPECT_TRUE(limiter.isBad(key.data(), 2 * W + D7 - 1));
  EXPECT_FALSE(limiter.isBad(key.data(), 2 * W + D7));
  EXPECT_EQ(Decision::Allow, receive(50, 8, 2 * W + D7));
}

TEST_F(FloodAdvertLimit, OngoingSuppressedAbuseExtendsRecoveryIndefinitely) {
  bad();
  // More than one millis rollover and many seven-day periods of continuous abuse.
  for (uint64_t elapsed = 2ULL * W; elapsed < 80ULL * 24 * 60 * 60 * 1000; elapsed += W) {
    uint32_t now = uint32_t(elapsed);
    burst(now);
    EXPECT_TRUE(limiter.isBad(key.data(), now));
  }
}

TEST_F(FloodAdvertLimit, RelapseOnDaySixRestartsEntireRecoveryPeriod) {
  bad();
  const uint32_t relapse = 2 * W + D7 - 8 * W;
  burst(relapse);
  EXPECT_TRUE(limiter.isBad(key.data(), 2 * W + D7));
  EXPECT_TRUE(limiter.isBad(key.data(), relapse + W + D7 - 1));
  EXPECT_FALSE(limiter.isBad(key.data(), relapse + W + D7));
}

TEST_F(FloodAdvertLimit, QuietRecoveryAndInitialWindowWorkAcrossMillisWrap) {
  const uint32_t start = UINT32_MAX - W / 2;
  bad(start);
  EXPECT_TRUE(limiter.isBad(key.data(), start + 2 * W + D7 - 1));
  EXPECT_FALSE(limiter.isBad(key.data(), start + 2 * W + D7));
}

TEST_F(FloodAdvertLimit, FullTableEvictsOldestNormalSourceButNeverBadList) {
  bad();
  for (unsigned i = 1; i <= 3; ++i) {
    auto source = key;
    source[0] += i;
    EXPECT_EQ(Decision::Allow, receive(1, 8, W + i, source.data()));
  }
  auto new_source = key;
  new_source[0] += 4;
  EXPECT_EQ(Decision::Allow, receive(1, 8, W + 4, new_source.data()));
  EXPECT_TRUE(limiter.isBad(key.data(), W + 4));
  uint8_t hash[MAX_HASH_SIZE] = {1};
  auto oldest = key;
  oldest[0] += 1;
  EXPECT_EQ(Decision::Capacity, limiter.check(oldest.data(), hash, W + 4));
  EXPECT_EQ(Decision::Duplicate, receive(1, 8, 2 * W + 3, new_source.data()));
  EXPECT_EQ(Decision::Allow, receive(1, 8, 2 * W + 4, new_source.data()));
  EXPECT_TRUE(limiter.isBad(key.data(), 2 * W + 4));
}

TEST_F(FloodAdvertLimit, KnownDuplicateUpdatesLastHeardAndUnknownHashCannot) {
  for (unsigned i = 0; i < 4; ++i) {
    auto source = key;
    source[0] += i;
    receive(1, 8, i, source.data());
  }
  uint8_t hash[MAX_HASH_SIZE] = {1};
  limiter.noteKnownCopy(key.data(), hash, 10); // oldest becomes most recently heard
  auto second = key;
  second[0] += 1;
  uint8_t forged_hash[MAX_HASH_SIZE] = {9};
  limiter.noteKnownCopy(second.data(), forged_hash, 11);
  auto new_source = key;
  new_source[0] += 4;
  EXPECT_EQ(Decision::Allow, receive(1, 8, 12, new_source.data()));
  EXPECT_EQ(Decision::Duplicate, limiter.check(key.data(), hash, 12));
  EXPECT_EQ(Decision::Capacity, limiter.check(second.data(), hash, 12));
}

TEST_F(FloodAdvertLimit, CapacityCannotEvictFirstStrikeEvidence) {
  for (unsigned i = 0; i < 4; ++i) {
    auto source = key;
    source[0] += i;
    for (unsigned seq = 1; seq <= 3; ++seq) receive(seq, 8, 0, source.data());
  }
  auto new_source = key;
  new_source[0] += 4;
  EXPECT_EQ(Decision::Capacity, receive(1, 8, 1, new_source.data()));
  EXPECT_EQ(Decision::Capacity, receive(1, 8, W, new_source.data()));
  EXPECT_EQ(Decision::Allow, receive(1, 8, 2 * W, new_source.data()));
}

TEST_F(FloodAdvertLimit, LastHeardEvictionWorksAcrossMillisWrap) {
  for (unsigned i = 0; i < 4; ++i) {
    auto source = key;
    source[0] += i;
    receive(1, 8, UINT32_MAX - 2 + i, source.data());
  }
  auto new_source = key;
  new_source[0] += 4;
  EXPECT_EQ(Decision::Allow, receive(1, 8, 2, new_source.data()));
  uint8_t hash[MAX_HASH_SIZE] = {1};
  EXPECT_EQ(Decision::Capacity, limiter.check(key.data(), hash, 2));
}

TEST_F(FloodAdvertLimit, RejectedForwardDoesNotSpendForwardingQuota) {
  EXPECT_EQ(Decision::Allow, receive(1, 8, 0, nullptr, false));
  EXPECT_EQ(Decision::Allow, receive(2, 8, 0));
  EXPECT_EQ(Decision::Allow, receive(3, 8, 0));
  EXPECT_EQ(Decision::Quota, receive(4, 8, 0));
}

TEST_F(FloodAdvertLimit, PeriodicMaintenanceExpiresIdleHistoryBeforeTimerWrap) {
  bad();
  for (uint64_t now = 2ULL * W; now < uint64_t(UINT32_MAX) + W; now += W) limiter.tick(uint32_t(now));
  EXPECT_FALSE(limiter.isBad(key.data(), W));
  EXPECT_EQ(Decision::Allow, receive(5, 8, W));
}

TEST_F(FloodAdvertLimit, CliExactKeyClearDoesNotClearCollidingFullKey) {
  auto collision = key;
  collision[31] = 1;
  for (unsigned window = 0; window < 2; ++window) {
    burst(window * W);
    for (unsigned seq = 0; seq < 3; ++seq) receive(seq, 8, window * W, collision.data());
  }
  ASSERT_TRUE(limiter.isBad(key.data(), W));
  ASSERT_TRUE(limiter.isBad(collision.data(), W));
  char hex[PUB_KEY_SIZE * 2 + 1];
  mesh::Utils::toHex(hex, key.data(), PUB_KEY_SIZE);
  std::string command = std::string("clear flood.advert ") + hex;
  char reply[160] = {};
  ASSERT_TRUE(mesh::cli::handleFloodAdvertClear(&limiter, command.c_str(), reply));
  EXPECT_STREQ("OK - key advert limit history cleared", reply);
  EXPECT_FALSE(limiter.isBad(key.data(), W));
  EXPECT_TRUE(limiter.isBad(collision.data(), W));
  // Clearing an already cleared key is idempotent for LoRa retries.
  EXPECT_TRUE(mesh::cli::handleFloodAdvertClear(&limiter, command.c_str(), reply));
  EXPECT_STREQ("OK - key advert limit history cleared", reply);
}

TEST_F(FloodAdvertLimit, CliAllResetAndRebootClearEveryKindOfHistory) {
  bad();
  char reply[160] = {};
  EXPECT_TRUE(mesh::cli::handleFloodAdvertClear(&limiter, "clear flood.advert all \r\n", reply));
  EXPECT_STREQ("OK - all advert limit history cleared", reply);
  EXPECT_FALSE(limiter.isBad(key.data(), W));
  EXPECT_EQ(Decision::Allow, receive(4, 8, W));
  EXPECT_EQ(Decision::Allow, receive(5, 8, W));
  limiter.reset(); // Mesh::begin() uses the same reset on reboot.
  bad();
  limiter.reset();
  EXPECT_FALSE(limiter.isBad(key.data(), 0));
}

TEST_F(FloodAdvertLimit, CliInvalidSelectorsNeverClearAnything) {
  bad();
  const char* invalid[] = {
    "clear flood.advert", "clear flood.advert ", "clear flood.advert BA",
    "clear flood.advert BADBEE5C0000", "clear flood.advert all extra",
    "clear flood.advert 000000000000000000000000000000000000000000000000000000000000000Z",
    "clear flood.advert 00000000000000000000000000000000000000000000000000000000000000000"
  };
  for (auto* command : invalid) {
    char reply[160] = {};
    EXPECT_TRUE(mesh::cli::handleFloodAdvertClear(&limiter, command, reply));
    EXPECT_EQ(0, strncmp(reply, "ERR:", 4));
    EXPECT_TRUE(limiter.isBad(key.data(), W));
  }
  char reply[160] = {};
  EXPECT_FALSE(mesh::cli::handleFloodAdvertClear(&limiter, "clear flood.advertisement all", reply));
  EXPECT_FALSE(mesh::cli::handleFloodAdvertClear(&limiter, "clear stats", reply));
  EXPECT_TRUE(mesh::cli::handleFloodAdvertClear(nullptr, "clear flood.advert all", reply));
  EXPECT_STREQ("ERR: advert limiter unavailable on this role", reply);
}

#ifndef FLOOD_ADVERT_COMBINED_TEST
int main(int argc, char** argv) {
  ::testing::InitGoogleTest(&argc, argv);
  return RUN_ALL_TESTS();
}
#endif
