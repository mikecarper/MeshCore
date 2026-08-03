#include <gtest/gtest.h>

#include <Arduino.h>
#include <helpers/UserGpio.h>

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

TEST_F(UserGpioTest, ResetCancelsAnExistingTimer) {
  UserGpio gpio(board);

  gpio.handleSet(" 16 on 30 off", reply, sizeof(reply));
  gpio.handleSet(" 16 reset", reply, sizeof(reply));
  EXPECT_FALSE(gpio.hasActiveTimer());

  g_mock_millis = 30000;
  gpio.loop();
  EXPECT_EQ(INPUT, g_mock_pin_modes[16]);
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
  EXPECT_STREQ("Error: use set gpio <pin> on|off [seconds on|off|reset]", reply);

  gpio.handleSet(" 16 on 0 off", reply, sizeof(reply));
  EXPECT_STREQ("Error: timer must be 1-2147483 seconds", reply);
}
