#include <gtest/gtest.h>

#include <helpers/AGCResetScheduler.h>
#include <cstring>

using mesh::AGCResetScheduler;

static_assert(sizeof(AGCResetScheduler) <= 12,
              "AGC scheduler must preserve the constrained Dispatcher layout");

namespace {

// Independent unpacked cadence model. No bit masks or shift-based interval
// reconstruction from the production representation are used here.
struct ReferenceScheduler {
  uint32_t base = 0, cadence = 0, deadline = 0, recovery = 0;
  unsigned attempts = 0;
  bool adaptive = false;

  void configure(uint32_t now, uint32_t requested, bool mode, uint32_t count) {
    if (!requested || requested > INT32_MAX) {
      *this = ReferenceScheduler();
      return;
    }
    if (base != requested || adaptive != mode || recovery != count) {
      base = cadence = requested;
      adaptive = mode;
      recovery = count;
      deadline = now + base;
      attempts = 0;
    }
  }
  void transmit(uint32_t now) {
    if (base && !adaptive) deadline = now + base;
  }
  void deferred(uint32_t now) { if (base) deadline = now + 50; }
  void attempted(uint32_t now) {
    if (!base) return;
    if (adaptive && ++attempts == 2) {
      attempts = 0;
      if (cadence < 60000) {
        cadence *= 2;
        if (cadence > 60000) cadence = 60000;
      }
    }
    deadline = now + cadence;
  }
  bool due(uint32_t now) const {
    return base && int32_t(now - deadline) >= 0;
  }
};

void expectEquivalent(const AGCResetScheduler& actual,
                      const ReferenceScheduler& expected, uint32_t now) {
  ASSERT_EQ(actual.interval(), expected.cadence);
  ASSERT_EQ(actual.nextAt(), expected.deadline);
  ASSERT_EQ(actual.due(now), expected.due(now));
  uint32_t delay = 123;
  ASSERT_EQ(actual.nextDelay(now, delay), expected.base != 0);
  ASSERT_EQ(delay, expected.base
      ? (expected.due(now) ? 0 : expected.deadline - now) : 123);
}

uint32_t nextRandom(uint32_t& value) {
  value ^= value << 13;
  value ^= value >> 17;
  return value ^= value << 5;
}

}  // namespace

TEST(AGCResetScheduler, DisabledAndInvalidIntervalsNeverSchedule) {
  AGCResetScheduler scheduler;
  uint32_t delay = 123;
  EXPECT_FALSE(scheduler.due(0));
  EXPECT_FALSE(scheduler.nextDelay(100, delay));
  EXPECT_EQ(delay, 123u);
  scheduler.noteTransmit(100);
  scheduler.noteAttempt(100);
  scheduler.noteDeferred(100);
  EXPECT_EQ(scheduler.interval(), 0u);
  for (uint32_t base : {0u, 0x80000000u, UINT32_MAX}) {
    scheduler.configure(1, 8000, true, 0);
    scheduler.configure(2, base, true, 0);
    EXPECT_FALSE(scheduler.due(500000));
    EXPECT_FALSE(scheduler.nextDelay(500000, delay));
    EXPECT_EQ(scheduler.nextAt(), 0u);
  }
}

TEST(AGCResetScheduler, IdenticalConfigurationDoesNotMoveDeadlineOrStage) {
  AGCResetScheduler scheduler;
  scheduler.configure(100, 8000, true, 7);
  for (uint32_t now = 100; now <= 8100; now += 100) {
    scheduler.configure(now, 8000, true, 7);
    EXPECT_EQ(scheduler.nextAt(), 8100u);
    EXPECT_EQ(scheduler.due(now), now == 8100);
  }
  scheduler.noteAttempt(8100);
  scheduler.configure(16099, 8000, true, 7);
  EXPECT_EQ(scheduler.nextAt(), 16100u);
  scheduler.noteAttempt(16100);
  EXPECT_EQ(scheduler.interval(), 16000u);
}

TEST(AGCResetScheduler, QuietAcceptedAttemptsBackOffEveryTwoWithoutHealthClaims) {
  AGCResetScheduler scheduler;
  scheduler.configure(0, 8000, true, 0);
  uint32_t now = 0;
  for (uint32_t interval : {8000u, 16000u, 16000u, 32000u,
                            32000u, 60000u, 60000u, 60000u}) {
    now = scheduler.nextAt();
    EXPECT_TRUE(scheduler.due(now));
    scheduler.noteAttempt(now);
    EXPECT_EQ(scheduler.interval(), interval);
    EXPECT_EQ(scheduler.nextAt(), now + interval);
    EXPECT_FALSE(scheduler.due(now));
  }
}

