#include <gtest/gtest.h>

#include <helpers/radiolib/RXPowerSaving.h>

TEST(RxPowerSaving, BalancedNormalProfileKeepsDerivedDutyCycle) {
  uint32_t rx_us = 0;
  uint32_t sleep_us = 0;

  ASSERT_TRUE(calcRxPowerSavingLevel(5, 7, 62.5f, 16, &rx_us, &sleep_us));
  EXPECT_EQ(20936U, rx_us);
  EXPECT_EQ(13425U, sleep_us);
  EXPECT_FALSE(rxPowerSavingUsesContinuousFallback(rx_us, sleep_us));
}

TEST(RxPowerSaving, Sf5Bw500UsesPreamble128) {
  uint32_t rx_us = 0;
  uint32_t sleep_us = 0;
  uint8_t effective_level = 0;
  uint8_t effective_preamble = 0;

  ASSERT_TRUE(recalcRxPowerSavingFromLevel(5, 5, 500.0f, 16,
                                           &rx_us, &sleep_us,
                                           &effective_level,
                                           &effective_preamble));
  EXPECT_EQ(5U, effective_level);
  EXPECT_EQ(128U, effective_preamble);
  EXPECT_FALSE(rxPowerSavingUsesContinuousFallback(rx_us, sleep_us));
}

TEST(RxPowerSaving, ReturningToSlowerProfileRestoresSmallerPreambleTiming) {
  uint32_t rx_us = 0;
  uint32_t sleep_us = 0;
  uint8_t effective_level = 0;
  uint8_t effective_preamble = 0;

  ASSERT_TRUE(recalcRxPowerSavingFromLevel(5, 5, 500.0f, 16,
                                           &rx_us, &sleep_us,
                                           &effective_level,
                                           &effective_preamble));
  ASSERT_EQ(128U, effective_preamble);

  ASSERT_TRUE(recalcRxPowerSavingFromLevel(5, 9, 500.0f, 16,
                                           &rx_us, &sleep_us,
                                           &effective_level,
                                           &effective_preamble));
  EXPECT_EQ(10468U, rx_us);
  EXPECT_EQ(6712U, sleep_us);
  EXPECT_EQ(5U, effective_level);
  EXPECT_EQ(16U, effective_preamble);
  EXPECT_FALSE(rxPowerSavingUsesContinuousFallback(rx_us, sleep_us));
}

TEST(RxPowerSaving, TcxoThresholdSeparatesFastestUsableProfiles) {
  constexpr uint32_t sx1262_tcxo_transition_us = 6000;
  uint32_t rx_us = 0;
  uint32_t sleep_us = 0;

  ASSERT_TRUE(calcRxPowerSavingLevel(6, 7, 500.0f, 32,
                                     &rx_us, &sleep_us));
  EXPECT_FALSE(canStartRxPowerSavingDutyCycle(
      rx_us, sleep_us, sx1262_tcxo_transition_us));

  ASSERT_TRUE(calcRxPowerSavingLevel(7, 7, 500.0f, 32,
                                     &rx_us, &sleep_us));
  EXPECT_TRUE(canStartRxPowerSavingDutyCycle(
      rx_us, sleep_us, sx1262_tcxo_transition_us));
}

TEST(RxPowerSaving, WirePreambleUses64OnlyWhen32CannotEnableRxps) {
  EXPECT_EQ(32U, rxPowerSavingPreambleForParams(7, 500.0f));
  EXPECT_EQ(32U, rxPowerSavingPreambleForParams(6, 250.0f));
  EXPECT_EQ(32U, rxPowerSavingPreambleForParams(5, 125.0f));
  EXPECT_EQ(32U, rxPowerSavingPreambleForParams(5, 62.5f));

  EXPECT_EQ(64U, rxPowerSavingPreambleForParams(5, 250.0f));
  EXPECT_EQ(64U, rxPowerSavingPreambleForParams(6, 500.0f));

  EXPECT_EQ(128U, rxPowerSavingPreambleForParams(5, 500.0f));
  EXPECT_EQ(16U, rxPowerSavingPreambleForParams(9, 500.0f));
}

