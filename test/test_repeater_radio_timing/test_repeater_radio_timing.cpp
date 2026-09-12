#include <gtest/gtest.h>
#include <helpers/RepeaterRadioTiming.h>

using mesh::RepeaterRadioTiming;
using mesh::RxInactivityWatchdog;

constexpr uint32_t HOUR = RepeaterRadioTiming::HOUR_SECS;
constexpr uint32_t HOUR_MS = RepeaterRadioTiming::HOUR_MS;

TEST(RepeaterRadioTiming, NormalDefaultsTo24HoursWithSavedAdvertIntervals) {
  RepeaterRadioTiming timing;
  EXPECT_TRUE(RepeaterRadioTiming::DEFAULT_RX_WATCHDOG_ENABLED);
  EXPECT_EQ(timing.watchdogMillis(true), 24 * HOUR_MS);
  EXPECT_EQ(timing.watchdogMillis(false), 0U);
  EXPECT_EQ(timing.localAdvertMinutes(120), 120U);
  EXPECT_EQ(timing.floodAdvertHours(24), 24U);
}

TEST(RepeaterRadioTiming, ShortSessionsDisableWatchdogAndKeepNormalFloodInterval) {
  for (uint32_t seconds : {1U, HOUR, 3 * HOUR}) {
    RepeaterRadioTiming timing;
    timing.setTempDuration(seconds);
    EXPECT_EQ(timing.watchdogMillis(true), 0U);
    EXPECT_EQ(timing.localAdvertMinutes(0), 60U);
    EXPECT_EQ(timing.floodAdvertHours(24), 24U);
    EXPECT_EQ(timing.floodAdvertHours(0), 0U);
    EXPECT_STREQ(timing.watchdogLabel(true), "off (temp<12hours)");
  }
}

TEST(RepeaterRadioTiming, ThreeHourFloodOverrideUsesStrictGreaterThanBoundary) {
  RepeaterRadioTiming timing;
  timing.setTempDuration(3 * HOUR + 1);
  EXPECT_EQ(timing.floodAdvertHours(24), 3U);
  EXPECT_EQ(timing.floodAdvertHours(0), 3U);
  EXPECT_EQ(timing.watchdogMillis(true), 0U);
}

TEST(RepeaterRadioTiming, WatchdogUsesInclusive12HourBoundary) {
  RepeaterRadioTiming timing;
  timing.setTempDuration(12 * HOUR - 1);
  EXPECT_EQ(timing.watchdogMillis(true), 0U);
  timing.setTempDuration(12 * HOUR);
  EXPECT_EQ(timing.watchdogMillis(true), 12 * HOUR_MS);
  EXPECT_EQ(timing.watchdogMillis(false), 12 * HOUR_MS);
  timing.setTempDuration(48 * HOUR);
  EXPECT_EQ(timing.watchdogMillis(true), 12 * HOUR_MS);
  EXPECT_STREQ(timing.watchdogLabel(true), "12hours");
}

TEST(RepeaterRadioTiming, EndingOrRebootingClearsAllOverrides) {
  RepeaterRadioTiming timing;
  timing.setTempDuration(48 * HOUR);
  timing.setTempDuration(0);
  EXPECT_EQ(timing.watchdogMillis(true), 24 * HOUR_MS);
  EXPECT_EQ(timing.localAdvertMinutes(120), 120U);
  EXPECT_EQ(timing.floodAdvertHours(24), 24U);
  EXPECT_EQ(timing.floodAdvertHours(0), 0U);
  timing.setTempDuration(48 * HOUR);
  timing = RepeaterRadioTiming();
  EXPECT_FALSE(timing.isTemporary());
  EXPECT_EQ(timing.watchdogMillis(true), 24 * HOUR_MS);
  EXPECT_EQ(timing.localAdvertMinutes(0), 0U);
}

TEST(RepeaterRadioTiming, RepliesIncludeDaysHoursMinutesAndDisabledReason) {
  char text[160];
  RepeaterRadioTiming::formatDuration(text, sizeof(text), 90 * 60);
  EXPECT_STREQ(text, "90 mins (0d1h30m)");
  RepeaterRadioTiming::appendTempWatchdogNote(text, sizeof(text), 90 * 60);
  EXPECT_STREQ(text, "90 mins (0d1h30m); rx.watchdog=off (temp<12hours)");
  RepeaterRadioTiming::formatDuration(text, sizeof(text), 24 * HOUR);
  RepeaterRadioTiming::appendTempWatchdogNote(text, sizeof(text), 24 * HOUR);
  EXPECT_STREQ(text, "1440 mins (1d0h0m); rx.watchdog=12hours");
  RepeaterRadioTiming::formatDuration(text, sizeof(text), 3 * HOUR + 1);
  EXPECT_STREQ(text, "180 mins 1 sec (0d3h0m)");
  RepeaterRadioTiming::formatDuration(text, sizeof(text), 3075 * 60);
  EXPECT_STREQ(text, "3075 mins (2d3h15m)");
  RepeaterRadioTiming::formatDuration(text, sizeof(text), 60);
  EXPECT_STREQ(text, "1 mins (0d0h1m)");
}

