#include <gtest/gtest.h>
#include <cmath>
#include <helpers/radiolib/RXPowerSaving.h>
#include <helpers/CLICommandUtils.h>

TEST(RxPowerSaving, WirePreamblesStayCompatibleRegardlessOfLocalPowerSaving) {
  EXPECT_EQ(32U, rxPowerSavingPreambleForParams(7, 500));
  EXPECT_EQ(32U, rxPowerSavingPreambleForParams(6, 250));
  EXPECT_EQ(32U, rxPowerSavingPreambleForParams(5, 125));
  EXPECT_EQ(64U, rxPowerSavingPreambleForParams(6, 500));
  EXPECT_EQ(64U, rxPowerSavingPreambleForParams(5, 250));
  EXPECT_EQ(128U, rxPowerSavingPreambleForParams(5, 500));
  EXPECT_EQ(16U, rxPowerSavingPreambleForParams(9, 500));
}

TEST(RxPowerSaving, GuardedProfilesCoverActualWireHeaderAndPromisedCatch) {
  for (float bw : {7.8f, 15.6f, 31.25f, 62.5f, 125.0f, 250.0f, 500.0f}) {
    for (uint8_t sf = 5; sf <= 12; ++sf) {
      for (uint32_t transition : {1000U, 2600U, 6000U}) {
        for (uint8_t requested = 1; requested <= 8; ++requested) {
          for (uint8_t assumption : {16, 32, 64, 128}) {
            SCOPED_TRACE(::testing::Message() << "sf=" << int(sf) << " bw=" << bw
                << " level=" << int(requested) << " preamble=" << int(assumption)
                << " transition=" << transition);
            uint32_t rx, sleep;
            uint8_t level, preamble;
            ASSERT_TRUE(calcRxPowerSavingLevelAtOrAbove(requested, sf, bw, assumption,
                transition, &rx, &sleep, &level, &preamble));
            if (rxPowerSavingUsesContinuousFallback(rx, sleep)) {
              EXPECT_EQ(0, level);
              EXPECT_EQ(0, preamble);
              continue;
            }
            EXPECT_GE(level, requested);
            EXPECT_LE(level, RX_POWERSAVING_MAX_LEVEL);
            EXPECT_LE(preamble, rxPowerSavingPreambleForParams(sf, bw));
            EXPECT_GE(sleep, transition + RX_POWERSAVING_MIN_SLEEP_MARGIN_US);
            EXPECT_TRUE(canStartRxPowerSavingDutyCycle(rx, sleep, transition));
            const double symbol = 1000.0 * (1U << sf) / bw;
            EXPECT_GE(preamble - sleep / symbol,
                      rxPowerSavingLevelCatch(level, preamble));
            const double actual_rx = (rx * 8U / 125U) * 15.625;
            const double actual_sleep = ((sleep - transition) * 8U / 125U) * 15.625;
            const double required = (rxPowerSavingPreambleForParams(sf, bw)
                + (sf <= 6 ? 6.25 : 4.25) + 8.0 + 1.0) * symbol;
            EXPECT_GE(2 * actual_rx + actual_sleep + 0.01, required);
          }
        }
      }
    }
  }
}

TEST(RxPowerSaving, AutomaticAdjustmentNeverSelectsExperimentalLevels) {
  uint32_t rx, sleep;
  uint8_t level, preamble;
  // The previous 6 ms TCXO budget cannot fit a guarded P32 at this tuple.
  ASSERT_TRUE(calcRxPowerSavingLevelAtOrAbove(1, 7, 500, 16, 6000,
      &rx, &sleep, &level, &preamble));
  EXPECT_TRUE(rxPowerSavingUsesContinuousFallback(rx, sleep));
  ASSERT_TRUE(calcRxPowerSavingLevelAtOrAbove(1, 7, 500, 16, 2600,
      &rx, &sleep, &level, &preamble));
  EXPECT_FALSE(rxPowerSavingUsesContinuousFallback(rx, sleep));
  EXPECT_LE(level, 8);
  EXPECT_EQ(32, preamble);
}

TEST(RxPowerSaving, FastTuplesRetainLongPreambleSupport) {
  uint32_t rx, sleep;
  uint8_t level, preamble;
  for (uint8_t sf : {5, 6}) {
    ASSERT_TRUE(calcRxPowerSavingLevelAtOrAbove(8, sf, 500, 16, 6000,
        &rx, &sleep, &level, &preamble));
    EXPECT_EQ(8, level);
    EXPECT_EQ(sf == 5 ? 128 : 64, preamble);
    EXPECT_FALSE(rxPowerSavingUsesContinuousFallback(rx, sleep));
  }
}