TEST(AGCResetScheduler, BaseAboveAdaptiveCeilingIsNeverShortened) {
  for (uint32_t base : {60000u, 61000u, 300000u, 1020000u,
                        uint32_t(INT32_MAX)}) {
    AGCResetScheduler scheduler;
    scheduler.configure(100, base, true, 0);
    for (unsigned attempt = 0; attempt < 20; ++attempt) {
      const uint32_t now = scheduler.nextAt();
      EXPECT_TRUE(scheduler.due(now));
      scheduler.noteAttempt(now);
      EXPECT_EQ(scheduler.interval(), base);
    }
  }
}

TEST(AGCResetScheduler, FixedTransmitPostponesButAdaptiveTransmitDoesNot) {
  for (bool adaptive : {false, true}) {
    AGCResetScheduler scheduler;
    scheduler.configure(100, 8000, adaptive, 0);
    scheduler.noteTransmit(8000);
    EXPECT_EQ(scheduler.nextAt(), adaptive ? 8100u : 16000u);
    EXPECT_EQ(scheduler.due(8100), adaptive);
    scheduler.noteAttempt(scheduler.nextAt());
    scheduler.noteAttempt(scheduler.nextAt());
    EXPECT_EQ(scheduler.interval(), adaptive ? 16000u : 8000u);
  }
}

TEST(AGCResetScheduler, BaseModeAndRecoveryChangesStartFreshIntervals) {
  AGCResetScheduler scheduler;
  scheduler.configure(0, 8000, true, 6);
  scheduler.noteAttempt(8000);
  scheduler.noteAttempt(16000);
  ASSERT_EQ(scheduler.interval(), 16000u);
  scheduler.configure(17000, 8000, true, 7);
  EXPECT_EQ(scheduler.interval(), 8000u);
  EXPECT_EQ(scheduler.nextAt(), 25000u);
  scheduler.noteAttempt(25000);
  EXPECT_EQ(scheduler.interval(), 8000u);
  scheduler.configure(26000, 16000, true, 7);
  EXPECT_EQ(scheduler.nextAt(), 42000u);
  scheduler.noteAttempt(42000);
  EXPECT_EQ(scheduler.interval(), 16000u);
  scheduler.configure(43000, 16000, false, 7);
  EXPECT_EQ(scheduler.nextAt(), 59000u);
  scheduler.configure(44000, 16000, true, 7);
  EXPECT_EQ(scheduler.nextAt(), 60000u);
  scheduler.configure(45000, 16000, true, UINT32_MAX);
  scheduler.configure(46000, 16000, true, 0);
  EXPECT_EQ(scheduler.nextAt(), 62000u);
}

TEST(AGCResetScheduler, DeferredAttemptsStayDueAndDoNotAdvanceStage) {
  AGCResetScheduler scheduler;
  scheduler.configure(0, 8000, true, 1);
  uint32_t delay = 0;
  ASSERT_TRUE(scheduler.nextDelay(7999, delay));
  EXPECT_EQ(delay, 1u);
  for (uint32_t now : {8000u, 8050u, 9000u, 100000u}) {
    scheduler.configure(now, 8000, true, 1);
    EXPECT_TRUE(scheduler.due(now));
    EXPECT_EQ(scheduler.nextAt(), 8000u);
    ASSERT_TRUE(scheduler.nextDelay(now, delay));
    EXPECT_EQ(delay, 0u);
  }
  scheduler.noteAttempt(100000);
  EXPECT_EQ(scheduler.interval(), 8000u);
  EXPECT_EQ(scheduler.nextAt(), 108000u);
  EXPECT_FALSE(scheduler.due(100000));
}