TEST(RepeaterRadioTiming, ReplyFormattingBoundsOutputAndLargeDurations) {
  struct Buffer { char text[12]; char guard; } output = {{}, '!'};
  RepeaterRadioTiming::formatDuration(output.text, sizeof(output.text), UINT32_MAX);
  EXPECT_EQ(output.text[11], '\0');
  EXPECT_EQ(output.guard, '!');
  RepeaterRadioTiming::appendTempWatchdogNote(output.text, sizeof(output.text), 24 * HOUR);
  EXPECT_EQ(output.guard, '!');
  char large[160];
  RepeaterRadioTiming::formatDuration(large, sizeof(large), UINT32_MAX);
  EXPECT_STREQ(large, "71582788 mins 15 sec (49710d6h28m)");
}

TEST(RxInactivityWatchdog, RebootsAfter24HoursWithoutRxRatherThanOnPeriodicCheck) {
  RxInactivityWatchdog watchdog;
  EXPECT_FALSE(watchdog.expired(1000, 0, 24 * HOUR_MS));
  EXPECT_FALSE(watchdog.expired(24 * HOUR_MS + 999, 0, 24 * HOUR_MS));
  EXPECT_TRUE(watchdog.expired(24 * HOUR_MS + 1000, 0, 24 * HOUR_MS));
}

TEST(RxInactivityWatchdog, SuccessfulReceiveMovesDeadlineAndTransitionStartsFreshWindow) {
  RxInactivityWatchdog watchdog;
  watchdog.expired(1000, 500, 24 * HOUR_MS);
  EXPECT_FALSE(watchdog.expired(20 * HOUR_MS, 20 * HOUR_MS, 24 * HOUR_MS));
  EXPECT_FALSE(watchdog.expired(24 * HOUR_MS + 1000, 20 * HOUR_MS, 24 * HOUR_MS));
  EXPECT_TRUE(watchdog.expired(44 * HOUR_MS, 20 * HOUR_MS, 24 * HOUR_MS));
  EXPECT_FALSE(watchdog.expired(44 * HOUR_MS, 20 * HOUR_MS, 12 * HOUR_MS));
  EXPECT_TRUE(watchdog.expired(56 * HOUR_MS, 20 * HOUR_MS, 12 * HOUR_MS));
  EXPECT_FALSE(watchdog.expired(56 * HOUR_MS, 20 * HOUR_MS, 0));
  EXPECT_FALSE(watchdog.expired(80 * HOUR_MS, 20 * HOUR_MS, 24 * HOUR_MS));
}

TEST(RxInactivityWatchdog, SameLengthReplacementAndManualEnableRestartObservation) {
  RxInactivityWatchdog watchdog;
  watchdog.expired(100, 0, 12 * HOUR_MS);
  watchdog.reset();
  EXPECT_FALSE(watchdog.expired(11 * HOUR_MS, 0, 12 * HOUR_MS));
  EXPECT_FALSE(watchdog.expired(12 * HOUR_MS + 100, 0, 12 * HOUR_MS));
  EXPECT_TRUE(watchdog.expired(23 * HOUR_MS, 0, 12 * HOUR_MS));
}

TEST(RxInactivityWatchdog, ReceiveAndDeadlinesSurviveMillisRollover) {
  RxInactivityWatchdog watchdog;
  const uint32_t start = UINT32_MAX - HOUR_MS;
  EXPECT_FALSE(watchdog.expired(start, start - 1, 24 * HOUR_MS));
  EXPECT_FALSE(watchdog.expired(start + HOUR_MS + 1, 0, 24 * HOUR_MS));
  EXPECT_FALSE(watchdog.expired(24 * HOUR_MS - 1, 0, 24 * HOUR_MS));
  EXPECT_TRUE(watchdog.expired(24 * HOUR_MS, 0, 24 * HOUR_MS));
}

int main(int argc, char** argv) {
  ::testing::InitGoogleTest(&argc, argv);
  return RUN_ALL_TESTS();
}
