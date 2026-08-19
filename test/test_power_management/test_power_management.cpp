#include <gtest/gtest.h>

#include <helpers/PowerManagementUtils.h>
#include <helpers/GpsPowerPolicy.h>

TEST(PowerManagement, MedianRejectsOneBrownoutSample) {
  EXPECT_EQ(mesh::power::medianVoltage(3290, 3700, 3710), 3700);
  EXPECT_EQ(mesh::power::medianVoltage(3710, 3290, 3700), 3700);
  EXPECT_EQ(mesh::power::medianVoltage(3700, 3710, 3290), 3700);
}

TEST(PowerManagement, BootLockRequiresValidLowBatteryReading) {
  EXPECT_TRUE(mesh::power::shouldBootLock(3000, 3300, false));
  EXPECT_FALSE(mesh::power::shouldBootLock(999, 3300, false));
  EXPECT_FALSE(mesh::power::shouldBootLock(3300, 3300, false));
  EXPECT_FALSE(mesh::power::shouldBootLock(3000, 0, false));
  EXPECT_FALSE(mesh::power::shouldBootLock(3000, 3300, true));
}

TEST(GpsPowerPolicy, ZeroUsesOneSecondDefault) {
  uint32_t configured = 99;
  ASSERT_TRUE(mesh::gps::parseUpdateInterval("0", configured));
  EXPECT_EQ(0U, configured);
  EXPECT_EQ(1U, mesh::gps::effectiveUpdateIntervalSec(configured));
  EXPECT_EQ(1000U, mesh::gps::updateIntervalMillis(configured));
}

TEST(GpsPowerPolicy, AcceptsPersistedIntervalThroughOneDay) {
  uint32_t configured = 0;
  ASSERT_TRUE(mesh::gps::parseUpdateInterval("86400", configured));
  EXPECT_EQ(86400U, configured);
  EXPECT_EQ(86400000U, mesh::gps::updateIntervalMillis(configured));
}

TEST(GpsPowerPolicy, RejectsMalformedAndOutOfRangeIntervals) {
  uint32_t configured = 17;
  EXPECT_FALSE(mesh::gps::parseUpdateInterval("", configured));
  EXPECT_FALSE(mesh::gps::parseUpdateInterval("5s", configured));
  EXPECT_FALSE(mesh::gps::parseUpdateInterval("86401", configured));
  EXPECT_EQ(17U, configured);
}

int main(int argc, char** argv) {
  ::testing::InitGoogleTest(&argc, argv);
  return RUN_ALL_TESTS();
}
