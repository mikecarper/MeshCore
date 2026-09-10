#include <gtest/gtest.h>
#include <helpers/radiolib/NoiseFloorEstimator.h>

static void fill(NoiseFloorEstimator& estimator, float dbm, uint32_t start = 0) {
  for (unsigned i = 0; i < 64; ++i) ASSERT_TRUE(estimator.add(dbm, start + i * 50));
}

TEST(NoiseFloorEstimator, RequiresACompleteSpacedBlock) {
  NoiseFloorEstimator n;
  int32_t floor = -11000;
  for (uint32_t t = 0; t < 1000; ++t) n.add(-100, t);
  EXPECT_EQ(20, n.count());
  EXPECT_FALSE(n.publish(floor, true));
  EXPECT_EQ(-11000, floor);
  for (uint32_t t = 1000; t <= 3150; ++t) n.add(-100, t);
  EXPECT_TRUE(n.complete());
  EXPECT_TRUE(n.publish(floor, true));
  EXPECT_EQ(-10250, floor);
  EXPECT_GT(NoiseFloorEstimator::WINDOW_TIMEOUT_MS, 3150U);
}

TEST(NoiseFloorEstimator, RejectsSparseLowOutliersAndMajorityTraffic) {
  NoiseFloorEstimator n;
  int32_t floor = 0;
  for (unsigned i = 0; i < 64; ++i) {
    ASSERT_TRUE(n.add(i < 7 ? -127 : i >= 16 ? -30 : -103.25f, i * 50));
  }
  EXPECT_TRUE(n.publish(floor, false));
  EXPECT_EQ(-10325, floor);
}

TEST(NoiseFloorEstimator, InterpolatesPercentileAndClampsAtMinus120) {
  NoiseFloorEstimator n;
  int32_t floor = 0;
  for (unsigned i = 0; i < 64; ++i) {
    n.add(i < 7 ? -106 : i == 7 ? -105.5f : i == 8 ? -104.5f : -104, i * 50);
  }
  ASSERT_TRUE(n.publish(floor, false));
  EXPECT_EQ(-10500, floor);
  n.reset(); fill(n, -127);
  ASSERT_TRUE(n.publish(floor, false));
  EXPECT_EQ(-12000, floor);
}

TEST(NoiseFloorEstimator, PersistentRiseEscapesTheOldAdmissionGate) {
  NoiseFloorEstimator n;
  int32_t floor = -12000;
  for (unsigned block = 1; block <= 3; ++block) {
    n.reset(); fill(n, -100);
    EXPECT_EQ(block == 3, n.publish(floor, true));
    EXPECT_EQ(block == 3 ? -10500 : -12000, floor);
  }
  n.reset(); fill(n, -100);
  ASSERT_TRUE(n.publish(floor, true));
  EXPECT_EQ(-10125, floor);
}

TEST(NoiseFloorEstimator, QuieterBlockBreaksTheConsecutiveRiseHold) {
  NoiseFloorEstimator n;
  int32_t floor = -10000;
  fill(n, -70); EXPECT_FALSE(n.publish(floor, true));
  n.reset(); fill(n, -110); EXPECT_TRUE(n.publish(floor, true));
  EXPECT_EQ(-10750, floor);
  n.reset(); fill(n, -70); EXPECT_FALSE(n.publish(floor, true));
  EXPECT_EQ(1, n.heldBlocks());
}

TEST(NoiseFloorEstimator, PartialTimeoutDoesNotCountAsACompleteHeldBlock) {
  NoiseFloorEstimator n;
  int32_t floor = -12000;
  fill(n, -100); EXPECT_FALSE(n.publish(floor, true));
  n.reset(); n.add(-40, 10000);
  EXPECT_FALSE(n.publish(floor, true));
  n.reset(); fill(n, -100); EXPECT_FALSE(n.publish(floor, true));
  EXPECT_EQ(-12000, floor);
  n.reset(); fill(n, -100); EXPECT_TRUE(n.publish(floor, true));
}

TEST(NoiseFloorEstimator, GainResetDiscardsSamplesAndRiseHistory) {
  NoiseFloorEstimator n;
  int32_t floor = -12000;
  fill(n, -100); EXPECT_FALSE(n.publish(floor, true));
  n.reset(true);
  EXPECT_EQ(0, n.count());
  EXPECT_EQ(0, n.heldBlocks());
  EXPECT_EQ(-12000, floor);
  fill(n, -80); EXPECT_TRUE(n.publish(floor, false));
  EXPECT_EQ(-8000, floor);
}