TEST(AGCResetScheduler, DeferredRetriesWaitFiftyMillisWithoutPromotingStage) {
  AGCResetScheduler scheduler;
  scheduler.configure(0, 8000, true, 1);
  scheduler.noteAttempt(8000);
  ASSERT_EQ(scheduler.nextAt(), 16000u);
  for (uint32_t now = 16000; now < 16500; now += 50) {
    ASSERT_TRUE(scheduler.due(now));
    scheduler.noteDeferred(now);
    EXPECT_EQ(scheduler.interval(), 8000u);
    EXPECT_FALSE(scheduler.due(now + 49));
    EXPECT_TRUE(scheduler.due(now + 50));
    uint32_t delay = 0;
    ASSERT_TRUE(scheduler.nextDelay(now + 49, delay));
    EXPECT_EQ(delay, 1u);
    scheduler.configure(now + 49, 8000, true, 1);
    EXPECT_EQ(scheduler.nextAt(), now + 50);
  }
  scheduler.noteAttempt(16500);
  EXPECT_EQ(scheduler.interval(), 16000u);
  EXPECT_EQ(scheduler.nextAt(), 32500u);

  scheduler.configure(UINT32_MAX - 10u, 32, false, 0);
  scheduler.noteDeferred(UINT32_MAX - 10u);
  EXPECT_EQ(scheduler.nextAt(), 39u);
  EXPECT_FALSE(scheduler.due(38));
  EXPECT_TRUE(scheduler.due(39));
}

TEST(AGCResetScheduler, ReceiveRetryDeferralPreservesStageAndValidatesDelay) {
  for (uint32_t origin : {100u, UINT32_MAX - 1999u}) {
    AGCResetScheduler scheduler;
    scheduler.configure(origin, 8000, true, 0);
    scheduler.noteAttempt(origin + 8000u); // one admitted attempt, no promotion yet
    scheduler.noteDeferred(origin, 2000);
    EXPECT_EQ(scheduler.interval(), 8000u);
    EXPECT_EQ(scheduler.nextAt(), origin + 2000u);
    EXPECT_FALSE(scheduler.due(origin + 1999u));
    EXPECT_TRUE(scheduler.due(origin + 2000u));
    scheduler.noteAttempt(origin + 2000u);
    EXPECT_EQ(scheduler.interval(), 16000u); // deferral retained the first attempt
  }
  for (uint32_t requested : {0u, uint32_t(INT32_MAX) + 1u, UINT32_MAX}) {
    AGCResetScheduler scheduler;
    scheduler.configure(0, 8000, true, 0);
    scheduler.noteDeferred(UINT32_MAX - 10u, requested);
    EXPECT_EQ(scheduler.nextAt(), 39u);
    EXPECT_FALSE(scheduler.due(38));
    EXPECT_TRUE(scheduler.due(39));
  }
  AGCResetScheduler scheduler;
  scheduler.configure(0, 8000, true, 0);
  scheduler.noteDeferred(100, INT32_MAX);
  EXPECT_FALSE(scheduler.due(100 + uint32_t(INT32_MAX) - 1));
  EXPECT_TRUE(scheduler.due(100 + uint32_t(INT32_MAX)));
  scheduler.reset(); scheduler.noteDeferred(100, 2000);
  uint32_t ignored = 0; EXPECT_FALSE(scheduler.nextDelay(100, ignored));
}

TEST(AGCResetScheduler, LateServiceDoesNotReplayMissedResetSlots) {
  AGCResetScheduler scheduler;
  scheduler.configure(10, 8000, true, 0);
  const uint32_t late = 7u * 24u * 60u * 60u * 1000u;
  ASSERT_TRUE(scheduler.due(late));
  scheduler.noteAttempt(late);
  EXPECT_EQ(scheduler.interval(), 8000u);
  EXPECT_EQ(scheduler.nextAt(), late + 8000u);
  EXPECT_FALSE(scheduler.due(late));
  scheduler.noteAttempt(late + 8000u);
  EXPECT_EQ(scheduler.interval(), 16000u);
}

TEST(AGCResetScheduler, DeadlinesAndDelaySurviveMillisRolloverIncludingZero) {
  AGCResetScheduler scheduler;
  scheduler.configure(0xfffffff0u, 32, true, 0);
  EXPECT_EQ(scheduler.nextAt(), 16u);
  uint32_t delay = 0;
  ASSERT_TRUE(scheduler.nextDelay(0xfffffff0u, delay));
  EXPECT_EQ(delay, 32u);
  EXPECT_FALSE(scheduler.due(15));
  EXPECT_TRUE(scheduler.due(16));
  scheduler.noteAttempt(16);
  EXPECT_EQ(scheduler.nextAt(), 48u);
  scheduler.configure(UINT32_MAX - 31u, 32, false, 0);
  EXPECT_EQ(scheduler.nextAt(), 0u);
  EXPECT_FALSE(scheduler.due(UINT32_MAX));
  EXPECT_TRUE(scheduler.due(0));
  ASSERT_TRUE(scheduler.nextDelay(UINT32_MAX, delay));
  EXPECT_EQ(delay, 1u);
  scheduler.reset();
  EXPECT_FALSE(scheduler.due(0));
  EXPECT_FALSE(scheduler.nextDelay(0, delay));
  scheduler.configure(100, 32, false, 0);
  EXPECT_EQ(scheduler.nextAt(), 132u);
}

