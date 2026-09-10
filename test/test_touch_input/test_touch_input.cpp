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

TEST(TouchInput, ReaderFooterHasFiveFullWidthTargetsIncludingExit) {
  using mesh::ui::TouchNavigationBar;
  // Same logical positions on the Indicator's 320 and 480 rendering profiles.
  for (int width : {160, 320, 480}) {
    for (bool mirrored : {false, true}) {
      TouchInput input(true, true, 70, mirrored);
      TouchNavigationBar bar;
      bar.top = width - 24;
      bar.height = 24;
      bar.exit_height = 12;
      const TouchAction expected[] = {TouchAction::VerticalPrevious,
          TouchAction::Previous, TouchAction::Next,
          TouchAction::VerticalNext, TouchAction::Select};
      // Every point of each cell is usable, not only the arrow glyph.
      for (int visual_x = 0; visual_x < width; ++visual_x) {
        const int x = mirrored ? width - 1 - visual_x : visual_x;
        input.update(true, x, bar.top, width, width, true, nullptr, &bar);
        EXPECT_EQ(input.update(false, -1, -1, width, width, true, nullptr, &bar), TouchAction::None);
        EXPECT_EQ(input.update(false, -1, -1, width, width, true, nullptr, &bar),
                  expected[visual_x / (width / 5)]);
        EXPECT_EQ(input.update(false, -1, -1, width, width, true, nullptr, &bar), TouchAction::None);
      }
      // The top header is an explicit exit target across its entire width.
      for (int visual_x : {0, width / 2, width - 1}) {
        const int x = mirrored ? width - 1 - visual_x : visual_x;
        input.update(true, x, 0, width, width, true, nullptr, &bar);
        input.update(false, -1, -1, width, width, true, nullptr, &bar);
        EXPECT_EQ(input.update(false, -1, -1, width, width, true, nullptr, &bar),
            TouchAction::Select);
      }
    }
  }
}

TEST(TouchInput, ReaderFooterDragDoesNotActivateExitOrEmitAnEndpointTap) {
  mesh::ui::TouchNavigationBar bar;
  bar.top = 130; bar.height = 16;
  TouchInput input(true, true, 70, true);
  input.update(true, 10, 135, 160, 160, true, nullptr, &bar);
  input.update(true, 90, 135, 160, 160, true, nullptr, &bar);
  EXPECT_EQ(input.update(false, -1, -1, 160, 160, true, nullptr, &bar), TouchAction::None);
  // A transient lost sample cannot end the swipe.
  input.update(true, 120, 135, 160, 160, true, nullptr, &bar);
  input.update(false, -1, -1, 160, 160, true, nullptr, &bar);
  EXPECT_EQ(input.update(false, -1, -1, 160, 160, true, nullptr, &bar), TouchAction::None);
  EXPECT_EQ(input.update(false, -1, -1, 160, 160, true, nullptr, &bar), TouchAction::None);
  // A one-sample contact away from the new targets is still ignored.
  input.update(true, 80, 50, 160, 160, false, nullptr, &bar);
  input.update(false, -1, -1, 160, 160, false, nullptr, &bar);
  EXPECT_EQ(input.update(false, -1, -1, 160, 160, false, nullptr, &bar), TouchAction::None);
}

TEST(TouchInput, RemovedChannelBarDoesNotLeaveInvisibleSelectorAboveNavigation) {
  mesh::ui::TouchNavigationBar bar;
  bar.top = 144; bar.height = 16;
  TouchInput input(true, true, 70, true);
  auto tap = [&](int visual_x, int y) {
    const int raw_x = 159 - visual_x;
    input.update(true, raw_x, y, 160, 160, false, nullptr, &bar);
    input.update(true, raw_x, y, 160, 160, false, nullptr, &bar);
    input.update(false, -1, -1, 160, 160, false, nullptr, &bar);
    return input.update(false, -1, -1, 160, 160, false, nullptr, &bar);
  };
  EXPECT_EQ(tap(0, 135), TouchAction::Previous);
  EXPECT_EQ(tap(159, 135), TouchAction::Next);
  EXPECT_EQ(tap(80, 135), TouchAction::Next);
  EXPECT_EQ(tap(0, 144), TouchAction::VerticalPrevious);
  EXPECT_EQ(tap(159, 159), TouchAction::Select); // X now owns the bottom-right.
}

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

TEST(TouchInput, CanMirrorStationaryTapZonesIndependentlyOfSwipes) {
  TouchInput input(true, true, 70, true);
  const TouchSplitSelector selector{2, 76, 82, 76, 40, 100};

  // The controller's raw-right coordinate is the visual-left WiFi box.
  input.update(true, 129, 80, 160, 160, false, &selector);
  EXPECT_EQ(input.update(false, -1, -1, 160, 160, false, &selector),
            TouchAction::None);
  EXPECT_EQ(input.update(false, -1, -1, 160, 160, false, &selector),
            TouchAction::SelectLeft);

  // The controller's raw-left coordinate is the visual-right BLE box.
  input.update(true, 30, 80, 160, 160, false, &selector);
  input.update(true, 31, 80, 160, 160, false, &selector);
  EXPECT_EQ(input.update(false, -1, -1, 160, 160, false, &selector),
            TouchAction::None);
  EXPECT_EQ(input.update(false, -1, -1, 160, 160, false, &selector),
            TouchAction::SelectRight);

  // Generic side taps and the message footer arrows use the same visual-X
  // correction, while their center zones stay centered.
  input.update(true, 150, 60, 160, 160);
  input.update(true, 150, 60, 160, 160);
  EXPECT_EQ(release(input, 160, 160), TouchAction::Previous);

  input.update(true, 10, 140, 160, 160, true);
  input.update(true, 10, 140, 160, 160, true);
  EXPECT_EQ(input.update(false, -1, -1, 160, 160, true),
            TouchAction::None);
  EXPECT_EQ(input.update(false, -1, -1, 160, 160, true),
            TouchAction::VerticalNext);

  // Swipe direction remains governed only by reverse_swipes.
  input.update(true, 130, 70, 160, 160, false, &selector);
  input.update(true, 30, 72, 160, 160, false, &selector);
  EXPECT_EQ(input.update(false, -1, -1, 160, 160, false, &selector),
            TouchAction::None);
  EXPECT_EQ(input.update(false, -1, -1, 160, 160, false, &selector),
            TouchAction::Previous);
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