TEST(RxPowerSaving, NamedProfilesAndExplicitExperimentalModes) {
  EXPECT_EQ(3, RX_POWERSAVING_CONSERVATIVE_LEVEL);
  EXPECT_EQ(6, RX_POWERSAVING_BALANCED_LEVEL);
  EXPECT_EQ(8, RX_POWERSAVING_MAX_LEVEL);
  uint32_t rx9, sleep9, rx10, sleep10;
  ASSERT_TRUE(calcRxPowerSavingLevel(9, 8, 62.5f, 32, &rx9, &sleep9));
  ASSERT_TRUE(calcRxPowerSavingLevel(10, 8, 62.5f, 32, &rx10, &sleep10));
  EXPECT_LT(rx10, rx9);
  EXPECT_GT(sleep10, sleep9);
  EXPECT_FALSE(isRxPowerSavingUnguardedLevel(8));
  EXPECT_TRUE(isRxPowerSavingUnguardedLevel(9));
}

TEST(RxPowerSaving, BothFamiliesShareGuardedGeometry) {
  for (uint8_t level = 1; level <= 8; ++level) {
    uint32_t sx_rx, sx_sleep, lr_rx, lr_sleep;
    ASSERT_TRUE(calcRxPowerSavingLevel(level, 8, 62.5f, 32, &sx_rx, &sx_sleep, 6, 2600));
    ASSERT_TRUE(calcRxPowerSavingLevel(level, 8, 62.5f, 32, &lr_rx, &lr_sleep, 8, 2600));
    EXPECT_EQ(sx_rx, lr_rx);
    EXPECT_EQ(sx_sleep, lr_sleep);
  }
}

TEST(RxPowerSaving, ExperimentalProfilesAvoidKnownBadSx126xTicks) {
  for (uint32_t tick : {320U, 640U}) {
    EXPECT_EQ(tick + 1, avoidBadRxPowerSavingTick(rxPowerSavingTickToUs(tick)) * 8U / 125U);
  }
}

TEST(RxPowerSaving, RetuningPreservesManualTimingsAndRestoresRequestedLevel) {
  uint32_t rx = 12345, sleep = 67890;
  EXPECT_FALSE(recalcRxPowerSavingFromLevel(0, 8, 62.5f, 16, &rx, &sleep));
  EXPECT_EQ(12345U, rx);
  EXPECT_EQ(67890U, sleep);
  uint8_t level, preamble;
  ASSERT_TRUE(recalcRxPowerSavingFromLevel(3, 5, 500, 16, &rx, &sleep, &level, &preamble));
  ASSERT_TRUE(recalcRxPowerSavingFromLevel(3, 9, 62.5f, 16, &rx, &sleep, &level, &preamble));
  EXPECT_EQ(3, level);
  EXPECT_EQ(16, preamble);
}

TEST(RxPowerSaving, RejectsInvalidInputs) {
  uint32_t rx, sleep;
  EXPECT_FALSE(calcRxPowerSavingLevel(0, 8, 62.5f, 16, &rx, &sleep));
  EXPECT_FALSE(calcRxPowerSavingLevel(11, 8, 62.5f, 16, &rx, &sleep));
  EXPECT_FALSE(calcRxPowerSavingLevel(5, 4, 62.5f, 16, &rx, &sleep));
  EXPECT_FALSE(calcRxPowerSavingLevel(5, 8, 0, 16, &rx, &sleep));
  EXPECT_FALSE(calcRxPowerSavingLevel(5, 8, NAN, 16, &rx, &sleep));
  EXPECT_FALSE(calcRxPowerSavingLevel(5, 8, INFINITY, 16, &rx, &sleep));
  EXPECT_FALSE(calcRxPowerSavingLevel(5, 8, 62.5f, 24, &rx, &sleep));
  EXPECT_FALSE(calcRxPowerSavingLevel(5, 8, 62.5f, 16, nullptr, &sleep));
}

TEST(RxPowerSavingCLI, ParsesNewNamesAndRejectsTrailingJunk) {
  mesh::cli::RxPowerSavingArguments args = {};
  for (const char* name : {"max", "overdrive", "riskyWorkingMax"}) {
    ASSERT_TRUE(mesh::cli::parseRxPowerSavingArgumentsStrict(name, args));
    EXPECT_EQ(mesh::cli::rxPowerSavingNamedLevel(name), args.level);
    EXPECT_EQ(16U, args.preamble);
  }
  ASSERT_TRUE(mesh::cli::parseRxPowerSavingArgumentsStrict("max preamble 32", args));
  EXPECT_EQ(8U, args.level);
  EXPECT_EQ(32U, args.preamble);
  for (const char* text : {"maxx", "max extra", "max preamble 32 extra", "riskyworkingmax",
                           "level 4294967296", "overdrive preamble nope"}) {
    EXPECT_FALSE(mesh::cli::parseRxPowerSavingArgumentsStrict(text, args)) << text;
  }
}

int main(int argc, char** argv) {
  ::testing::InitGoogleTest(&argc, argv);
  return RUN_ALL_TESTS();
}
