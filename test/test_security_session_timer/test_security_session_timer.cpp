#include <gtest/gtest.h>

#include <helpers/nrf52/SecuritySessionTimer.h>

TEST(SecuritySessionTimer, ExpiresAtTwoMinutesAndCanBeCancelled) {
  SecuritySessionTimer timer;
  timer.start(1000);
  EXPECT_FALSE(timer.expired(120999));
  EXPECT_TRUE(timer.expired(121000));
  timer.cancel();
  EXPECT_FALSE(timer.expired(500000));
}

TEST(SecuritySessionTimer, HandlesMillisRollover) {
  SecuritySessionTimer timer;
  timer.start(0xFFFFFF00UL);
  EXPECT_FALSE(timer.expired(0x000000FFUL, 512));
  EXPECT_TRUE(timer.expired(0x00000100UL, 512));
}

TEST(SecuritySessionTimer, RestartUsesTheLatestConnection) {
  SecuritySessionTimer timer;
  timer.start(100);
  timer.start(1000);
  EXPECT_FALSE(timer.expired(1100, 200));
  EXPECT_TRUE(timer.expired(1200, 200));
}

int main(int argc, char** argv) {
  ::testing::InitGoogleTest(&argc, argv);
  return RUN_ALL_TESTS();
}
