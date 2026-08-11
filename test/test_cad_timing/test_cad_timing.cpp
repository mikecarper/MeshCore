#include <gtest/gtest.h>

#include <helpers/radiolib/CadTiming.h>
#include <helpers/radiolib/LR2021SideDetectorConfig.h>
#include <helpers/radiolib/RadioAirtime.h>

TEST(RadioAirtime, DetectsUnsignedRadioLibErrors) {
  EXPECT_TRUE(mesh::isEncodedRadioLibAirtimeError(UINT32_MAX));
  EXPECT_TRUE(mesh::isEncodedRadioLibAirtimeError(
      static_cast<uint32_t>(-20)));
  EXPECT_FALSE(mesh::isEncodedRadioLibAirtimeError(200000UL));
}

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

TEST(LR2021SideDetectors, ParsesStrictBoundedListsAndDisable) {
  uint8_t side_sfs[mesh::lr2021::STORED_SIDE_DETECTOR_BYTES] = {99, 99, 99, 99};
  uint8_t num = 99;

  EXPECT_TRUE(mesh::lr2021::parseSideDetectorSFList("", side_sfs, num));
  EXPECT_EQ(0, num);
  EXPECT_EQ(0, side_sfs[0]);
  EXPECT_TRUE(mesh::lr2021::validateSideDetectorSFs(nullptr, 0, 0, 0.0f));
  EXPECT_FALSE(mesh::lr2021::validateSideDetectorSFs(nullptr, 1, 8, 62.5f));

  EXPECT_TRUE(mesh::lr2021::parseSideDetectorSFList("9,10,12", side_sfs, num));
  EXPECT_EQ(3, num);
  EXPECT_EQ(9, side_sfs[0]);
  EXPECT_EQ(10, side_sfs[1]);
  EXPECT_EQ(12, side_sfs[2]);
  EXPECT_EQ(0, side_sfs[3]);

  EXPECT_FALSE(mesh::lr2021::parseSideDetectorSFList("9,10,11,12", side_sfs, num));
  EXPECT_FALSE(mesh::lr2021::parseSideDetectorSFList("9,", side_sfs, num));
  EXPECT_FALSE(mesh::lr2021::parseSideDetectorSFList("9junk", side_sfs, num));
  EXPECT_FALSE(mesh::lr2021::parseSideDetectorSFList(
      "999999999999999999999999999999999999999999999999999999", side_sfs, num));
}

TEST(LR2021SideDetectors, AcceptsTwoDetectorsAtPrimarySf10) {
  const uint8_t valid[] = {11, 12};
  EXPECT_TRUE(mesh::lr2021::validateSideDetectorSFs(valid, 2, 10, 62.5f));

  const uint8_t too_many[] = {10, 11, 12};
  EXPECT_FALSE(mesh::lr2021::validateSideDetectorSFs(too_many, 3, 9, 812.5f));
}

TEST(LR2021SideDetectors, RejectsInvalidRxRelationships) {
  const uint8_t duplicate[] = {9, 9};
  EXPECT_FALSE(mesh::lr2021::validateSideDetectorSFs(duplicate, 2, 8, 62.5f));

  const uint8_t below_primary[] = {8};
  EXPECT_FALSE(mesh::lr2021::validateSideDetectorSFs(below_primary, 1, 8, 62.5f));

  const uint8_t excessive_span[] = {12};
  EXPECT_FALSE(mesh::lr2021::validateSideDetectorSFs(excessive_span, 1, 7, 62.5f));
}

TEST(LR2021SideDetectors, RecomputesLdroWhenBandwidthChanges) {
  EXPECT_TRUE(mesh::lr2021::sideDetectorLDRO(10, 62.5f));
  EXPECT_FALSE(mesh::lr2021::sideDetectorLDRO(10, 125.0f));
}

TEST(LR2021SideDetectors, RequiresStoredTerminator) {
  uint8_t count = 0;
  const uint8_t valid[] = {9, 10, 11, 0};
  EXPECT_TRUE(mesh::lr2021::storedSideDetectorCount(valid, count));
  EXPECT_EQ(3, count);

  const uint8_t torn[] = {9, 10, 11, 12};
  EXPECT_FALSE(mesh::lr2021::storedSideDetectorCount(torn, count));
}

int main(int argc, char** argv) {
  ::testing::InitGoogleTest(&argc, argv);
  return RUN_ALL_TESTS();
}
