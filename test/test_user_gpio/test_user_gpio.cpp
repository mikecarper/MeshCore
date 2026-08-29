#include <gtest/gtest.h>

#define P_LORA_LF_PA_POWER 4
#define P_LORA_HF_PA_POWER 3
#define ST7789_CS 16
#define ST7789_RS 15
#define USER_GPIO_RESERVED_PINS 40, 41

#include <Arduino.h>
#include <helpers/UserGpio.h>
#include <helpers/UserGpioPinPolicy.h>
#include <helpers/UserGpioReplyTracker.h>

#include <climits>
#include <cstring>

int main(int argc, char** argv) {
  ::testing::InitGoogleTest(&argc, argv);
  return RUN_ALL_TESTS();
}

class UserGpioTestBoard : public mesh::MainBoard {
public:
  bool available[64] = {};

  uint16_t getBattMilliVolts() override { return 0; }
  const char* getManufacturerName() const override { return "test"; }
  void reboot() override {}
  uint8_t getStartupReason() const override { return BD_STARTUP_NORMAL; }
  bool isUserGpioAvailable(uint8_t pin) const override {
    return pin < 64 && available[pin];
  }
};

class UserGpioTest : public ::testing::Test {
protected:
  UserGpioTestBoard board;
  char reply[160];

  void SetUp() override {
    resetArduinoMock();
    memset(reply, 0, sizeof(reply));
    board.available[4] = true;
    board.available[16] = true;
  }
};

TEST(UserGpioPinPolicyTest, ReservesRadioPaAndSt7789ControlAliases) {
  EXPECT_TRUE(UserGpioPinPolicy::isFirmwareReserved(P_LORA_LF_PA_POWER));
  EXPECT_TRUE(UserGpioPinPolicy::isFirmwareReserved(P_LORA_HF_PA_POWER));
  EXPECT_TRUE(UserGpioPinPolicy::isFirmwareReserved(ST7789_CS));
  EXPECT_TRUE(UserGpioPinPolicy::isFirmwareReserved(ST7789_RS));
  EXPECT_FALSE(UserGpioPinPolicy::isFirmwareReserved(22));
}

TEST(UserGpioPinPolicyTest, ReservesBoardSpecificDirectIoPins) {
  EXPECT_TRUE(UserGpioPinPolicy::isFirmwareReserved(40));
  EXPECT_TRUE(UserGpioPinPolicy::isFirmwareReserved(41));
  EXPECT_FALSE(UserGpioPinPolicy::isFirmwareReserved(39));
}

TEST_F(UserGpioTest, ListsOnlyBoardApprovedPins) {
  UserGpio gpio(board);

  gpio.handleGet("", reply, sizeof(reply));

  EXPECT_STREQ("> available GPIOs: 4,16", reply);
}

TEST_F(UserGpioTest, ReportsWhenNoPinsAreAvailable) {
  UserGpioTestBoard empty_board;
  UserGpio gpio(empty_board);

  gpio.handleGet("", reply, sizeof(reply));

  EXPECT_STREQ("> available GPIOs: none", reply);
}

TEST_F(UserGpioTest, StateAliasesListOnlyPinsNotInResetState) {
  UserGpio gpio(board);

  gpio.handleGet(" state", reply, sizeof(reply));
  EXPECT_STREQ("> active GPIOs: none", reply);

  gpio.handleSet(" 4 off", reply, sizeof(reply));
  gpio.handleSet(" 16 on 5 reset", reply, sizeof(reply));
  gpio.handleGet(" states", reply, sizeof(reply));
  EXPECT_STREQ("> active GPIOs: 4=off,16=on(5s->reset)", reply);

  gpio.handleSet(" 4 reset", reply, sizeof(reply));
  gpio.handleGet(" status", reply, sizeof(reply));
  EXPECT_STREQ("> active GPIOs: 16=on(5s->reset)", reply);

  gpio.handleGet(" status 16", reply, sizeof(reply));
  EXPECT_STREQ("> GPIO 16 on, 5s -> reset", reply);
}

