#include <gtest/gtest.h>

#include <helpers/RegionNameUtils.h>

TEST(RegionNameUtils, PublicMarkerDoesNotCreateADistinctName) {
  EXPECT_TRUE(RegionNameUtils::equivalent("sea", "#sea"));
  EXPECT_TRUE(RegionNameUtils::equivalent("#sea", "sea"));
  EXPECT_TRUE(RegionNameUtils::equivalent("#sea", "#sea"));
}

TEST(RegionNameUtils, DifferentAndPrivateNamesRemainDistinct) {
  EXPECT_FALSE(RegionNameUtils::equivalent("sea", "w-wa"));
  EXPECT_FALSE(RegionNameUtils::equivalent("sea", "$sea"));
  EXPECT_FALSE(RegionNameUtils::equivalent(nullptr, "sea"));
}

int main(int argc, char** argv) {
  ::testing::InitGoogleTest(&argc, argv);
  return RUN_ALL_TESTS();
}
