#include <gtest/gtest.h>

#include <helpers/CompanionFrameQueue.h>
#include <helpers/CompanionHardwareCommandCompat.h>

namespace {

struct TestFrame {
  size_t len;
  uint8_t buf[8];
};

static bool enqueue(TestFrame queue[], size_t& count, size_t capacity,
                    uint8_t code) {
  return mesh::enqueueCompanionFrame(queue, count, capacity, &code, 1);
}

TEST(CompanionFrameQueue, HighBitSeparatesResponsesFromPushes) {
  const uint8_t highest_command_or_response = 0x7F;
  const uint8_t first_push = 0x80;
  const uint8_t unknown_high_code = 0x98;

  EXPECT_FALSE(mesh::isCompanionPushFrame(&highest_command_or_response, 1));
  EXPECT_TRUE(mesh::isCompanionPushFrame(&first_push, 1));
  EXPECT_TRUE(mesh::isCompanionPushFrame(&unknown_high_code, 1));
}

TEST(CompanionFrameQueue, PushTrafficLeavesOneResponseSlot) {
  TestFrame queue[4] = {};
  size_t count = 0;

  EXPECT_TRUE(enqueue(queue, count, 4, 0x88));
  EXPECT_TRUE(enqueue(queue, count, 4, 0x84));
  EXPECT_TRUE(enqueue(queue, count, 4, 0x81));
  EXPECT_FALSE(enqueue(queue, count, 4, 0x8A));
  ASSERT_EQ(3U, count);

  EXPECT_TRUE(enqueue(queue, count, 4, 0x00));
  ASSERT_EQ(4U, count);
  EXPECT_EQ(0x00, queue[0].buf[0]);
}

TEST(CompanionFrameQueue, RequiredPushEvictsBestEffortTraffic) {
  TestFrame queue[4] = {
      {1, {0x06}}, {1, {0x82}}, {1, {0x88}}, {1, {0x84}}};
  size_t count = 4;

  ASSERT_TRUE(enqueue(queue, count, 4, 0x8B));
  ASSERT_EQ(4U, count);
  EXPECT_EQ(0x06, queue[0].buf[0]);
  EXPECT_EQ(0x82, queue[1].buf[0]);
  EXPECT_EQ(0x8B, queue[2].buf[0]);
  EXPECT_EQ(0x88, queue[3].buf[0]);
}

TEST(CompanionFrameQueue, RequiredPushesRemainFifoAheadOfLogs) {
  TestFrame queue[6] = {};
  size_t count = 0;

  ASSERT_TRUE(enqueue(queue, count, 6, 0x88));
  ASSERT_TRUE(enqueue(queue, count, 6, 0x82));
  ASSERT_TRUE(enqueue(queue, count, 6, 0x84));
  ASSERT_TRUE(enqueue(queue, count, 6, 0x85));

  ASSERT_EQ(4U, count);
  EXPECT_EQ(0x82, queue[0].buf[0]);
  EXPECT_EQ(0x85, queue[1].buf[0]);
  EXPECT_EQ(0x88, queue[2].buf[0]);
  EXPECT_EQ(0x84, queue[3].buf[0]);
}

TEST(CompanionFrameQueue, MessageWaitingCoalesces) {
  TestFrame queue[4] = {};
  size_t count = 0;

  ASSERT_TRUE(enqueue(queue, count, 4, 0x83));
  ASSERT_TRUE(enqueue(queue, count, 4, 0x83));
  EXPECT_EQ(1U, count);
}

TEST(CompanionFrameQueue, UnknownPushDefaultsToRequired) {
  uint8_t unknown = 0xF1;
  EXPECT_TRUE(mesh::companionFrameRequiresDelivery(&unknown, 1));
  uint8_t packet_log = 0x88;
  EXPECT_FALSE(mesh::companionFrameRequiresDelivery(&packet_log, 1));
}

TEST(CompanionFrameQueue, ResponsesRunBeforeQueuedPushesAndRemainFifo) {
  TestFrame queue[5] = {};
  size_t count = 0;

  ASSERT_TRUE(enqueue(queue, count, 5, 0x88));
  ASSERT_TRUE(enqueue(queue, count, 5, 0x06));
  ASSERT_TRUE(enqueue(queue, count, 5, 0x84));
  ASSERT_TRUE(enqueue(queue, count, 5, 0x00));

  ASSERT_EQ(4U, count);
  EXPECT_EQ(0x06, queue[0].buf[0]);
  EXPECT_EQ(0x00, queue[1].buf[0]);
  EXPECT_EQ(0x88, queue[2].buf[0]);
  EXPECT_EQ(0x84, queue[3].buf[0]);
}

TEST(CompanionFrameQueue, ResponseEvictsNewestPushFromAnAlreadyFullQueue) {
  TestFrame queue[4] = {
      {1, {0x06}}, {1, {0x88}}, {1, {0x00}}, {1, {0x84}}};
  size_t count = 4;

  ASSERT_TRUE(enqueue(queue, count, 4, 0x01));
  ASSERT_EQ(4U, count);
  EXPECT_EQ(0x06, queue[0].buf[0]);
  EXPECT_EQ(0x00, queue[1].buf[0]);
  EXPECT_EQ(0x01, queue[2].buf[0]);
  EXPECT_EQ(0x88, queue[3].buf[0]);
}

TEST(CompanionFrameQueue, ResponseDoesNotEvictAnotherResponse) {
  TestFrame queue[3] = {
      {1, {0x00}}, {1, {0x01}}, {1, {0x06}}};
  size_t count = 3;

  EXPECT_FALSE(enqueue(queue, count, 3, 0x02));
  EXPECT_EQ(3U, count);
  EXPECT_EQ(0x00, queue[0].buf[0]);
  EXPECT_EQ(0x01, queue[1].buf[0]);
  EXPECT_EQ(0x06, queue[2].buf[0]);
}

TEST(CompanionHardwareCommandCompat, Command66TextWinsOverBareLegacyGet) {
  EXPECT_FALSE(mesh::companion::isRunCliFrame(0x42, 1));
  EXPECT_TRUE(mesh::companion::isFemRxGainGet(0x42));
  EXPECT_TRUE(mesh::companion::isRunCliFrame(0x42, 2));
  EXPECT_FALSE(mesh::companion::isRunCliFrame(0x41, 2));
}

TEST(CompanionHardwareCommandCompat, BothShippedAliasBlocksRemainInbound) {
  EXPECT_TRUE(mesh::companion::isFemRxGainGet(0x42));
  EXPECT_TRUE(mesh::companion::isFemRxGainGet(0x78));
  EXPECT_TRUE(mesh::companion::isFemRxGainSet(0x43));
  EXPECT_TRUE(mesh::companion::isFemRxGainSet(0x79));
  EXPECT_TRUE(mesh::companion::isRadioRxGainGet(0x44));
  EXPECT_TRUE(mesh::companion::isRadioRxGainGet(0x7A));
  EXPECT_TRUE(mesh::companion::isRadioRxGainSet(0x45));
  EXPECT_TRUE(mesh::companion::isRadioRxGainSet(0x7B));
  EXPECT_TRUE(mesh::companion::isWiFiPowerSaveGet(0x46));
  EXPECT_TRUE(mesh::companion::isWiFiPowerSaveGet(0x7C));
  EXPECT_TRUE(mesh::companion::isWiFiPowerSaveSet(0x47));
  EXPECT_TRUE(mesh::companion::isWiFiPowerSaveSet(0x7D));
  EXPECT_TRUE(mesh::companion::isBluetoothNameGet(0x48));
  EXPECT_TRUE(mesh::companion::isBluetoothNameGet(0x7E));
  EXPECT_TRUE(mesh::companion::isBluetoothNameSet(0x49));
  EXPECT_TRUE(mesh::companion::isBluetoothNameSet(0x7F));

  EXPECT_FALSE(mesh::companion::isRadioRxGainGet(0x78));
  EXPECT_FALSE(mesh::companion::isWiFiPowerSaveSet(0x7F));
}

}  // namespace

int main(int argc, char** argv) {
  ::testing::InitGoogleTest(&argc, argv);
  return RUN_ALL_TESTS();
}