TEST_F(UserGpioTest, SetsAndGetsOnOffAndReset) {
  UserGpio gpio(board);

  gpio.handleSet(" 16 ON", reply, sizeof(reply));
  EXPECT_STREQ("OK - GPIO 16 on", reply);
  EXPECT_EQ(OUTPUT, g_mock_pin_modes[16]);
  EXPECT_EQ(HIGH, g_mock_pin_levels[16]);

  gpio.handleGet(" 16", reply, sizeof(reply));
  EXPECT_STREQ("> GPIO 16 on", reply);

  gpio.handleSet(" 16 off", reply, sizeof(reply));
  EXPECT_EQ(OUTPUT, g_mock_pin_modes[16]);
  EXPECT_EQ(LOW, g_mock_pin_levels[16]);

  gpio.handleSet(" 16 reset", reply, sizeof(reply));
  EXPECT_STREQ("OK - GPIO 16 reset", reply);
  EXPECT_EQ(INPUT, g_mock_pin_modes[16]);
  gpio.handleGet(" 16", reply, sizeof(reply));
  EXPECT_STREQ("> GPIO 16 reset", reply);
}

TEST_F(UserGpioTest, AppliesTimedTransitionWithoutBlocking) {
  UserGpio gpio(board);

  gpio.handleSet(" 16 on 30 off", reply, sizeof(reply));
  EXPECT_STREQ("OK - GPIO 16 on for 30s, then off", reply);
  EXPECT_TRUE(gpio.hasActiveTimer());

  g_mock_millis = 29999;
  gpio.loop();
  EXPECT_EQ(HIGH, g_mock_pin_levels[16]);

  gpio.handleGet(" 16", reply, sizeof(reply));
  EXPECT_STREQ("> GPIO 16 on, 1s -> off", reply);

  g_mock_millis = 30000;
  gpio.loop();
  EXPECT_EQ(LOW, g_mock_pin_levels[16]);
  EXPECT_FALSE(gpio.hasActiveTimer());
}

TEST_F(UserGpioTest, BareDurationsAreSeconds) {
  UserGpio gpio(board);

  gpio.handleSet(" 16 on 5 off", reply, sizeof(reply));
  EXPECT_STREQ("OK - GPIO 16 on for 5s, then off", reply);

  g_mock_millis = 4999;
  gpio.loop();
  EXPECT_EQ(HIGH, g_mock_pin_levels[16]);
  g_mock_millis = 5000;
  gpio.loop();
  EXPECT_EQ(LOW, g_mock_pin_levels[16]);
}

TEST_F(UserGpioTest, SupportsMillisecondDurations) {
  UserGpio gpio(board);

  gpio.handleSet(" 16 on 5ms off", reply, sizeof(reply));
  EXPECT_STREQ("OK - GPIO 16 on for 5ms, then off", reply);

  g_mock_millis = 1;
  gpio.handleGet(" 16", reply, sizeof(reply));
  EXPECT_STREQ("> GPIO 16 on, 4ms -> off", reply);

  g_mock_millis = 4;
  gpio.loop();
  EXPECT_EQ(HIGH, g_mock_pin_levels[16]);
  g_mock_millis = 5;
  gpio.loop();
  EXPECT_EQ(LOW, g_mock_pin_levels[16]);
}

TEST_F(UserGpioTest, AcceptsUpToTwentyFourHours) {
  UserGpio gpio(board);

  gpio.handleSet(" 16 on 86400 off", reply, sizeof(reply));
  EXPECT_STREQ("OK - GPIO 16 on for 86400s, then off", reply);
  EXPECT_TRUE(gpio.hasActiveTimer());

  gpio.handleSet(" 16 on 86401 off", reply, sizeof(reply));
  EXPECT_STREQ("Error: timer must be 1ms-24h", reply);

  gpio.handleSet(" 16 on 86400001ms off", reply, sizeof(reply));
  EXPECT_STREQ("Error: timer must be 1ms-24h", reply);
}

