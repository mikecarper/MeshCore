#include <gtest/gtest.h>

#include <helpers/TempRadioLeaseDeadline.h>

TEST(TempRadioLeaseDeadline, EpochWindowGetsAnIndependentUptimeEnd) {
  const uint64_t hard_end = mesh::TempRadioLeaseDeadline::fromEpochEnd(
      50'000ULL, 1'800'000'000UL, 1'800'000'180UL);

  EXPECT_EQ(hard_end, 230'000ULL);
  EXPECT_FALSE(mesh::TempRadioLeaseDeadline::expired(229'999ULL, hard_end));
  EXPECT_TRUE(mesh::TempRadioLeaseDeadline::expired(230'000ULL, hard_end));
}

TEST(TempRadioLeaseDeadline, BackwardRtcCorrectionCannotExtendLease) {
  const uint64_t hard_end = mesh::TempRadioLeaseDeadline::fromEpochEnd(
      10'000ULL, 1'800'000'000UL, 1'800'000'180UL);

  // The wall clock has moved backward by an hour and no longer considers the
  // epoch end due.  Uptime remains authoritative for the safety bound.
  const uint32_t corrected_wall_clock = 1'799'996'580UL;
  EXPECT_LT(corrected_wall_clock, 1'800'000'180UL);
  EXPECT_TRUE(mesh::TempRadioLeaseDeadline::expired(190'000ULL, hard_end));
}

TEST(TempRadioLeaseDeadline, PendingWindowAlsoExpiresAndBoundsSleep) {
  const uint64_t hard_end = mesh::TempRadioLeaseDeadline::fromEpochEnd(
      4'000ULL, 1'800'000'000UL, 1'800'000'181UL);

  EXPECT_EQ(
      mesh::TempRadioLeaseDeadline::secondsUntil(180'001ULL, hard_end),
      5U);
  EXPECT_EQ(
      mesh::TempRadioLeaseDeadline::secondsUntil(185'000ULL, hard_end),
      0U);
}
