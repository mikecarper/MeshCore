#include <gtest/gtest.h>

#include <helpers/radiolib/CadTiming.h>

TEST(CadTiming, UsesShortDeadlineForCascadeProfile) {
  EXPECT_EQ(mesh::calculateCadScanTimeoutMillis(7, 62.5f), 100UL);
}

TEST(CadTiming, ScalesForSlowRadioSettings) {
  EXPECT_EQ(mesh::calculateCadScanTimeoutMillis(12, 62.5f), 414UL);
  EXPECT_EQ(mesh::calculateCadScanTimeoutMillis(12, 7.8f), 3171UL);
}

TEST(CadTiming, BoundsInvalidAndExtremeInputs) {
  EXPECT_EQ(mesh::calculateCadScanTimeoutMillis(0, 62.5f), 500UL);
  EXPECT_EQ(mesh::calculateCadScanTimeoutMillis(7, 0.0f), 500UL);
  EXPECT_EQ(mesh::calculateCadScanTimeoutMillis(12, 1.0f), 3500UL);
}

int main(int argc, char** argv) {
  ::testing::InitGoogleTest(&argc, argv);
  return RUN_ALL_TESTS();
}
