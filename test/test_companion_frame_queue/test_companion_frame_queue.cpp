#include <gtest/gtest.h>

#include <helpers/CompanionFrameQueue.h>
#include <helpers/CompanionFrameLimits.h>
#include <helpers/CompanionHardwareCommandCompat.h>

namespace {

struct TestFrame {
  size_t len;
  uint8_t buf[MAX_FRAME_SIZE];
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

TEST(CompanionFrameQueue, Radio2LogIsBestEffortAndBroadcast) {
  const uint8_t radio2_log[] = {0x92, 20, uint8_t(-92), 0xA1, 0x88};
  EXPECT_EQ(mesh::COMPANION_BEST_EFFORT_PUSH,
            mesh::companionFrameClass(radio2_log, sizeof(radio2_log)));
  EXPECT_FALSE(mesh::companionFrameRequiresDelivery(radio2_log, sizeof(radio2_log)));
  EXPECT_FALSE(mesh::companionFrameUsesRequesterRoute(radio2_log, sizeof(radio2_log)));
}

TEST(CompanionFrameQueue, BothRadioLogsStayFifoBesideRequiredReplies) {
  TestFrame queue[6] = {};
  size_t count = 0;
  const uint8_t primary[] = {0x88, 20, uint8_t(-80), 0x01};
  const uint8_t radio2[] = {0x92, 24, uint8_t(-90), 0x02};
  const uint8_t primary_after[] = {0x88, 16, uint8_t(-70), 0x03};
  ASSERT_TRUE(mesh::enqueueCompanionFrame(queue, count, 6, primary, sizeof(primary)));
  ASSERT_TRUE(enqueue(queue, count, 6, 0x85));
  ASSERT_TRUE(mesh::enqueueCompanionFrame(queue, count, 6, radio2, sizeof(radio2)));
  ASSERT_TRUE(enqueue(queue, count, 6, 0x00));
  ASSERT_TRUE(mesh::enqueueCompanionFrame(queue, count, 6, primary_after, sizeof(primary_after)));
  ASSERT_EQ(5U, count);
  EXPECT_EQ(0x00, queue[0].buf[0]);
  EXPECT_EQ(0x85, queue[1].buf[0]);
  EXPECT_EQ(0, memcmp(primary, queue[2].buf, sizeof(primary)));
  EXPECT_EQ(0, memcmp(radio2, queue[3].buf, sizeof(radio2)));
  EXPECT_EQ(0, memcmp(primary_after, queue[4].buf, sizeof(primary_after)));
}

TEST(CompanionFrameQueue, Radio2LogAdmissionAndEvictionKeepWholePackets) {
  TestFrame queue[4] = {};
  size_t count = 0;
  const uint8_t primary[] = {0x88, 20, uint8_t(-80), 0x01};
  const uint8_t radio2[] = {0x92, 24, uint8_t(-90), 0x02};
  ASSERT_TRUE(mesh::enqueueCompanionFrame(queue, count, 4, primary, sizeof(primary)));
  ASSERT_TRUE(mesh::enqueueCompanionFrame(queue, count, 4, radio2, sizeof(radio2)));
  ASSERT_TRUE(enqueue(queue, count, 4, 0x83));
  // The fourth slot remains for required traffic; rejection leaves no
  // separate metadata which could attach itself to a later primary packet.
  EXPECT_FALSE(mesh::enqueueCompanionFrame(queue, count, 4, radio2, sizeof(radio2)));
  ASSERT_EQ(3U, count);
  ASSERT_TRUE(enqueue(queue, count, 4, 0x85));
  ASSERT_TRUE(enqueue(queue, count, 4, 0x00));
  ASSERT_EQ(4U, count);
  EXPECT_EQ(0x00, queue[0].buf[0]);
  EXPECT_EQ(0x83, queue[1].buf[0]);
  EXPECT_EQ(0x85, queue[2].buf[0]);
  ASSERT_EQ(sizeof(primary), queue[3].len);
  EXPECT_EQ(0, memcmp(primary, queue[3].buf, sizeof(primary)));
  // The entire tagged packet was evicted. No log can displace these
  // required replies, and the next command response can reclaim the log.
  EXPECT_FALSE(mesh::enqueueCompanionFrame(queue, count, 4, radio2, sizeof(radio2)));
  ASSERT_TRUE(enqueue(queue, count, 4, 0x01));
  EXPECT_FALSE(enqueue(queue, count, 4, 0x02));
  EXPECT_EQ(0x00, queue[0].buf[0]);
  EXPECT_EQ(0x01, queue[1].buf[0]);
  EXPECT_EQ(0x83, queue[2].buf[0]);
  EXPECT_EQ(0x85, queue[3].buf[0]);
}

TEST(CompanionFrameQueue, MaximumRadio2LogKeepsExistingMtuAndCompletePayload) {
  struct {
    uint32_t before = 0x11223344;
    TestFrame queue[4] = {};
    uint32_t after = 0x55667788;
  } guarded;
  uint8_t radio2[MAX_FRAME_SIZE];
  for (size_t i = 0; i < sizeof(radio2); ++i) radio2[i] = uint8_t(i);
  radio2[0] = 0x92;
  radio2[1] = uint8_t(-16);  // Same SNR/RSSI/raw offsets as legacy 0x88.
  radio2[2] = uint8_t(-100);
  size_t count = 0;
  ASSERT_TRUE(mesh::enqueueCompanionFrame(guarded.queue, count, 4, radio2, sizeof(radio2)));
  ASSERT_TRUE(enqueue(guarded.queue, count, 4, 0x00));
  ASSERT_EQ(2U, count);
  ASSERT_EQ(sizeof(radio2), guarded.queue[1].len);
  EXPECT_EQ(0, memcmp(radio2, guarded.queue[1].buf, sizeof(radio2)));
  EXPECT_EQ(0x11223344U, guarded.before);
  EXPECT_EQ(0x55667788U, guarded.after);
}

TEST(CompanionFrameQueue, Radio2RawHalfStaysPinnedAfterItsMarkerWasSent) {
  TestFrame queue[4] = {
      {4, {mesh::COMPANION_RADIO2_LOG_PENDING, 24, uint8_t(-90), 0x02}},
      {4, {0x88, 20, uint8_t(-80), 0x01}}};
  const TestFrame pending = queue[0];
  size_t count = 2;
  EXPECT_TRUE(mesh::companionFrameRequiresDelivery(pending.buf, pending.len));
  EXPECT_FALSE(mesh::companionFrameUsesRequesterRoute(pending.buf, pending.len));
  ASSERT_TRUE(enqueue(queue, count, 4, 0x85));
  ASSERT_TRUE(enqueue(queue, count, 4, 0x00));
  ASSERT_TRUE(enqueue(queue, count, 4, 0x01));
  ASSERT_EQ(4U, count);
  ASSERT_EQ(pending.len, queue[0].len);
  EXPECT_EQ(0, memcmp(pending.buf, queue[0].buf, pending.len));
  EXPECT_EQ(0x00, queue[1].buf[0]);
  EXPECT_EQ(0x01, queue[2].buf[0]);
  EXPECT_EQ(0x85, queue[3].buf[0]);
  EXPECT_FALSE(enqueue(queue, count, 4, 0x82));
  EXPECT_FALSE(enqueue(queue, count, 4, 0x88));
  EXPECT_FALSE(enqueue(queue, count, 4, mesh::COMPANION_RADIO2_LOG));
}

TEST(CompanionFrameQueue, AOneEntryPendingGroupCannotBeEvicted) {
  TestFrame queue[1] = {{4, {mesh::COMPANION_RADIO2_LOG_PENDING, 24, 0xA0, 0x02}}};
  size_t count = 1;
  EXPECT_FALSE(enqueue(queue, count, 1, 0x00));
  EXPECT_EQ(1U, count);
  EXPECT_TRUE(mesh::companionRadioLogDataPending(queue[0].buf, queue[0].len));
}

TEST(CompanionFrameQueue, InternalLogStateIsNotAStandaloneWireMarker) {
  const uint8_t marker[] = {mesh::COMPANION_RADIO2_LOG, 0xA1};
  const uint8_t queued[] = {mesh::COMPANION_RADIO2_LOG, 0xA1, 0x88, 0x93};
  EXPECT_FALSE(mesh::isCompanionRadio2Log(nullptr, sizeof(queued)));
  EXPECT_FALSE(mesh::isCompanionRadio2Log(marker, sizeof(marker)));
  EXPECT_TRUE(mesh::companionRadioLogMarkerPending(queued, sizeof(queued)));
  EXPECT_FALSE(mesh::companionRadioLogDataPending(queued, sizeof(queued)));
  const uint8_t pending[] = {mesh::COMPANION_RADIO2_LOG_PENDING, 0xA1, 0x88, 0x93};
  EXPECT_TRUE(mesh::isCompanionRadio2Log(pending, sizeof(pending)));
  EXPECT_TRUE(mesh::companionRadioLogDataPending(pending, sizeof(pending)));
  EXPECT_FALSE(mesh::companionRadioLogMarkerPending(pending, sizeof(pending)));
}

TEST(CompanionFrameQueue, QueuePriorityDoesNotImplyRequesterOwnership) {
  uint8_t response = 0x05;
  uint8_t login_result = 0x85;
  uint8_t message_waiting = 0x83;
  uint8_t control_data = 0x8E;
  uint8_t unknown = 0xF1;

  EXPECT_TRUE(mesh::companionFrameUsesRequesterRoute(&response, 1));
  EXPECT_TRUE(mesh::companionFrameUsesRequesterRoute(&login_result, 1));
  EXPECT_FALSE(mesh::companionFrameUsesRequesterRoute(&message_waiting, 1));
  EXPECT_FALSE(mesh::companionFrameUsesRequesterRoute(&control_data, 1));
  EXPECT_FALSE(mesh::companionFrameUsesRequesterRoute(&unknown, 1));
  EXPECT_TRUE(mesh::companionFrameRequiresDelivery(&message_waiting, 1));
  EXPECT_TRUE(mesh::companionFrameRequiresDelivery(&control_data, 1));
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

TEST(CompanionHardwareCommandCompat, OfficialAppCliTerminatorIsOptional) {
  const uint8_t command[] = "A7|get wifi.enabled";
  const size_t text_length = sizeof(command) - 1;
  EXPECT_EQ(text_length, mesh::companion::cliCommandTextLength(command, sizeof(command)));
  EXPECT_EQ(text_length, mesh::companion::cliCommandTextLength(command, text_length));
  const uint8_t single[] = {'x', 0};
  EXPECT_EQ(1U, mesh::companion::cliCommandTextLength(single, 1));
  EXPECT_EQ(1U, mesh::companion::cliCommandTextLength(single, 2));
}

TEST(CompanionHardwareCommandCompat, EmptyAndEmbeddedNulCliBodiesAreRejected) {
  const uint8_t empty[] = {0};
  const uint8_t embedded[] = {'v', 'e', 'r', 0, 'x'};
  const uint8_t doubled[] = {'v', 'e', 'r', 0, 0};
  EXPECT_EQ(0U, mesh::companion::cliCommandTextLength(nullptr, 1));
  EXPECT_EQ(0U, mesh::companion::cliCommandTextLength(empty, 0));
  EXPECT_EQ(0U, mesh::companion::cliCommandTextLength(empty, sizeof(empty)));
  EXPECT_EQ(0U, mesh::companion::cliCommandTextLength(embedded, sizeof(embedded)));
  EXPECT_EQ(0U, mesh::companion::cliCommandTextLength(doubled, sizeof(doubled)));
}

TEST(CompanionHardwareCommandCompat, FullSizeCliBodyDoesNotRequireExtraByte) {
  uint8_t body[171];
  memset(body, 'x', sizeof(body));
  EXPECT_EQ(sizeof(body), mesh::companion::cliCommandTextLength(body, sizeof(body)));
  body[sizeof(body) - 1] = 0;
  EXPECT_EQ(sizeof(body) - 1, mesh::companion::cliCommandTextLength(body, sizeof(body)));
}

}  // namespace

int main(int argc, char** argv) {
  ::testing::InitGoogleTest(&argc, argv);
  return RUN_ALL_TESTS();
}
