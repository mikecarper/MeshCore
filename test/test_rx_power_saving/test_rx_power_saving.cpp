#include <gtest/gtest.h>
#include <algorithm>
#include <cmath>
#include <helpers/radiolib/RXPowerSaving.h>
#include <helpers/radiolib/RXPowerSavingCLI.h>
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

namespace {
class RxPowerSavingTestControl : public RxPowerSavingControl {
public:
  bool accept = true;
  bool enabled = false;
  bool config_error = false;
  uint32_t transition_us = 1000;
  float capture_cost = 8;
  uint32_t rx_us = 0, sleep_us = 0;
  RxPowerSavingStatus status;
  RxPowerSavingStatus getRxPowerSavingStatus() const override { return status; }
  bool setRxPowerSaving(bool on, uint32_t rx, uint32_t sleep) override {
    if (!accept) return false;
    enabled = on; rx_us = rx; sleep_us = sleep;
    return true;
  }
  void setRxPowerSavingConfigError(bool invalid) override { config_error = invalid; }
  float rxPowerSavingCaptureCostSymbols() const override { return capture_cost; }
  uint32_t rxPowerSavingTransitionUs() const override { return transition_us; }
};
}

TEST(RxPowerSaving, ArmFailuresBackOffAndRecoverAcrossClockRollover) {
  for (uint32_t origin : {100U, UINT32_MAX - 4999U}) {
    RxPowerSavingArmRetryState retry;
    uint32_t now = origin;
    EXPECT_TRUE(retry.canAttempt(now));
    EXPECT_FALSE(retry.pending());
    EXPECT_EQ(0U, retry.delayMs(now));
    unsigned failures = 0;
    for (uint32_t delay : {5000U, 15000U, 60000U, 300000U, 300000U}) {
      ASSERT_TRUE(retry.canAttempt(now));
      retry.recordFailure(now);
      EXPECT_TRUE(retry.pending());
      EXPECT_EQ(++failures, retry.consecutiveFailures());
      EXPECT_FALSE(retry.canAttempt(now));
      EXPECT_EQ(delay, retry.delayMs(now));
      now += delay - 1;
      EXPECT_FALSE(retry.canAttempt(now));
      EXPECT_EQ(1U, retry.delayMs(now));
      ++now;
      EXPECT_TRUE(retry.canAttempt(now));
      EXPECT_EQ(0U, retry.delayMs(now));
    }
    // Admission deferral retains the streak and cannot create a due-wake spin.
    retry.defer(now);
    EXPECT_EQ(failures, retry.consecutiveFailures());
    EXPECT_EQ(100U, retry.delayMs(now));
    now += 100;
    EXPECT_TRUE(retry.canAttempt(now));
    retry.recordSuccess();
    EXPECT_EQ(0U, retry.consecutiveFailures());
    EXPECT_FALSE(retry.pending());
    EXPECT_TRUE(retry.canAttempt(now));
    retry.recordFailure(now);
    EXPECT_EQ(5000U, retry.delayMs(now));
    retry.reset();
    EXPECT_FALSE(retry.pending());
    EXPECT_EQ(0U, retry.consecutiveFailures());
    EXPECT_TRUE(retry.canAttempt(now));
  }
}

TEST(RxPowerSaving, ArmFailureStreakSaturatesAndBackoffRemainsBounded) {
  RxPowerSavingArmRetryState retry;
  uint32_t now = 0;
  for (unsigned attempt = 0; attempt < 300; ++attempt) {
    ASSERT_TRUE(retry.canAttempt(now));
    retry.recordFailure(now);
    EXPECT_EQ(std::min(attempt + 1, 255U), retry.consecutiveFailures());
    EXPECT_LE(retry.delayMs(now), 300000U);
    now += retry.delayMs(now);
  }
  EXPECT_EQ(255U, retry.consecutiveFailures());
  EXPECT_TRUE(retry.canAttempt(now));
}

TEST(RxPowerSaving, StaleArmDeadlinesAndOversizedDefersCannotCreateLongWaits) {
  for (uint32_t origin : {100U, UINT32_MAX - 4999U}) {
    RxPowerSavingArmRetryState retry;
    retry.recordFailure(origin);
    // A scanner or radio owner can retain a failed arm for more than half
    // the millis range. Its expired deadline must remain immediately due.
    const uint32_t resumed = origin + UINT32_C(0x80000000) + 1000000U;
    EXPECT_TRUE(retry.pending());
    EXPECT_TRUE(retry.canAttempt(resumed));
    EXPECT_EQ(0U, retry.delayMs(resumed));
    retry.defer(resumed, UINT32_MAX);
    EXPECT_EQ(1U, retry.consecutiveFailures());
    EXPECT_FALSE(retry.canAttempt(resumed));
    EXPECT_EQ(300000U, retry.delayMs(resumed));
    EXPECT_FALSE(retry.canAttempt(resumed + 299999U));
    EXPECT_EQ(1U, retry.delayMs(resumed + 299999U));
    EXPECT_TRUE(retry.canAttempt(resumed + 300000U));
    EXPECT_EQ(0U, retry.delayMs(resumed + 300000U));
  }
}

TEST(RxPowerSaving, ConfigUsesRuntimeRadioTimingAndKeepsWirePreamble) {
  RxPowerSavingTestControl control;
  RxPowerSavingConfig config;
  config.enabled = 1;
  ASSERT_TRUE(applyRxPowerSavingConfig(&config, 6, 500, &control));
  EXPECT_TRUE(control.enabled);
  EXPECT_FALSE(control.config_error);
  EXPECT_FALSE(rxPowerSavingUsesContinuousFallback(control.rx_us, control.sleep_us));
  EXPECT_EQ(16, config.preamble);
  uint32_t rx = 0, sleep = 0;
  ASSERT_TRUE(recalcRxPowerSavingFromLevel(config.level, 6, 500, 16, &rx, &sleep,
      nullptr, nullptr, control.transition_us, control.capture_cost));
  EXPECT_EQ(rx, control.rx_us);
  EXPECT_EQ(sleep, control.sleep_us);
}

