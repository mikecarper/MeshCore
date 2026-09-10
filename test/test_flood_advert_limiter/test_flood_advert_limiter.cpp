#include <gtest/gtest.h>
#include <helpers/FloodAdvertLimiter.h>
#include <helpers/FloodAdvertCLI.h>
#include <helpers/RemoteCliReplyCache.h>
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

TEST_F(FloodAdvertLimit, CliListsOnlyLimitedKeysAndReturnsFullKeyDetails) {
  char reply[160] = {};
  ASSERT_TRUE(mesh::cli::handleFloodAdvertGet(&limiter, "get flood.advert", reply, 0));
  EXPECT_STREQ("> no rate-limited adverts", reply);
  receive(1, 8, 0);
  ASSERT_TRUE(mesh::cli::handleFloodAdvertGet(&limiter, "get flood.advert", reply, 0));
  EXPECT_STREQ("> no rate-limited adverts", reply);
  receive(2, 8, 0);
  ASSERT_TRUE(mesh::cli::handleFloodAdvertGet(&limiter, "get flood.advert 1", reply, 1000));
  EXPECT_STREQ("> page 1/1 limited=1\n1 BADBEE5C0000 quota wait=10799s", reply);
  ASSERT_TRUE(mesh::cli::handleFloodAdvertGet(&limiter, "get flood.advert key 1", reply, 1000));
  char hex[PUB_KEY_SIZE * 2 + 1];
  mesh::Utils::toHex(hex, key.data(), PUB_KEY_SIZE);
  EXPECT_EQ(std::string("> ") + hex + "\nquota wait=10799s sent=2/2 hops=8 recovery=0s", reply);
  // Reading neither spends more quota nor resets it.
  EXPECT_EQ(Decision::Quota, receive(3, 8, 1000));
  ASSERT_TRUE(mesh::cli::handleFloodAdvertGet(&limiter, "get flood.advert", reply, W));
  EXPECT_STREQ("> no rate-limited adverts", reply);
}

TEST_F(FloodAdvertLimit, CliPagesTheWholeListWithoutTruncationOrChangingIt) {
  for (unsigned i = 0; i < 4; ++i) {
    auto source = key;
    source[0] += i;
    receive(1, 8, 0, source.data());
    receive(2, 8, 0, source.data());
  }
  struct { char before = '!'; char reply[160] = {}; char after = '?'; } guarded;
  ASSERT_TRUE(mesh::cli::handleFloodAdvertGet(&limiter, "get flood.advert", guarded.reply, 0));
  EXPECT_STREQ("> page 1/2 limited=4\n1 BADBEE5C0000 quota wait=10800s\n2 BBDBEE5C0000 quota wait=10800s\n3 BCDBEE5C0000 quota wait=10800s", guarded.reply);
  ASSERT_TRUE(mesh::cli::handleFloodAdvertGet(&limiter, "get flood.advert\t2\r\n", guarded.reply, 1));
  EXPECT_STREQ("> page 2/2 limited=4\n4 BDDBEE5C0000 quota wait=10800s", guarded.reply);
  EXPECT_EQ('!', guarded.before);
  EXPECT_EQ('?', guarded.after);
  ASSERT_TRUE(mesh::cli::handleFloodAdvertGet(&limiter, "get flood.advert 3", guarded.reply, 1));
  EXPECT_STREQ("ERR: advert list index out of range", guarded.reply);
  ASSERT_TRUE(mesh::cli::handleFloodAdvertGet(&limiter, "get flood.advert key 4", guarded.reply, 1));
  EXPECT_NE(nullptr, strstr(guarded.reply, "BDDBEE5C0000"));
  EXPECT_EQ(4U, limiter.listLimited(1, 0, nullptr, 0));
}