TEST(RxPowerSaving, RequestedLevelIsRaisedOnlyToRequiredMinimum) {
  constexpr uint32_t sx1262_tcxo_transition_us = 6000;
  uint32_t rx_us = 0;
  uint32_t sleep_us = 0;
  uint8_t effective_level = 0;
  uint8_t effective_preamble = 0;

  ASSERT_TRUE(calcRxPowerSavingLevelAtOrAbove(
      1, 7, 500.0f, 16, sx1262_tcxo_transition_us,
      &rx_us, &sleep_us, &effective_level, &effective_preamble));
  EXPECT_EQ(7U, effective_level);
  EXPECT_EQ(32U, effective_preamble);
  EXPECT_TRUE(canStartRxPowerSavingDutyCycle(
      rx_us, sleep_us, sx1262_tcxo_transition_us));

  ASSERT_TRUE(calcRxPowerSavingLevelAtOrAbove(
      8, 7, 500.0f, 16, sx1262_tcxo_transition_us,
      &rx_us, &sleep_us, &effective_level, &effective_preamble));
  EXPECT_EQ(8U, effective_level);
  EXPECT_EQ(32U, effective_preamble);
  EXPECT_TRUE(canStartRxPowerSavingDutyCycle(
      rx_us, sleep_us, sx1262_tcxo_transition_us));
}

TEST(RxPowerSaving, Sf5Bw500UsesPreamble128AtEffectiveLevel8) {
  constexpr uint32_t sx1262_tcxo_transition_us = 6000;
  uint32_t rx_us = 0;
  uint32_t sleep_us = 0;
  uint8_t effective_level = 99;
  uint8_t effective_preamble = 99;

  ASSERT_TRUE(calcRxPowerSavingLevelAtOrAbove(
      1, 5, 500.0f, 16, sx1262_tcxo_transition_us,
      &rx_us, &sleep_us, &effective_level, &effective_preamble));
  EXPECT_EQ(8U, effective_level);
  EXPECT_EQ(128U, effective_preamble);
  EXPECT_EQ(626U, rx_us);
  EXPECT_EQ(6398U, sleep_us);
  EXPECT_TRUE(canStartRxPowerSavingDutyCycle(
      rx_us, sleep_us, sx1262_tcxo_transition_us));
  EXPECT_FALSE(rxPowerSavingUsesContinuousFallback(rx_us, sleep_us));
}

TEST(RxPowerSaving, Sf5Bw250NeedsLongerTransmittedPreambleOnTcxoRadio) {
  constexpr uint32_t sx1262_tcxo_transition_us = 6000;
  uint32_t rx_us = 0;
  uint32_t sleep_us = 0;
  uint8_t effective_level = 99;
  uint8_t effective_preamble = 99;

  // Even the maximum level only provides a 3.616 ms sleep window. RadioLib
  // must reserve 6 ms for the SX1262 TCXO and sleep/wake transitions.
  ASSERT_TRUE(calcRxPowerSavingLevel(10, 5, 250.0f, 32,
                                     &rx_us, &sleep_us));
  EXPECT_EQ(1024U, rx_us);
  EXPECT_EQ(3616U, sleep_us);
  EXPECT_FALSE(canStartRxPowerSavingDutyCycle(
      rx_us, sleep_us, sx1262_tcxo_transition_us));

  ASSERT_TRUE(calcRxPowerSavingLevelAtOrAbove(
      8, 5, 250.0f, 32, sx1262_tcxo_transition_us,
      &rx_us, &sleep_us, &effective_level, &effective_preamble));
  EXPECT_EQ(8U, effective_level);
  EXPECT_EQ(64U, effective_preamble);
  EXPECT_EQ(1252U, rx_us);
  EXPECT_EQ(6424U, sleep_us);
  EXPECT_FALSE(rxPowerSavingUsesContinuousFallback(rx_us, sleep_us));
}

