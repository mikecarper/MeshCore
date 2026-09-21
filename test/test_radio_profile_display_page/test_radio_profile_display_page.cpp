#include <helpers/ui/RadioProfileDisplayPage.h>

#include <gtest/gtest.h>

TEST(RadioProfileDisplayPage, AlternatesEverySevenSecondsOnlyWhenEnabled) {
  using mesh::ui::RADIO_PROFILE_DISPLAY_PAGE_MILLIS;
  using mesh::ui::showSecondaryRadioProfilePage;

  EXPECT_FALSE(showSecondaryRadioProfilePage(false, RADIO_PROFILE_DISPLAY_PAGE_MILLIS));
  EXPECT_FALSE(showSecondaryRadioProfilePage(true, 0));
  EXPECT_FALSE(showSecondaryRadioProfilePage(true, RADIO_PROFILE_DISPLAY_PAGE_MILLIS - 1));
  EXPECT_TRUE(showSecondaryRadioProfilePage(true, RADIO_PROFILE_DISPLAY_PAGE_MILLIS));
  EXPECT_TRUE(showSecondaryRadioProfilePage(true, 2 * RADIO_PROFILE_DISPLAY_PAGE_MILLIS - 1));
  EXPECT_FALSE(showSecondaryRadioProfilePage(true, 2 * RADIO_PROFILE_DISPLAY_PAGE_MILLIS));
}

TEST(RadioProfileDisplayPage, LabelsTemporaryAndSavedProfilesClearly) {
  EXPECT_STREQ("R1", mesh::ui::radioProfileDisplayTag(false, false));
  EXPECT_STREQ("T1", mesh::ui::radioProfileDisplayTag(false, true));
  EXPECT_STREQ("R2", mesh::ui::radioProfileDisplayTag(true, false));
  EXPECT_STREQ("T2", mesh::ui::radioProfileDisplayTag(true, true));
}

TEST(RadioProfileDisplayPage, PlacesSystemStatusAfterEachRadioProfile) {
  using mesh::ui::RADIO_PROFILE_DISPLAY_PAGE_MILLIS;
  using mesh::ui::radioProfileDisplayPageIndex;
  using mesh::ui::showRadioProfileSystemStatusPage;
  using mesh::ui::showSecondaryRadioProfilePage;

  // Dual-radio: R1, R2, system status, then back to R1.
  EXPECT_EQ(0, radioProfileDisplayPageIndex(true, 1, 0));
  EXPECT_EQ(1, radioProfileDisplayPageIndex(true, 1,
      RADIO_PROFILE_DISPLAY_PAGE_MILLIS));
  EXPECT_EQ(2, radioProfileDisplayPageIndex(true, 1,
      2 * RADIO_PROFILE_DISPLAY_PAGE_MILLIS));
  EXPECT_TRUE(showSecondaryRadioProfilePage(true, 1,
      RADIO_PROFILE_DISPLAY_PAGE_MILLIS));
  uint8_t status_page = 99;
  EXPECT_TRUE(showRadioProfileSystemStatusPage(true, 1,
      2 * RADIO_PROFILE_DISPLAY_PAGE_MILLIS, &status_page));
  EXPECT_EQ(0, status_page);

  // A very short display gets each status sub-page without losing either
  // radio profile from the cycle.
  EXPECT_TRUE(showRadioProfileSystemStatusPage(true, 2,
      3 * RADIO_PROFILE_DISPLAY_PAGE_MILLIS, &status_page));
  EXPECT_EQ(1, status_page);
  EXPECT_FALSE(showRadioProfileSystemStatusPage(true, 2,
      4 * RADIO_PROFILE_DISPLAY_PAGE_MILLIS, &status_page));
}

TEST(RadioProfileDisplayPage, ManualPagesDoNotDependOnElapsedTime) {
  using mesh::ui::radioProfileManualPageIndex;
  using mesh::ui::showRadioProfileSystemStatusPage;
  using mesh::ui::showSecondaryRadioProfilePage;

  // A repeater begins on R1 and only changes page when its button handler
  // advances this selected-page value.
  EXPECT_FALSE(mesh::ui::showManualSecondaryRadioProfilePage(true,
      static_cast<uint8_t>(0)));
  EXPECT_TRUE(mesh::ui::showManualSecondaryRadioProfilePage(true,
      static_cast<uint8_t>(1)));
  EXPECT_EQ(0, radioProfileManualPageIndex(true, 1, 3));

  uint8_t status_page = 99;
  EXPECT_TRUE(mesh::ui::showManualRadioProfileSystemStatusPage(true, 1,
      static_cast<uint8_t>(2), &status_page));
  EXPECT_EQ(0, status_page);
}

int main(int argc, char** argv) {
  ::testing::InitGoogleTest(&argc, argv);
  return RUN_ALL_TESTS();
}