TEST_F(UserGpioTest, OnAndOffWithoutDurationRemainUntilChanged) {
  UserGpio gpio(board);

  gpio.handleSet(" 16 on", reply, sizeof(reply));
  g_mock_millis = 24UL * 60UL * 60UL * 1000UL;
  gpio.loop();
  EXPECT_EQ(HIGH, g_mock_pin_levels[16]);
  EXPECT_FALSE(gpio.hasActiveTimer());
}

TEST_F(UserGpioTest, ResetCancelsAnExistingTimer) {
  UserGpio gpio(board);

  gpio.handleSet(" 16 on 30 off", reply, sizeof(reply));
  gpio.handleSet(" 16 reset", reply, sizeof(reply));
  EXPECT_FALSE(gpio.hasActiveTimer());

  g_mock_millis = 30000;
  gpio.loop();
  EXPECT_EQ(INPUT, g_mock_pin_modes[16]);

  UserGpio::Completion completion;
  EXPECT_FALSE(gpio.takeCompletion(completion));
}

TEST_F(UserGpioTest, ANewCommandReplacesAnExistingTimer) {
  UserGpio gpio(board);

  gpio.handleSet(" 16 on 30 off", reply, sizeof(reply));
  g_mock_millis = 1000;
  gpio.handleSet(" 16 off 2 on", reply, sizeof(reply));
  EXPECT_STREQ("OK - GPIO 16 off for 2s, then on", reply);

  g_mock_millis = 2999;
  gpio.loop();
  EXPECT_EQ(LOW, g_mock_pin_levels[16]);
  g_mock_millis = 3000;
  gpio.loop();
  EXPECT_EQ(HIGH, g_mock_pin_levels[16]);
  EXPECT_FALSE(gpio.hasActiveTimer());
}

TEST_F(UserGpioTest, TimedFinalStateCanResetThePin) {
  UserGpio gpio(board);

  gpio.handleSet(" 4 off 2 reset", reply, sizeof(reply));
  EXPECT_EQ(OUTPUT, g_mock_pin_modes[4]);
  g_mock_millis = 2000;
  gpio.loop();
  EXPECT_EQ(INPUT, g_mock_pin_modes[4]);
  gpio.handleGet(" 4", reply, sizeof(reply));
  EXPECT_STREQ("> GPIO 4 reset", reply);
}

TEST_F(UserGpioTest, ReportsCompletedTimedTransition) {
  UserGpio gpio(board);

  gpio.handleSet(" 4 off 2 reset", reply, sizeof(reply), 1234, 2);
  g_mock_millis = 2000;
  gpio.loop();

  UserGpio::Completion completion;
  ASSERT_TRUE(gpio.takeCompletion(completion));
  EXPECT_EQ(4, completion.pin);
  EXPECT_EQ(UserGpio::STATE_RESET, completion.state);
  EXPECT_EQ(1234U, completion.request_id);
  EXPECT_FALSE(gpio.takeCompletion(completion));
}

TEST_F(UserGpioTest, DuplicateNetworkTimerDoesNotRestartCountdown) {
  UserGpio gpio(board);

  gpio.handleSet(" 16 on 30 off", reply, sizeof(reply), 1234, 2);
  g_mock_millis = 1000;
  UserGpio::SetResult result =
      gpio.handleSet(" 16 on 30 off", reply, sizeof(reply), 1234, 2);
  EXPECT_EQ(UserGpio::SetResult::DUPLICATE_IGNORED, result.outcome);
  EXPECT_STREQ("OK - duplicate ignored; GPIO 16 on, 29s -> off", reply);

  g_mock_millis = 29999;
  gpio.loop();
  EXPECT_EQ(HIGH, g_mock_pin_levels[16]);
  g_mock_millis = 30000;
  gpio.loop();
  EXPECT_EQ(LOW, g_mock_pin_levels[16]);
}