TEST(RxPowerSaving, Sf5Bw250UsesPreamble64AtEffectiveLevel8) {
  constexpr uint32_t sx1262_tcxo_transition_us = 6000;
  uint32_t rx_us = 0;
  uint32_t sleep_us = 0;
  uint8_t effective_level = 0;
  uint8_t effective_preamble = 0;

  ASSERT_TRUE(calcRxPowerSavingLevelAtOrAbove(
      1, 5, 250.0f, 64, sx1262_tcxo_transition_us,
      &rx_us, &sleep_us, &effective_level, &effective_preamble));
  EXPECT_EQ(8U, effective_level);
  EXPECT_EQ(64U, effective_preamble);
  EXPECT_EQ(1252U, rx_us);
  EXPECT_EQ(6424U, sleep_us);
  EXPECT_TRUE(canStartRxPowerSavingDutyCycle(
      rx_us, sleep_us, sx1262_tcxo_transition_us));
}

TEST(RxPowerSaving, Sf5Bw250SelectorUsesPreamble64Automatically) {
  constexpr uint32_t sx1262_tcxo_transition_us = 6000;
  uint32_t rx_us = 0;
  uint32_t sleep_us = 0;
  uint8_t effective_level = 0;
  uint8_t effective_preamble = 0;

  ASSERT_TRUE(calcRxPowerSavingLevelAtOrAbove(
      1, 5, 250.0f, 16, sx1262_tcxo_transition_us,
      &rx_us, &sleep_us, &effective_level, &effective_preamble));
  EXPECT_EQ(8U, effective_level);
  EXPECT_EQ(64U, effective_preamble);
  EXPECT_EQ(1252U, rx_us);
  EXPECT_EQ(6424U, sleep_us);
}

TEST(RxPowerSaving, Sf5Bw125KeepsPreamble32) {
  constexpr uint32_t sx1262_tcxo_transition_us = 6000;
  uint32_t rx_us = 0;
  uint32_t sleep_us = 0;
  uint8_t effective_level = 0;
  uint8_t effective_preamble = 0;

  ASSERT_TRUE(calcRxPowerSavingLevelAtOrAbove(
      1, 5, 125.0f, 16, sx1262_tcxo_transition_us,
      &rx_us, &sleep_us, &effective_level, &effective_preamble));
  EXPECT_EQ(7U, effective_level);
  EXPECT_EQ(32U, effective_preamble);
  EXPECT_EQ(2731U, rx_us);
  EXPECT_EQ(6101U, sleep_us);
  EXPECT_TRUE(canStartRxPowerSavingDutyCycle(
      rx_us, sleep_us, sx1262_tcxo_transition_us));
}

TEST(RxPowerSaving, EquivalentFastProfilesSupportExplicitPreamble32Level7) {
  constexpr uint32_t sx1262_tcxo_transition_us = 6000;
  uint32_t rx_us = 0;
  uint32_t sleep_us = 0;
  uint8_t effective_level = 0;
  uint8_t effective_preamble = 0;

  ASSERT_TRUE(calcRxPowerSavingLevelAtOrAbove(
      7, 7, 500.0f, 32, sx1262_tcxo_transition_us,
      &rx_us, &sleep_us, &effective_level, &effective_preamble));
  EXPECT_EQ(7U, effective_level);
  EXPECT_EQ(32U, effective_preamble);
  EXPECT_EQ(2731U, rx_us);
  EXPECT_EQ(6101U, sleep_us);

  ASSERT_TRUE(calcRxPowerSavingLevelAtOrAbove(
      7, 6, 250.0f, 32, sx1262_tcxo_transition_us,
      &rx_us, &sleep_us, &effective_level, &effective_preamble));
  EXPECT_EQ(7U, effective_level);
  EXPECT_EQ(32U, effective_preamble);

  ASSERT_TRUE(calcRxPowerSavingLevelAtOrAbove(
      7, 5, 125.0f, 32, sx1262_tcxo_transition_us,
      &rx_us, &sleep_us, &effective_level, &effective_preamble));
  EXPECT_EQ(7U, effective_level);
  EXPECT_EQ(32U, effective_preamble);
}

