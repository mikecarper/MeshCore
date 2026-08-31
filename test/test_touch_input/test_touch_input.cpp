#include <gtest/gtest.h>

#include <helpers/ui/TouchInput.h>

using mesh::ui::TouchAction;
using mesh::ui::TouchInput;
using mesh::ui::TouchSplitSelector;

namespace {

TouchAction release(TouchInput& input, int width = 137, int height = 137) {
  EXPECT_EQ(input.update(false, -1, -1, width, height), TouchAction::None);
  return input.update(false, -1, -1, width, height);
}

}  // namespace

TEST(TouchInput, EmitsOnlyAfterRelease) {
  TouchInput input;
  EXPECT_EQ(input.update(true, 70, 50, 137, 137), TouchAction::None);
  EXPECT_EQ(input.update(true, 72, 51, 137, 137), TouchAction::None);
  EXPECT_EQ(release(input), TouchAction::Select);
  EXPECT_EQ(input.update(false, -1, -1, 137, 137), TouchAction::None);
}

TEST(TouchInput, MapsTapZonesToAllActions) {
  TouchInput input;

  input.update(true, 10, 60, 137, 137);
  input.update(true, 10, 60, 137, 137);
  EXPECT_EQ(release(input), TouchAction::Previous);

  input.update(true, 68, 60, 137, 137);
  input.update(true, 68, 60, 137, 137);
  EXPECT_EQ(release(input), TouchAction::Select);

  input.update(true, 125, 60, 137, 137);
  input.update(true, 125, 60, 137, 137);
  EXPECT_EQ(release(input), TouchAction::Next);
}

TEST(TouchInput, CanUseAWiderCenterTapZone) {
  TouchInput input(false, false, 70);

  input.update(true, 25, 60, 137, 137);
  input.update(true, 25, 60, 137, 137);
  EXPECT_EQ(release(input), TouchAction::Select);

  input.update(true, 10, 60, 137, 137);
  input.update(true, 10, 60, 137, 137);
  EXPECT_EQ(release(input), TouchAction::Previous);

  input.update(true, 127, 60, 137, 137);
  input.update(true, 127, 60, 137, 137);
  EXPECT_EQ(release(input), TouchAction::Next);
}

TEST(TouchInput, MapsHorizontalSwipesToPageNavigation) {
  TouchInput input;

  input.update(true, 110, 60, 137, 137);
  input.update(true, 40, 62, 137, 137);
  EXPECT_EQ(release(input), TouchAction::Next);

  input.update(true, 30, 60, 137, 137);
  input.update(true, 105, 58, 137, 137);
  EXPECT_EQ(release(input), TouchAction::Previous);
}

TEST(TouchInput, MapsVerticalSwipesToPageNavigation) {
  TouchInput input;

  input.update(true, 60, 110, 137, 137);
  input.update(true, 62, 35, 137, 137);
  EXPECT_EQ(release(input), TouchAction::Next);

  input.update(true, 60, 25, 137, 137);
  input.update(true, 58, 105, 137, 137);
  EXPECT_EQ(release(input), TouchAction::Previous);
}

TEST(TouchInput, CanRouteVerticalSwipesToASeparateSelector) {
  TouchInput input(true, true);

  input.update(true, 60, 110, 137, 137);
  input.update(true, 62, 35, 137, 137);
  EXPECT_EQ(release(input), TouchAction::VerticalPrevious);

  input.update(true, 60, 25, 137, 137);
  input.update(true, 58, 105, 137, 137);
  EXPECT_EQ(release(input), TouchAction::VerticalNext);

  input.update(true, 110, 60, 137, 137);
  input.update(true, 40, 62, 137, 137);
  EXPECT_EQ(release(input), TouchAction::Previous);
}

TEST(TouchInput, BottomSelectorHasLargeArrowTapTargets) {
  TouchInput input(true, true, 70);

  input.update(true, 15, 125, 137, 137, true);
  input.update(true, 15, 125, 137, 137, true);
  EXPECT_EQ(input.update(false, -1, -1, 137, 137, true),
            TouchAction::None);
  EXPECT_EQ(input.update(false, -1, -1, 137, 137, true),
            TouchAction::VerticalPrevious);

  input.update(true, 122, 125, 137, 137, true);
  input.update(true, 122, 125, 137, 137, true);
  EXPECT_EQ(input.update(false, -1, -1, 137, 137, true),
            TouchAction::None);
  EXPECT_EQ(input.update(false, -1, -1, 137, 137, true),
            TouchAction::VerticalNext);

  input.update(true, 68, 125, 137, 137, true);
  input.update(true, 68, 125, 137, 137, true);
  EXPECT_EQ(input.update(false, -1, -1, 137, 137, true),
            TouchAction::None);
  EXPECT_EQ(input.update(false, -1, -1, 137, 137, true),
            TouchAction::None);
}

