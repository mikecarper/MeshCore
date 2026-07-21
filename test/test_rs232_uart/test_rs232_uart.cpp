#include <gtest/gtest.h>

#include <helpers/bridges/RS232UartUtils.h>

#include <vector>

struct FakeUart {
  std::vector<int> calls;
  void end() { calls.push_back(1); }
  void setPins(int rx, int tx) {
    calls.push_back(2);
    calls.push_back(rx);
    calls.push_back(tx);
  }
};

TEST(RS232Uart, StopsPeripheralBeforeChangingPins) {
  FakeUart uart;
  mesh::bridge::prepareNrfUart(uart, 7, 8);
  ASSERT_EQ(uart.calls.size(), 4u);
  EXPECT_EQ(uart.calls[0], 1);
  EXPECT_EQ(uart.calls[1], 2);
  EXPECT_EQ(uart.calls[2], 7);
  EXPECT_EQ(uart.calls[3], 8);
}

int main(int argc, char** argv) {
  ::testing::InitGoogleTest(&argc, argv);
  return RUN_ALL_TESTS();
}
