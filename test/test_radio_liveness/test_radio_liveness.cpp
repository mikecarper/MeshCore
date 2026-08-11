#include <gtest/gtest.h>

#include <helpers/RadioLivenessTracker.h>
#include <helpers/radiolib/RxBoostedGainDefaults.h>

using mesh::RadioLivenessTracker;
using mesh::RadioRecoveryAction;

TEST(RxBoostedGainDefaults, UsesTargetCompileTimeSetting) {
  using mesh::radio::selectRxBoostedGainDefault;

  EXPECT_EQ(selectRxBoostedGainDefault(true, 0, false, 0), 0);
  EXPECT_EQ(selectRxBoostedGainDefault(true, 1, false, 0), 1);
  EXPECT_EQ(selectRxBoostedGainDefault(false, 0, true, 0), 0);
  EXPECT_EQ(selectRxBoostedGainDefault(false, 0, true, 1), 1);
  EXPECT_EQ(selectRxBoostedGainDefault(false, 0, false, 0), 1);
}

TEST(RxBoostedGainDefaults, Sx126xSettingTakesPrecedence) {
  using mesh::radio::selectRxBoostedGainDefault;

  EXPECT_EQ(selectRxBoostedGainDefault(true, 0, true, 1), 0);
  EXPECT_EQ(selectRxBoostedGainDefault(true, 1, true, 0), 1);
}

TEST(RadioLivenessTracker, StagesSoftThenHardRecovery) {
  RadioLivenessTracker tracker;
  tracker.begin(1000);
  EXPECT_EQ(tracker.poll(1999, 1000, 5000), RadioRecoveryAction::NONE);
  EXPECT_EQ(tracker.poll(2000, 1000, 5000), RadioRecoveryAction::SOFT);
  EXPECT_EQ(tracker.poll(3000, 1000, 5000), RadioRecoveryAction::NONE);
  EXPECT_EQ(tracker.poll(6000, 1000, 5000), RadioRecoveryAction::HARD);
  EXPECT_EQ(tracker.poll(6999, 1000, 5000), RadioRecoveryAction::NONE);
  EXPECT_EQ(tracker.poll(35999, 1000, 5000), RadioRecoveryAction::NONE);
  EXPECT_EQ(tracker.poll(36000, 1000, 5000), RadioRecoveryAction::HARD);
  tracker.noteHardRecoveryResult(36000, true);
  EXPECT_EQ(tracker.poll(36999, 1000, 5000), RadioRecoveryAction::NONE);
  EXPECT_EQ(tracker.poll(37000, 1000, 5000), RadioRecoveryAction::SOFT);
}

TEST(RadioLivenessTracker, HardwareActivityCancelsEscalation) {
  RadioLivenessTracker tracker;
  tracker.begin(0);
  EXPECT_EQ(tracker.poll(1000, 1000, 5000), RadioRecoveryAction::SOFT);
  tracker.noteActivity(1200);
  EXPECT_EQ(tracker.stage(), 0);
  EXPECT_EQ(tracker.poll(2199, 1000, 5000), RadioRecoveryAction::NONE);
  EXPECT_EQ(tracker.poll(2200, 1000, 5000), RadioRecoveryAction::SOFT);
}

TEST(RadioLivenessTracker, ElapsedTimeIsRolloverSafe) {
  RadioLivenessTracker tracker;
  tracker.begin(0xFFFFFF00UL);
  EXPECT_EQ(tracker.poll(0x000000FFUL, 512, 4096), RadioRecoveryAction::NONE);
  EXPECT_EQ(tracker.poll(0x00000100UL, 512, 4096), RadioRecoveryAction::SOFT);
}

int main(int argc, char** argv) {
  ::testing::InitGoogleTest(&argc, argv);
  return RUN_ALL_TESTS();
}