TEST_F(UserGpioTest, SameTimestampFromAnotherClientIsNotADuplicate) {
  UserGpio gpio(board);

  gpio.handleSet(" 16 on 30 off", reply, sizeof(reply), 1234, 2);
  g_mock_millis = 1000;
  UserGpio::SetResult result =
      gpio.handleSet(" 16 on 30 off", reply, sizeof(reply), 1234, 3);
  EXPECT_EQ(UserGpio::SetResult::TIMER_STARTED, result.outcome);

  g_mock_millis = 30000;
  gpio.loop();
  EXPECT_EQ(HIGH, g_mock_pin_levels[16]);
  g_mock_millis = 31000;
  gpio.loop();
  EXPECT_EQ(LOW, g_mock_pin_levels[16]);
}

TEST_F(UserGpioTest, TimersRemainCorrectAcrossMillisWrap) {
  g_mock_millis = UINT32_MAX - 499;
  UserGpio gpio(board);

  gpio.handleSet(" 4 on 1 off", reply, sizeof(reply));
  g_mock_millis = 499;
  gpio.loop();
  EXPECT_EQ(HIGH, g_mock_pin_levels[4]);

  g_mock_millis = 500;
  gpio.loop();
  EXPECT_EQ(LOW, g_mock_pin_levels[4]);
}

TEST_F(UserGpioTest, RejectsUnavailablePinsAndMalformedTimers) {
  UserGpio gpio(board);

  gpio.handleSet(" 5 on", reply, sizeof(reply));
  EXPECT_STREQ("Error: GPIO 5 is unavailable", reply);
  EXPECT_EQ(INPUT, g_mock_pin_modes[5]);

  gpio.handleSet(" 16 reset 10 on", reply, sizeof(reply));
  EXPECT_STREQ("Error: use set gpio <pin> on|off [duration on|off|reset]", reply);

  gpio.handleSet(" 16 on 0 off", reply, sizeof(reply));
  EXPECT_STREQ("Error: timer must be 1ms-24h", reply);
}

TEST(UserGpioReplyTrackerTest, KeepsTheSchedulingClientRouteUntilCompletion) {
  UserGpioReplyTracker tracker;
  const uint8_t client_key[UserGpioReplyTracker::CLIENT_TAG_SIZE] =
      {1, 2, 3, 4, 5, 6, 7, 8};
  tracker.beginCommand(3, 2, client_key);
  EXPECT_NE(0U, tracker.requestSource());
  tracker.timerScheduled(16, 1234);

  tracker.beginCommand(7, 1, client_key);
  int client_index = -1;
  uint8_t path_hash_size = 0;
  uint8_t returned_tag[UserGpioReplyTracker::CLIENT_TAG_SIZE];
  ASSERT_TRUE(tracker.takeRoute(16, 1234, client_index, path_hash_size,
                                returned_tag));
  EXPECT_EQ(3, client_index);
  EXPECT_EQ(2, path_hash_size);
  EXPECT_TRUE(UserGpioReplyTracker::matchesClient(client_key, returned_tag));
  EXPECT_FALSE(tracker.takeRoute(16, 1234, client_index, path_hash_size,
                                 returned_tag));
}

TEST(UserGpioReplyTrackerTest, ResetCancellationDropsTheCompletionRoute) {
  UserGpioReplyTracker tracker;
  const uint8_t client_key[UserGpioReplyTracker::CLIENT_TAG_SIZE] =
      {1, 2, 3, 4, 5, 6, 7, 8};
  tracker.beginCommand(3, 2, client_key);
  tracker.timerScheduled(16, 1234);
  tracker.timerCancelled(16);

  int client_index = -1;
  uint8_t path_hash_size = 0;
  uint8_t returned_tag[UserGpioReplyTracker::CLIENT_TAG_SIZE];
  EXPECT_FALSE(tracker.takeRoute(16, 1234, client_index, path_hash_size,
                                 returned_tag));
}