TEST(TouchInput, SplitSelectorMapsTapHalvesToExplicitChoices) {
  TouchInput input(true, true, 70);
  const TouchSplitSelector selector{8, 68, 84, 68, 38, 72};

  input.update(true, 30, 70, 160, 160, false, &selector);
  input.update(true, 31, 70, 160, 160, false, &selector);
  EXPECT_EQ(input.update(false, -1, -1, 160, 160, false, &selector),
            TouchAction::None);
  EXPECT_EQ(input.update(false, -1, -1, 160, 160, false, &selector),
            TouchAction::SelectLeft);

  input.update(true, 130, 70, 160, 160, false, &selector);
  input.update(true, 129, 70, 160, 160, false, &selector);
  EXPECT_EQ(input.update(false, -1, -1, 160, 160, false, &selector),
            TouchAction::None);
  EXPECT_EQ(input.update(false, -1, -1, 160, 160, false, &selector),
            TouchAction::SelectRight);

  input.update(true, 80, 70, 160, 160, false, &selector);
  input.update(true, 80, 70, 160, 160, false, &selector);
  EXPECT_EQ(input.update(false, -1, -1, 160, 160, false, &selector),
            TouchAction::None);
  EXPECT_EQ(input.update(false, -1, -1, 160, 160, false, &selector),
            TouchAction::None);

  input.update(true, 30, 20, 160, 160, false, &selector);
  input.update(true, 30, 20, 160, 160, false, &selector);
  EXPECT_EQ(input.update(false, -1, -1, 160, 160, false, &selector),
            TouchAction::None);
  EXPECT_EQ(input.update(false, -1, -1, 160, 160, false, &selector),
            TouchAction::None);
}

TEST(TouchInput, SplitSelectorAcceptsQuickBoundedTapsOnly) {
  TouchInput input(true, true, 70);
  const TouchSplitSelector selector{2, 76, 82, 76, 36, 98};

  input.update(true, 30, 100, 160, 160, false, &selector);
  EXPECT_EQ(input.update(false, -1, -1, 160, 160, false, &selector),
            TouchAction::None);
  EXPECT_EQ(input.update(false, -1, -1, 160, 160, false, &selector),
            TouchAction::SelectLeft);

  input.update(true, 130, 100, 160, 160, false, &selector);
  EXPECT_EQ(input.update(false, -1, -1, 160, 160, false, &selector),
            TouchAction::None);
  EXPECT_EQ(input.update(false, -1, -1, 160, 160, false, &selector),
            TouchAction::SelectRight);

  input.update(true, 80, 100, 160, 160, false, &selector);
  EXPECT_EQ(input.update(false, -1, -1, 160, 160, false, &selector),
            TouchAction::None);
  EXPECT_EQ(input.update(false, -1, -1, 160, 160, false, &selector),
            TouchAction::None);

  input.update(true, 30, 100, 160, 160);
  EXPECT_EQ(release(input, 160, 160), TouchAction::None);
}

TEST(TouchInput, SplitSelectorKeepsSwipesAsNavigation) {
  TouchInput input(true, true, 70);
  const TouchSplitSelector selector{8, 68, 84, 68, 38, 72};

  input.update(true, 130, 70, 160, 160, false, &selector);
  input.update(true, 30, 72, 160, 160, false, &selector);
  EXPECT_EQ(input.update(false, -1, -1, 160, 160, false, &selector),
            TouchAction::None);
  EXPECT_EQ(input.update(false, -1, -1, 160, 160, false, &selector),
            TouchAction::Previous);

  input.update(true, 30, 70, 160, 160, false, &selector);
  input.update(true, 130, 68, 160, 160, false, &selector);
  EXPECT_EQ(input.update(false, -1, -1, 160, 160, false, &selector),
            TouchAction::None);
  EXPECT_EQ(input.update(false, -1, -1, 160, 160, false, &selector),
            TouchAction::Next);
}

TEST(TouchInput, CanReverseSwipesWithoutReversingTapZones) {
  TouchInput input(true);

  input.update(true, 110, 60, 137, 137);
  input.update(true, 40, 62, 137, 137);
  EXPECT_EQ(release(input), TouchAction::Previous);

  input.update(true, 30, 60, 137, 137);
  input.update(true, 105, 58, 137, 137);
  EXPECT_EQ(release(input), TouchAction::Next);

  input.update(true, 10, 60, 137, 137);
  input.update(true, 10, 60, 137, 137);
  EXPECT_EQ(release(input), TouchAction::Previous);

  input.update(true, 125, 60, 137, 137);
  input.update(true, 125, 60, 137, 137);
  EXPECT_EQ(release(input), TouchAction::Next);
}

TEST(TouchInput, KeepsGestureAcrossOneMissingTouchSample) {
  TouchInput input;

  input.update(true, 110, 60, 137, 137);
  input.update(true, 80, 61, 137, 137);
  EXPECT_EQ(input.update(false, -1, -1, 137, 137), TouchAction::None);
  input.update(true, 40, 62, 137, 137);
  EXPECT_EQ(release(input), TouchAction::Next);
}

TEST(TouchInput, IgnoresContactWithNoDirectionSample) {
  TouchInput input;

  input.update(true, 120, 60, 137, 137);
  EXPECT_EQ(release(input), TouchAction::None);
}

TEST(TouchInput, UsesStartZoneForShortAmbiguousMovement) {
  TouchInput input(true);

  input.update(true, 40, 60, 137, 137);
  input.update(true, 52, 60, 137, 137);
  EXPECT_EQ(release(input), TouchAction::Previous);
}

TEST(TouchInput, RepeatedSwipesKeepTheSameDirection) {
  TouchInput input(true);

  for (int attempt = 0; attempt < 10; ++attempt) {
    input.update(true, 110, 60, 137, 137);
    input.update(true, 72, 61, 137, 137);
    input.update(true, 35, 62, 137, 137);
    EXPECT_EQ(release(input), TouchAction::Previous);
  }
}

int main(int argc, char** argv) {
  ::testing::InitGoogleTest(&argc, argv);
  return RUN_ALL_TESTS();
}