TEST(RxPowerSaving, InvalidConfigFallsBackWithoutOverwritingRequestedPolicy) {
  RxPowerSavingTestControl control;
  RxPowerSavingConfig config;
  config.enabled = 1;
  config.level = 0;
  config.rx_us = 500;
  EXPECT_FALSE(applyRxPowerSavingConfig(&config, 8, 62.5f, &control));
  EXPECT_EQ(1, config.enabled);
  EXPECT_EQ(500U, config.rx_us);
  EXPECT_FALSE(control.enabled);
  EXPECT_TRUE(control.config_error);
  EXPECT_FALSE(normalizeRxPowerSavingConfig(&config, 8, NAN));
}

TEST(RxPowerSavingCLI, RejectedCommandsNeverOverwriteSavedConfig) {
  RxPowerSavingTestControl control;
  RxPowerSavingConfig config;
  char reply[160];
  for (const char* invalid : {"max extra", "level 4294967296", "level 256", "500 1000",
                            "max preamble 65552", "riskyworkingmax", "level 8 preamble 24"}) {
    EXPECT_FALSE(RXPowerSavingCLI::set(invalid, 8, 62.5f, &config, &control, reply, sizeof(reply))) << invalid;
    EXPECT_EQ(0, config.enabled);
    EXPECT_EQ(RX_POWERSAVING_BALANCED_LEVEL, config.level);
  }
  control.accept = false;
  EXPECT_FALSE(RXPowerSavingCLI::set("max", 8, 62.5f, &config, &control, reply, sizeof(reply)));
  EXPECT_EQ(0, config.enabled);
}

TEST(RxPowerSavingCLI, OffPreservesRequestedLevelAndManualPeriods) {
  RxPowerSavingTestControl control;
  RxPowerSavingConfig config;
  char reply[160];
  ASSERT_TRUE(RXPowerSavingCLI::set("max preamble 64", 5, 500, &config, &control, reply, sizeof(reply)));
  EXPECT_EQ(8, config.level);
  EXPECT_EQ(64, config.preamble);
  ASSERT_TRUE(RXPowerSavingCLI::set("off", 5, 500, &config, &control, reply, sizeof(reply)));
  EXPECT_EQ(0, config.enabled);
  EXPECT_EQ(8, config.level);
  ASSERT_TRUE(RXPowerSavingCLI::set("12000 5000", 8, 62.5f, &config, &control, reply, sizeof(reply)));
  EXPECT_EQ(0, config.level);
  EXPECT_EQ(12000U, config.rx_us);
  EXPECT_EQ(5000U, config.sleep_us);
}

TEST(RxPowerSavingCLI, StatusUsesLiveWirePreambleAndReportsHardwareClamp) {
  RxPowerSavingTestControl control;
  control.status.supported = control.status.armed = true;
  control.status.effective_rx_us = 78000;
  control.status.effective_sleep_us = 60000;
  RxPowerSavingConfig config;
  config.enabled = 1;
  char reply[160];
  RXPowerSavingCLI::get(&config, &control, 8, 62.5f, reply, sizeof(reply), 32);
  EXPECT_NE(nullptr, strstr(reply, "effective=armed"));
  EXPECT_NE(nullptr, strstr(reply, "catch=17.4"));
  EXPECT_NE(nullptr, strstr(reply, "erx=78000,eslp=60000"));
  control.status.armed = false;
  RXPowerSavingCLI::get(&config, &control, 8, 62.5f, reply, sizeof(reply), 32);
  EXPECT_EQ(nullptr, strstr(reply, "catch="));
}

TEST(RxPowerSavingCLI, StatusCatchRejectsInvalidBandwidthAndBoundsMaximumPeriods) {
  RxPowerSavingTestControl control;
  control.status.supported = control.status.armed = true;
  RxPowerSavingConfig config;
  config.sleep_us = config.rx_us = RX_POWERSAVING_MAX_PERIOD_US;
  control.status.effective_rx_us = config.rx_us;
  control.status.effective_sleep_us = config.sleep_us;
  char reply[160];
  for (float bw : {NAN, INFINITY, 0.0f, -1.0f, 501.0f}) {
    RXPowerSavingCLI::get(&config, &control, 5, bw, reply, sizeof(reply), 65535);
    EXPECT_EQ(nullptr, strstr(reply, "catch="));
  }
  RXPowerSavingCLI::get(&config, &control, 5, 500.0f, reply, sizeof(reply), 65535);
  EXPECT_NE(nullptr, strstr(reply, "catch=-403215.0"));
  EXPECT_LT(strlen(reply), sizeof(reply));
}

TEST(RxPowerSavingCLI, CompactStatusKeepsExperimentalWarningAndFullSetterNames) {
  RxPowerSavingTestControl control;
  RxPowerSavingConfig config;
  char reply[160];
  for (const char* name : {"overdrive", "riskyWorkingMax"}) {
    ASSERT_TRUE(RXPowerSavingCLI::set(name, 8, 62.5f, &config, &control, reply, sizeof(reply)));
    EXPECT_NE(nullptr, strstr(reply, name));
    RXPowerSavingCLI::get(&config, &control, 8, 62.5f, reply, sizeof(reply));
    EXPECT_NE(nullptr, strstr(reply, config.level == 9 ? "level=9(!)" : "level=10(!)"));
  }
}

int main(int argc, char** argv) {
  ::testing::InitGoogleTest(&argc, argv);
  return RUN_ALL_TESTS();
}
