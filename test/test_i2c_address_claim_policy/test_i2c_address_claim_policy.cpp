#include <gtest/gtest.h>

#include <cstdio>
#include <string>

#include "helpers/sensors/I2CAddressClaimPolicy.h"
#include "helpers/sensors/NmeaSentenceProbe.h"

static std::string nmeaSentence(const char* body) {
  uint8_t checksum = 0;
  for (const char* cursor = body; *cursor; ++cursor) {
    checksum ^= static_cast<uint8_t>(*cursor);
  }
  char suffix[8];
  std::snprintf(suffix, sizeof(suffix), "*%02X\r\n", checksum);
  return std::string("$") + body + suffix;
}

static bool feed(mesh::NmeaSentenceProbe& probe, const std::string& bytes) {
  bool found = false;
  for (unsigned char byte : bytes) found = probe.ingest(byte) || found;
  return found;
}

TEST(I2CAddressClaimPolicy, SkipsOnlyTheAddressClaimedByGps) {
  EXPECT_TRUE(mesh::shouldSkipSensorAtClaimedGpsAddress(
      true, true, 0x42, 0x42));
  EXPECT_FALSE(mesh::shouldSkipSensorAtClaimedGpsAddress(
      true, true, 0x43, 0x42));
}

TEST(I2CAddressClaimPolicy, FailedOrAbsentGpsLeavesSensorsAvailable) {
  EXPECT_FALSE(mesh::shouldSkipSensorAtClaimedGpsAddress(
      false, true, 0x42, 0x42));
}

TEST(I2CAddressClaimPolicy, SameNumericAddressOnAnotherBusIsIndependent) {
  EXPECT_FALSE(mesh::shouldSkipSensorAtClaimedGpsAddress(
      true, false, 0x42, 0x42));
}

TEST(I2CAddressClaimPolicy, GpsProbeFailsClosedOnInaOrIncompleteIdentity) {
  EXPECT_FALSE(mesh::shouldProbeI2cGps(
      mesh::I2cIdentityProbeResult::Match));
  EXPECT_FALSE(mesh::shouldProbeI2cGps(
      mesh::I2cIdentityProbeResult::Inconclusive));
  EXPECT_FALSE(mesh::shouldProbeI2cGps(
      mesh::I2cIdentityProbeResult::NoResponse));
  EXPECT_TRUE(mesh::shouldProbeI2cGps(
      mesh::I2cIdentityProbeResult::NotMatch));
}

TEST(I2CAddressClaimPolicy, RejectsReservedAndOutOfRangeAddresses) {
  EXPECT_FALSE(mesh::isValidI2cPeripheralAddress(0x00));
  EXPECT_FALSE(mesh::isValidI2cPeripheralAddress(0x07));
  EXPECT_TRUE(mesh::isValidI2cPeripheralAddress(0x08));
  EXPECT_TRUE(mesh::isValidI2cPeripheralAddress(0x42));
  EXPECT_TRUE(mesh::isValidI2cPeripheralAddress(0x77));
  EXPECT_FALSE(mesh::isValidI2cPeripheralAddress(0x78));
  EXPECT_FALSE(mesh::isValidI2cPeripheralAddress(0xFF));
  EXPECT_FALSE(mesh::isValidI2cPeripheralAddress(0x142));
  EXPECT_FALSE(mesh::isValidI2cPeripheralAddress(0x10042));
}

TEST(I2CAddressClaimPolicy, RejectsUnsafePinPairsBeforeGpioAccess) {
  EXPECT_TRUE(mesh::isValidI2cPinPair(8, 7, 23));
  EXPECT_FALSE(mesh::isValidI2cPinPair(-1, 7, 23));
  EXPECT_FALSE(mesh::isValidI2cPinPair(8, -1, 23));
  EXPECT_FALSE(mesh::isValidI2cPinPair(23, 7, 23));
  EXPECT_FALSE(mesh::isValidI2cPinPair(8, 23, 23));
  EXPECT_FALSE(mesh::isValidI2cPinPair(7, 7, 23));
}

TEST(I2CAddressClaimPolicy, ClassifiesInaIdentityWithoutAssumingDynamicBytesMatch) {
  using Status = mesh::I2cRegisterProbeStatus;
  using Result = mesh::I2cIdentityProbeResult;
  constexpr uint16_t kManufacturer = 0x5449;
  constexpr uint16_t kDie = 0x3220;

  EXPECT_EQ(mesh::classifyIna3221Identity(
                Status::Success, kManufacturer,
                Status::Success, kDie, kManufacturer, kDie),
            Result::Match);
  EXPECT_EQ(mesh::classifyIna3221Identity(
                Status::NoResponse, 0,
                Status::Inconclusive, 0, kManufacturer, kDie),
            Result::NoResponse);
  EXPECT_EQ(mesh::classifyIna3221Identity(
                Status::Success, kManufacturer,
                Status::Inconclusive, 0, kManufacturer, kDie),
            Result::Inconclusive);
  EXPECT_EQ(mesh::classifyIna3221Identity(
                Status::Success, kManufacturer,
                Status::Success, 0x1234, kManufacturer, kDie),
            Result::Inconclusive);
  EXPECT_EQ(mesh::classifyIna3221Identity(
                Status::Success, 0x0001,
                Status::Success, 0x0002, kManufacturer, kDie),
            Result::NotMatch);
  EXPECT_EQ(mesh::classifyIna3221Identity(
                Status::Success, 0x0001,
                Status::Success, kManufacturer, kManufacturer, kDie),
            Result::Inconclusive);
}

TEST(NmeaSentenceProbe, AcceptsChecksumValidGpsTalkersIncrementally) {
  mesh::NmeaSentenceProbe gps;
  EXPECT_TRUE(feed(gps, nmeaSentence("GNGGA,123519,,,,,0,00,99.99,,,,,,")));
  EXPECT_TRUE(gps.found());

  mesh::NmeaSentenceProbe beidou;
  EXPECT_TRUE(feed(beidou, nmeaSentence("BDRMC,123519,V,,,,,,,300826,,,N")));
}

TEST(NmeaSentenceProbe, RejectsNoiseBadChecksumsAndNonGpsTalkers) {
  mesh::NmeaSentenceProbe probe;
  EXPECT_FALSE(feed(probe, "noise\x00\xff$GNGGA,123*00\r\n"));
  EXPECT_FALSE(feed(probe, nmeaSentence("AAGGA,123519,,,,,0,00,99.99,,,,,,")));
  EXPECT_FALSE(probe.found());
}

TEST(NmeaSentenceProbe, NewDollarRestartsAnIncompleteSentence) {
  mesh::NmeaSentenceProbe probe;
  const std::string valid = nmeaSentence("GPGGA,123519,,,,,0,00,99.99,,,,,,");
  EXPECT_TRUE(feed(probe, "$GPGGA,broken" + valid));
}

int main(int argc, char** argv) {
  ::testing::InitGoogleTest(&argc, argv);
  return RUN_ALL_TESTS();
}