TEST(NoiseFloorEstimator, SpacingSurvivesMillisRolloverAndRejectsInvalidReads) {
  NoiseFloorEstimator n;
  EXPECT_FALSE(n.add(NAN, 0));
  EXPECT_FALSE(n.add(INFINITY, 0));
  EXPECT_FALSE(n.add(0, 0));
  EXPECT_FALSE(n.add(-201, 0));
  EXPECT_TRUE(n.add(-105, 0xFFFFFFF0U));
  EXPECT_FALSE(n.add(-105, 0x20));
  EXPECT_TRUE(n.add(-105, 0x22));
}

TEST(NoiseFloorEstimator, NearlyContinuousInterferenceIsAnExplicitLimit) {
  NoiseFloorEstimator n;
  int32_t floor = -10500;
  for (unsigned block = 0; block < 3; ++block) {
    n.reset();
    for (unsigned i = 0; i < 64; ++i) n.add(i < 60 ? -60 : -105, i * 50);
    EXPECT_EQ(block == 2, n.publish(floor, true));
  }
  EXPECT_EQ(-7125, floor); // too few quiet samples to distinguish traffic from noise
}

TEST(NoiseFloorEstimator, LowTailContaminationRequiresSettledReceiverSamples) {
  NoiseFloorEstimator n;
  int32_t floor = -10500;
  for (unsigned i = 0; i < 64; ++i) n.add(i < 15 ? -127 : -105, i * 50);
  ASSERT_TRUE(n.publish(floor, true));
  EXPECT_EQ(-11625, floor); // the RX wrapper must exclude unsettled frontend readings
}

TEST(NoiseFloorEstimator, CapturedOffSfTrafficDoesNotBecomeTheBackgroundFloor) {
  // Mercerwood Heltec V4: matching T1000-E frames plus dense RAK3401 traffic
  // at another SF/BW. Sorted centi-dBm captures retain the sparse low outliers.
  const int16_t quiet[64] = {
    -8300, -8200, -8100, -8100, -7000, -7000, -7000, -7000,
    -7000, -7000, -7000, -7000, -7000, -7000, -7000, -7000,
    -7000, -7000, -7000, -7000, -7000, -7000, -6900, -6900,
    -6900, -6900, -6900, -6900, -6900, -6900, -6900, -6900,
    -6900, -6900, -6900, -6900, -6900, -6900, -6900, -6900,
    -6900, -6900, -6900, -6900, -6900, -6900, -6900, -6900,
    -6900, -6900, -6900, -6900, -6900, -6900, -6900, -6900,
    -6900, -6900, -6900, -6900, -6900, -6800, -6500, -1100
  };
  const int16_t noisy[64] = {
    -8200, -8200, -7000, -7000, -6900, -6900, -6900, -6900,
    -6900, -6900, -6900, -6900, -6900, -6400, -4500, -4200,
    -3600, -3600, -3500, -3400, -3400, -3300, -3200, -3200,
    -3100, -3000, -3000, -3000, -2900, -2800, -2600, -2600,
    -2500, -2400, -2400, -2400, -2300, -2300, -2300, -2300,
    -2300, -2300, -2300, -2200, -2200, -2200, -2200, -2200,
    -2100, -2100, -2100, -2000, -2000, -2000, -1900, -1900,
    -1900, -1900, -1800, -1800, -1800, -1800, -1100, -1100
  };
  NoiseFloorEstimator n;
  int32_t floor = 0;
  for (unsigned i = 0; i < 64; ++i) n.add(quiet[i] / 100.0f, i * 50);
  ASSERT_TRUE(n.publish(floor, false));
  EXPECT_GE(floor, -7300);
  EXPECT_LE(floor, -6800);
  for (unsigned block = 0; block < 5; ++block) {
    n.reset();
    for (unsigned i = 0; i < 64; ++i) n.add(noisy[i] / 100.0f, i * 50);
    n.publish(floor, true);
    EXPECT_GE(floor, -7300);
    EXPECT_LE(floor, -6800); // a median or lower quartile follows the interferer
  }
}

int main(int argc, char** argv) {
  ::testing::InitGoogleTest(&argc, argv);
  return RUN_ALL_TESTS();
}