TEST_F(FloodAdvertLimit, ListedPrefixCollisionsPreserveFullKeyIdentity) {
  auto collision = key;
  collision[31] = 1;
  receive(1, 8, 0);
  receive(1, 8, 1, collision.data());
  Limiter::LimitedEntry rows[2];
  ASSERT_EQ(2U, limiter.listLimited(100, 0, rows, 2));
  EXPECT_EQ(0, memcmp(rows[0].key, rows[1].key, Limiter::PREFIX_BYTES));
  EXPECT_NE(0, memcmp(rows[0].key, rows[1].key, PUB_KEY_SIZE));
  for (const auto& row : rows) {
    EXPECT_EQ(Limiter::WindowQuota, row.reasons);
    EXPECT_EQ(2, row.forwarded);
    EXPECT_EQ(2, row.quota);
    EXPECT_EQ(W - 100, row.wait_ms);
  }
  char reply[160];
  ASSERT_TRUE(mesh::cli::handleFloodAdvertGet(&limiter, "get flood.advert key 2", reply, 100));
  char hex[PUB_KEY_SIZE * 2 + 1];
  mesh::Utils::toHex(hex, collision.data(), PUB_KEY_SIZE);
  EXPECT_NE(nullptr, strstr(reply, hex));
}

TEST_F(FloodAdvertLimit, LargestSupportedTableFitsUsbAndRemoteReplies) {
  mesh::StaticFloodAdvertLimiter<128> large;
  for (unsigned i = 0; i < 128; ++i) {
    auto source = key;
    source[0] = i;
    for (uint8_t seq = 0; seq < Limiter::HASH_SLOTS; ++seq) {
      uint8_t hash[MAX_HASH_SIZE] = {seq};
      large.observe(source.data(), hash, 0, 0);
    }
  }
  struct { char before = '!'; char reply[160] = {}; char after = '?'; } guarded;
  ASSERT_TRUE(mesh::cli::handleFloodAdvertGet(&large, "get flood.advert 34", guarded.reply, 0));
  EXPECT_STREQ("> page 34/43 limited=128\n100 63DBEE5C0000 history wait=10800s\n101 64DBEE5C0000 history wait=10800s\n102 65DBEE5C0000 history wait=10800s", guarded.reply);
  EXPECT_LE(strlen(guarded.reply), mesh::RemoteCliReplyCache::MAX_REPLY_TEXT);
  ASSERT_TRUE(mesh::cli::handleFloodAdvertGet(&large, "get flood.advert key 128", guarded.reply, 0));
  EXPECT_NE(nullptr, strstr(guarded.reply, "history wait=10800s sent=0/10 hops=0 recovery=0s"));
  EXPECT_LE(strlen(guarded.reply), mesh::RemoteCliReplyCache::MAX_REPLY_TEXT);
  EXPECT_EQ('!', guarded.before);
  EXPECT_EQ('?', guarded.after);
}

TEST_F(FloodAdvertLimit, ListingUsesTheSameShortestPrefixPathAsForwarding) {
  receive(1, 8, 0);
  receive(2, 8, 1);
  ASSERT_EQ(1U, limiter.listLimited(1, 0, nullptr, 0));
  uint8_t hash[MAX_HASH_SIZE] = {1};
  limiter.observe(key.data(), hash, 1, 2, true);
  EXPECT_EQ(0U, limiter.listLimited(2, 0, nullptr, 0));
  EXPECT_EQ(Decision::Allow, receive(3, 8, 2));
}

TEST_F(FloodAdvertLimit, ListingShowsSaturatedReceiveHistoryWithoutSpentQuota) {
  for (unsigned i = 0; i < Limiter::HASH_SLOTS; ++i) receive(i, 1, 0, nullptr, false);
  Limiter::LimitedEntry row;
  ASSERT_EQ(1U, limiter.listLimited(0, 0, &row, 1));
  EXPECT_EQ(Limiter::ReceiveHistory, row.reasons);
  EXPECT_EQ(0, row.forwarded);
  EXPECT_EQ(10, row.quota);
  EXPECT_EQ(W, row.wait_ms);
  char reply[160];
  ASSERT_TRUE(mesh::cli::handleFloodAdvertGet(&limiter, "get flood.advert", reply, 0));
  EXPECT_NE(nullptr, strstr(reply, "history wait=10800s"));
  EXPECT_EQ(Decision::Quota, receive(99, 1, 0));
}