TEST(RxPowerSaving, Sf6Bw250KeepsPreamble32) {
  constexpr uint32_t sx1262_tcxo_transition_us = 6000;
  uint32_t rx_us = 0;
  uint32_t sleep_us = 0;
  uint8_t effective_level = 0;
  uint8_t effective_preamble = 0;

  ASSERT_TRUE(calcRxPowerSavingLevelAtOrAbove(
      1, 6, 250.0f, 16, sx1262_tcxo_transition_us,
      &rx_us, &sleep_us, &effective_level, &effective_preamble));
  EXPECT_EQ(7U, effective_level);
  EXPECT_EQ(32U, effective_preamble);
  EXPECT_EQ(2731U, rx_us);
  EXPECT_EQ(6101U, sleep_us);
}

TEST(RxPowerSaving, Sf6Bw500UsesPreamble64WhenRequired) {
  constexpr uint32_t sx1262_tcxo_transition_us = 6000;
  uint32_t rx_us = 0;
  uint32_t sleep_us = 0;
  uint8_t effective_level = 0;
  uint8_t effective_preamble = 0;

  ASSERT_TRUE(calcRxPowerSavingLevelAtOrAbove(
      1, 6, 500.0f, 16, sx1262_tcxo_transition_us,
      &rx_us, &sleep_us, &effective_level, &effective_preamble));
  EXPECT_EQ(8U, effective_level);
  EXPECT_EQ(64U, effective_preamble);
  EXPECT_EQ(1252U, rx_us);
  EXPECT_EQ(6424U, sleep_us);
}

TEST(RxPowerSaving, TimingAssumptionCannotExceedWirePreamble) {
  constexpr uint32_t sx1262_tcxo_transition_us = 6000;
  uint32_t rx_us = 0;
  uint32_t sleep_us = 0;
  uint8_t effective_level = 0;
  uint8_t effective_preamble = 0;

  ASSERT_TRUE(calcRxPowerSavingLevelAtOrAbove(
      1, 7, 500.0f, 64, sx1262_tcxo_transition_us,
      &rx_us, &sleep_us, &effective_level, &effective_preamble));
  EXPECT_EQ(7U, effective_level);
  EXPECT_EQ(32U, effective_preamble);
  EXPECT_EQ(2731U, rx_us);
  EXPECT_EQ(6101U, sleep_us);
}

TEST(RxPowerSaving, Sf5Bw62Point5UsesPreamble16AtEffectiveLevel10) {
  constexpr uint32_t sx1262_tcxo_transition_us = 6000;
  uint32_t rx_us = 0;
  uint32_t sleep_us = 0;
  uint8_t effective_level = 0;
  uint8_t effective_preamble = 0;

  ASSERT_TRUE(calcRxPowerSavingLevelAtOrAbove(
      1, 5, 62.5f, 16, sx1262_tcxo_transition_us,
      &rx_us, &sleep_us, &effective_level, &effective_preamble));
  EXPECT_EQ(10U, effective_level);
  EXPECT_EQ(16U, effective_preamble);
  EXPECT_EQ(4096U, rx_us);
  EXPECT_EQ(6272U, sleep_us);
}

TEST(RxPowerSaving, RejectsInvalidInputs) {
  uint32_t rx_us = 123;
  uint32_t sleep_us = 456;

  EXPECT_FALSE(calcRxPowerSavingLevel(0, 7, 500.0f, 32,
                                      &rx_us, &sleep_us));
  EXPECT_FALSE(calcRxPowerSavingLevel(5, 4, 500.0f, 32,
                                      &rx_us, &sleep_us));
  EXPECT_FALSE(calcRxPowerSavingLevel(5, 7, 0.0f, 32,
                                      &rx_us, &sleep_us));
  EXPECT_FALSE(calcRxPowerSavingLevel(5, 7, 500.0f, 24,
                                      &rx_us, &sleep_us));
}

int main(int argc, char** argv) {
  ::testing::InitGoogleTest(&argc, argv);
  return RUN_ALL_TESTS();
}