TEST(AGCResetScheduler, AdaptiveRepresentationDoesNotBleedStageOrStreakBits) {
  for (uint32_t base : {1u, 2u, 3u, 31u, 32u, 32767u, 32768u,
                        59999u, 60000u, 60001u, 1020000u}) {
    AGCResetScheduler actual;
    ReferenceScheduler expected;
    actual.configure(UINT32_MAX - 123u, base, true, 42);
    expected.configure(UINT32_MAX - 123u, base, true, 42);
    for (unsigned attempt = 0; attempt < 80; ++attempt) {
      const uint32_t now = actual.nextAt();
      actual.noteAttempt(now);
      expected.attempted(now);
      expectEquivalent(actual, expected, now);
      // This implementation-only assertion checks the compact non-persisted
      // word: no stage overflow into streak/unused bits, no base corruption.
      uint32_t words[3];
      std::memcpy(words, &actual, sizeof(actual));
      EXPECT_EQ(words[2] & 0x7c000000u, 0u);
      EXPECT_EQ(words[2] & 0xfffffu, base);
      EXPECT_LE((words[2] >> 20) & 31u, 16u);
      EXPECT_NE(words[2] & 0x80000000u, 0u);
    }
  }
}

TEST(AGCResetScheduler, LargerExternalIntervalsRetainExactFixedCadence) {
  for (uint32_t base : {1020001u, 0x100000u, 0x2000000u, 0x4000000u,
                        uint32_t(INT32_MAX)}) {
    for (bool request_adaptive : {false, true}) {
      AGCResetScheduler actual;
      ReferenceScheduler expected;
      actual.configure(UINT32_MAX - 44u, base, request_adaptive, 7);
      expected.configure(UINT32_MAX - 44u, base, false, 7);
      for (uint32_t now : {1u, 100u, UINT32_MAX - 1u}) {
        actual.noteTransmit(now);
        expected.transmit(now);
        expectEquivalent(actual, expected, now);
        actual.noteAttempt(now);
        expected.attempted(now);
        expectEquivalent(actual, expected, now);
        actual.configure(now, base, request_adaptive, 7);
        expectEquivalent(actual, expected, now);
      }
    }
  }
}

TEST(AGCResetScheduler, DeterministicDifferentialAcrossSupportedBoundaries) {
  uint32_t random = 0x69ab1234;
  const uint32_t boundaries[] = {1, 2, 3, 31, 32, 1000, 3999, 4000, 8000,
      28000, 32767, 32768, 59999, 60000, 60001, 65535, 65536, 1019999, 1020000};
  for (unsigned script = 0; script < 80; ++script) {
    uint32_t base = script < sizeof(boundaries) / sizeof(boundaries[0])
        ? boundaries[script] : nextRandom(random) % 1020000 + 1;
    bool adaptive = script % 2;
    uint32_t recovery = UINT32_MAX;
    uint32_t now = UINT32_MAX - 333;
    AGCResetScheduler actual;
    ReferenceScheduler expected;
    actual.configure(now, base, adaptive, recovery);
    expected.configure(now, base, adaptive, recovery);
    for (unsigned operation = 0; operation < 200; ++operation) {
      now += nextRandom(random) % 5000;
      switch (nextRandom(random) % 7) {
        case 0:
          ++recovery;
          actual.configure(now, base, adaptive, recovery);
          expected.configure(now, base, adaptive, recovery);
          break;
        case 1: actual.noteTransmit(now); expected.transmit(now); break;
        case 2: actual.noteDeferred(now); expected.deferred(now); break;
        case 3: actual.noteAttempt(now); expected.attempted(now); break;
        case 4:
          now = actual.nextAt();
          actual.noteAttempt(now); expected.attempted(now);
          break;
        case 5:
          adaptive = !adaptive;
          actual.configure(now, base, adaptive, recovery);
          expected.configure(now, base, adaptive, recovery);
          break;
        case 6:
          actual.configure(now, base, adaptive, recovery);
          expected.configure(now, base, adaptive, recovery);
          break;
      }
      expectEquivalent(actual, expected, now);
    }
  }
}

int main(int argc, char** argv) {
  ::testing::InitGoogleTest(&argc, argv);
  return RUN_ALL_TESTS();
}
