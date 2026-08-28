#include <gtest/gtest.h>

#include <helpers/sensors/GpsTimeValidation.h>

TEST(GpsTimeValidation, AcceptsCompleteUtcDatesInSignedTimestampRange) {
  EXPECT_TRUE(mesh::gps::isValidNmeaDateTime(2020, 1, 1, 0, 0, 0));
  EXPECT_TRUE(mesh::gps::isValidNmeaDateTime(2024, 2, 29, 23, 59, 59));
  EXPECT_TRUE(mesh::gps::isValidNmeaDateTime(2038, 1, 19, 3, 14, 7));
}

TEST(GpsTimeValidation, RejectsZeroPartialAndOutOfRangeDates) {
  EXPECT_FALSE(mesh::gps::isValidNmeaDateTime(0, 0, 0, 0, 0, 0));
  EXPECT_FALSE(mesh::gps::isValidNmeaDateTime(2019, 12, 31, 23, 59, 59));
  EXPECT_FALSE(mesh::gps::isValidNmeaDateTime(2038, 1, 19, 3, 14, 8));
  EXPECT_FALSE(mesh::gps::isValidNmeaDateTime(2038, 1, 20, 0, 0, 0));
  EXPECT_FALSE(mesh::gps::isValidNmeaDateTime(2039, 1, 1, 0, 0, 0));
  EXPECT_FALSE(mesh::gps::isValidNmeaDateTime(2026, 13, 1, 0, 0, 0));
  EXPECT_FALSE(mesh::gps::isValidNmeaDateTime(2026, 1, 1, 24, 0, 0));
  EXPECT_FALSE(mesh::gps::isValidNmeaDateTime(2026, 1, 1, 0, 60, 0));
  EXPECT_FALSE(mesh::gps::isValidNmeaDateTime(2026, 1, 1, 0, 0, 60));
}

TEST(GpsTimeValidation, EnforcesCalendarDayBounds) {
  EXPECT_FALSE(mesh::gps::isValidNmeaDateTime(2023, 2, 29, 12, 0, 0));
  EXPECT_TRUE(mesh::gps::isValidNmeaDateTime(2024, 2, 29, 12, 0, 0));
  EXPECT_FALSE(mesh::gps::isValidNmeaDateTime(2026, 4, 31, 12, 0, 0));
  EXPECT_TRUE(mesh::gps::isValidNmeaDateTime(2026, 4, 30, 12, 0, 0));
}

int main(int argc, char** argv) {
  ::testing::InitGoogleTest(&argc, argv);
  return RUN_ALL_TESTS();
}
