#include <Arduino.h>
#include <gtest/gtest.h>

#include "helpers/ui/MomentaryButton.h"

namespace {

constexpr uint8_t kPin = 7;

void advance(uint32_t millis_delta) {
  g_mock_millis += millis_delta;
}

int beginTransition(MomentaryButton& button, uint8_t level) {
  g_mock_pin_levels[kPin] = level;
  return button.check();
}

int finishDebounce(MomentaryButton& button) {
  advance(25);
  return button.check();
}

void cleanPress(MomentaryButton& button) {
  EXPECT_EQ(BUTTON_EVENT_NONE, beginTransition(button, LOW));
  EXPECT_TRUE(button.needsPolling());
  EXPECT_EQ(BUTTON_EVENT_NONE, finishDebounce(button));
  EXPECT_TRUE(button.needsPolling());
}

int cleanRelease(MomentaryButton& button) {
  EXPECT_EQ(BUTTON_EVENT_NONE, beginTransition(button, HIGH));
  EXPECT_TRUE(button.needsPolling());
  return finishDebounce(button);
}

class MomentaryButtonTest : public testing::Test {
 protected:
  void SetUp() override {
    resetArduinoMock();
    g_mock_millis = 100;
    g_mock_pin_levels[kPin] = HIGH;
  }
};

TEST_F(MomentaryButtonTest, CleanSingleWaitsForSlidingWindowAndEmitsOnce) {
  MomentaryButton button(kPin, 1000, true, true, true);
  button.begin();

  cleanPress(button);
  EXPECT_EQ(BUTTON_EVENT_NONE, cleanRelease(button));
  EXPECT_TRUE(button.needsPolling());

  advance(279);
  EXPECT_EQ(BUTTON_EVENT_NONE, button.check());
  advance(1);
  EXPECT_EQ(BUTTON_EVENT_CLICK, button.check());
  EXPECT_FALSE(button.needsPolling());
  EXPECT_EQ(BUTTON_EVENT_NONE, button.check());
}

TEST_F(MomentaryButtonTest, ReleaseBounceStillProducesOneSingleClick) {
  MomentaryButton button(kPin, 1000, true, true, true);
  button.begin();
  cleanPress(button);

  EXPECT_EQ(BUTTON_EVENT_NONE, beginTransition(button, HIGH));
  advance(10);
  g_mock_pin_levels[kPin] = LOW;
  EXPECT_EQ(BUTTON_EVENT_NONE, button.check());
  advance(10);
  EXPECT_EQ(BUTTON_EVENT_NONE, beginTransition(button, HIGH));
  EXPECT_EQ(BUTTON_EVENT_NONE, finishDebounce(button));

  advance(280);
  EXPECT_EQ(BUTTON_EVENT_CLICK, button.check());
  EXPECT_FALSE(button.needsPolling());
  EXPECT_EQ(BUTTON_EVENT_NONE, button.check());
}

TEST_F(MomentaryButtonTest, TwoCleanClicksProduceDoubleClick) {
  MomentaryButton button(kPin, 1000, true, true, true);
  button.begin();

  cleanPress(button);
  EXPECT_EQ(BUTTON_EVENT_NONE, cleanRelease(button));
  advance(100);
  cleanPress(button);
  EXPECT_EQ(BUTTON_EVENT_NONE, cleanRelease(button));
  advance(280);
  EXPECT_EQ(BUTTON_EVENT_DOUBLE_CLICK, button.check());
  EXPECT_FALSE(button.needsPolling());
}

TEST_F(MomentaryButtonTest, ThreeCleanClicksProduceTripleClick) {
  MomentaryButton button(kPin, 1000, true, true, true);
  button.begin();

  cleanPress(button);
  EXPECT_EQ(BUTTON_EVENT_NONE, cleanRelease(button));
  advance(80);
  cleanPress(button);
  EXPECT_EQ(BUTTON_EVENT_NONE, cleanRelease(button));
  advance(80);
  cleanPress(button);
  EXPECT_EQ(BUTTON_EVENT_NONE, cleanRelease(button));
  advance(280);
  EXPECT_EQ(BUTTON_EVENT_TRIPLE_CLICK, button.check());
  EXPECT_FALSE(button.needsPolling());
}

TEST_F(MomentaryButtonTest, MulticlickDisabledEmitsOnAcceptedRelease) {
  MomentaryButton button(kPin, 1000, true, true, false);
  button.begin();

  cleanPress(button);
  EXPECT_EQ(BUTTON_EVENT_CLICK, cleanRelease(button));
  EXPECT_FALSE(button.needsPolling());
}

TEST_F(MomentaryButtonTest, LongPressEmitsOnceAndDoesNotBecomeClick) {
  MomentaryButton button(kPin, 1000, true, true, true);
  button.begin();
  cleanPress(button);

  advance(999);
  EXPECT_EQ(BUTTON_EVENT_NONE, button.check());
  advance(1);
  EXPECT_EQ(BUTTON_EVENT_LONG_PRESS, button.check());
  EXPECT_FALSE(button.needsPolling());
  EXPECT_EQ(BUTTON_EVENT_NONE, cleanRelease(button));
  advance(280);
  EXPECT_EQ(BUTTON_EVENT_NONE, button.check());
}

TEST_F(MomentaryButtonTest, ReleaseDebounceCanCrossLongPressBoundary) {
  MomentaryButton button(kPin, 1000, true, true, true);
  button.begin();
  cleanPress(button);

  advance(990);
  EXPECT_EQ(BUTTON_EVENT_NONE, beginTransition(button, HIGH));
  EXPECT_EQ(BUTTON_EVENT_LONG_PRESS, finishDebounce(button));
  EXPECT_FALSE(button.needsPolling());
  advance(280);
  EXPECT_EQ(BUTTON_EVENT_NONE, button.check());
}

TEST_F(MomentaryButtonTest, ClickThenHoldKeepsReleaseDebounceAlive) {
  MomentaryButton button(kPin, 1000, true, true, true);
  button.begin();

  cleanPress(button);
  EXPECT_EQ(BUTTON_EVENT_NONE, cleanRelease(button));
  advance(100);
  cleanPress(button);

  advance(1000);
  EXPECT_EQ(BUTTON_EVENT_NONE, beginTransition(button, HIGH));
  EXPECT_TRUE(button.needsPolling());
  EXPECT_EQ(BUTTON_EVENT_NONE, finishDebounce(button));
  EXPECT_FALSE(button.needsPolling());
  advance(280);
  EXPECT_EQ(BUTTON_EVENT_NONE, button.check());
}

TEST_F(MomentaryButtonTest, CancelSuppressesImmediateClickMode) {
  MomentaryButton button(kPin, 0, true, true, false);
  button.begin();
  cleanPress(button);

  button.cancelClick();
  EXPECT_EQ(BUTTON_EVENT_NONE, cleanRelease(button));
  EXPECT_FALSE(button.needsPolling());
  EXPECT_EQ(BUTTON_EVENT_NONE, button.check());
}

TEST_F(MomentaryButtonTest, PressAcceptedAtMillisZeroIsNotLost) {
  MomentaryButton button(kPin, 1000, true, true, true);
  button.begin();
  g_mock_millis = UINT32_MAX - 24;

  cleanPress(button);
  EXPECT_EQ(0U, g_mock_millis);
  EXPECT_TRUE(button.needsPolling());
  EXPECT_EQ(BUTTON_EVENT_NONE, cleanRelease(button));
  advance(280);
  EXPECT_EQ(BUTTON_EVENT_CLICK, button.check());
  EXPECT_FALSE(button.needsPolling());
}

TEST_F(MomentaryButtonTest, ClickDeadlineSurvivesMillisRollover) {
  MomentaryButton button(kPin, 1000, true, true, true);
  button.begin();
  g_mock_millis = UINT32_MAX - 100;

  cleanPress(button);
  EXPECT_EQ(BUTTON_EVENT_NONE, cleanRelease(button));
  advance(279);
  EXPECT_EQ(BUTTON_EVENT_NONE, button.check());
  advance(1);
  EXPECT_EQ(BUTTON_EVENT_CLICK, button.check());
  EXPECT_FALSE(button.needsPolling());
}

TEST_F(MomentaryButtonTest, PressBeforeWindowMayFinishDebouncingAfterDeadline) {
  MomentaryButton button(kPin, 1000, true, true, true);
  button.begin();

  cleanPress(button);
  EXPECT_EQ(BUTTON_EVENT_NONE, cleanRelease(button));
  advance(279);
  EXPECT_EQ(BUTTON_EVENT_NONE, beginTransition(button, LOW));
  EXPECT_EQ(BUTTON_EVENT_NONE, finishDebounce(button));
  EXPECT_EQ(BUTTON_EVENT_NONE, cleanRelease(button));
  advance(280);
  EXPECT_EQ(BUTTON_EVENT_DOUBLE_CLICK, button.check());
  EXPECT_FALSE(button.needsPolling());
}

TEST_F(MomentaryButtonTest, PressAtExpiredWindowStartsNewClickSequence) {
  MomentaryButton button(kPin, 1000, true, true, true);
  button.begin();

  cleanPress(button);
  EXPECT_EQ(BUTTON_EVENT_NONE, cleanRelease(button));
  advance(280);
  EXPECT_EQ(BUTTON_EVENT_CLICK, beginTransition(button, LOW));
  EXPECT_TRUE(button.needsPolling());
  EXPECT_EQ(BUTTON_EVENT_NONE, finishDebounce(button));
  EXPECT_EQ(BUTTON_EVENT_NONE, cleanRelease(button));
  advance(280);
  EXPECT_EQ(BUTTON_EVENT_CLICK, button.check());
  EXPECT_FALSE(button.needsPolling());
}

}  // namespace

int main(int argc, char** argv) {
  ::testing::InitGoogleTest(&argc, argv);
  return RUN_ALL_TESTS();
}
