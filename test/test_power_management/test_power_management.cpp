#include <gtest/gtest.h>

#include <helpers/PowerManagementUtils.h>

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

int main(int argc, char** argv) {
  ::testing::InitGoogleTest(&argc, argv);
  return RUN_ALL_TESTS();
}