TEST_F(FloodAdvertLimit, ListingReportsBadKeyCooldownAndDoesNotExtendRecovery) {
  bad();
  Limiter::LimitedEntry row;
  ASSERT_EQ(1U, limiter.listLimited(W, 0, &row, 1));
  EXPECT_TRUE(row.reasons & Limiter::BadListRule);
  EXPECT_EQ(H12, row.wait_ms);
  EXPECT_EQ(D7, row.recovery_ms);
  char reply[160];
  ASSERT_TRUE(mesh::cli::handleFloodAdvertGet(&limiter, "get flood.advert", reply, W));
  EXPECT_NE(nullptr, strstr(reply, "bad wait=43200s"));
  // Bad-list membership is still visible when its next single forward is due.
  ASSERT_EQ(1U, limiter.listLimited(W + H12, 0, &row, 1));
  EXPECT_TRUE(row.reasons & Limiter::BadListRule);
  EXPECT_EQ(0U, row.wait_ms);
  EXPECT_EQ(D7 - (H12 - W), row.recovery_ms);
  for (uint32_t now = W + H12; now < 2 * W + D7; now += W) {
    EXPECT_EQ(1U, limiter.listLimited(now, 0, nullptr, 0));
  }
  EXPECT_EQ(0U, limiter.listLimited(2 * W + D7, 0, nullptr, 0));
  EXPECT_FALSE(limiter.isBad(key.data(), 2 * W + D7));
}

TEST_F(FloodAdvertLimit, ListingWaitsAndExpiryWorkAcrossMillisWrap) {
  const uint32_t start = UINT32_MAX - W / 2;
  bad(start);
  Limiter::LimitedEntry row;
  ASSERT_EQ(1U, limiter.listLimited(start + W + 1, 0, &row, 1));
  EXPECT_EQ(H12 - 1, row.wait_ms);
  EXPECT_EQ(D7 - 1, row.recovery_ms);
  EXPECT_EQ(0U, limiter.listLimited(start + 2 * W + D7, 0, &row, 1));
}

TEST_F(FloodAdvertLimit, CliInvalidListSelectorsDoNotChangeHistoryOrStealOtherCommands) {
  bad();
  const char* invalid[] = {
    "get flood.advert 0", "get flood.advert -1", "get flood.advert 65536",
    "get flood.advert 99999999999999999999", "get flood.advert 1.5",
    "get flood.advert all", "get flood.advert 1 extra", "get flood.advert key",
    "get flood.advert key 0", "get flood.advert key -1", "get flood.advert key 1 extra"
  };
  for (const char* command : invalid) {
    char reply[160] = {};
    ASSERT_TRUE(mesh::cli::handleFloodAdvertGet(&limiter, command, reply, W));
    EXPECT_EQ(0, strncmp(reply, "ERR:", 4)) << command;
    EXPECT_TRUE(limiter.isBad(key.data(), W));
  }
  char reply[160] = "unchanged";
  EXPECT_FALSE(mesh::cli::handleFloodAdvertGet(&limiter, "get flood.advert.interval", reply, W));
  EXPECT_FALSE(mesh::cli::handleFloodAdvertGet(&limiter, "get flood.advertisement", reply, W));
  EXPECT_FALSE(mesh::cli::handleFloodAdvertGet(&limiter, "clear flood.advert all", reply, W));
  EXPECT_STREQ("unchanged", reply);
  ASSERT_TRUE(mesh::cli::handleFloodAdvertGet(nullptr, "get flood.advert", reply, W));
  EXPECT_STREQ("ERR: advert limiter unavailable on this role", reply);
}

#ifndef FLOOD_ADVERT_COMBINED_TEST
int main(int argc, char** argv) {
  ::testing::InitGoogleTest(&argc, argv);
  return RUN_ALL_TESTS();
}
#endif
