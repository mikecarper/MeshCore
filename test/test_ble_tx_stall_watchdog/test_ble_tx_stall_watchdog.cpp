#include <gtest/gtest.h>

#include <helpers/BleTxStallWatchdog.h>

#include <stdint.h>
#include <vector>

TEST(BleFrameChunks, WritesOneAttPayloadAtATime) {
  const uint8_t frame[50] = {};
  std::vector<size_t> chunk_lengths;

  const size_t written = mesh::writeBleFrameInChunks(
      frame, sizeof(frame), 20,
      [&chunk_lengths](const uint8_t*, size_t len) {
        chunk_lengths.push_back(len);
        return len;
      });

  EXPECT_EQ(written, sizeof(frame));
  ASSERT_EQ(chunk_lengths.size(), 3U);
  EXPECT_EQ(chunk_lengths[0], 20U);
  EXPECT_EQ(chunk_lengths[1], 20U);
  EXPECT_EQ(chunk_lengths[2], 10U);
}

TEST(BleFrameChunks, StopsAfterAnAmbiguousLaterFragmentFailure) {
  const uint8_t frame[50] = {};
  size_t calls = 0;

  const size_t written = mesh::writeBleFrameInChunks(
      frame, sizeof(frame), 20,
      [&calls](const uint8_t*, size_t len) {
        calls++;
        return calls == 1 ? len : 0;
      });

  EXPECT_EQ(written, 20U);
  EXPECT_EQ(calls, 2U);
}

TEST(BleFrameChunks, PreservesAndStopsAtAShortChunkWrite) {
  const uint8_t frame[50] = {};
  size_t calls = 0;

  const size_t written = mesh::writeBleFrameInChunks(
      frame, sizeof(frame), 20,
      [&calls](const uint8_t*, size_t len) {
        calls++;
        return calls == 1 ? len : 5U;
      });

  EXPECT_EQ(written, 25U);
  EXPECT_EQ(calls, 2U);
}

TEST(BleFrameChunks, RejectsInvalidInputsWithoutCallingWriter) {
  const uint8_t frame[1] = {};
  size_t calls = 0;
  auto writer = [&calls](const uint8_t*, size_t len) {
    calls++;
    return len;
  };

  EXPECT_EQ(mesh::writeBleFrameInChunks(nullptr, 1, 20, writer), 0U);
  EXPECT_EQ(mesh::writeBleFrameInChunks(frame, 0, 20, writer), 0U);
  EXPECT_EQ(mesh::writeBleFrameInChunks(frame, 1, 0, writer), 0U);
  EXPECT_EQ(calls, 0U);
}

TEST(BleTxStallWatchdog, ExpiresAtTheExactBlockedBoundary) {
  mesh::BleTxStallWatchdog watchdog;

  EXPECT_FALSE(watchdog.noteBlocked(1000, 10000));
  EXPECT_TRUE(watchdog.active());
  EXPECT_FALSE(watchdog.noteBlocked(10999, 10000));
  EXPECT_TRUE(watchdog.noteBlocked(11000, 10000));
}

TEST(BleTxStallWatchdog, ProgressResetStartsAFreshWindow) {
  mesh::BleTxStallWatchdog watchdog;

  EXPECT_FALSE(watchdog.noteBlocked(100, 1000));
  EXPECT_FALSE(watchdog.noteBlocked(1099, 1000));

  watchdog.reset();
  EXPECT_FALSE(watchdog.active());
  EXPECT_FALSE(watchdog.noteBlocked(1100, 1000));
  EXPECT_FALSE(watchdog.noteBlocked(2099, 1000));
  EXPECT_TRUE(watchdog.noteBlocked(2100, 1000));
}

TEST(BleTxStallWatchdog, ZeroIsAValidStartTime) {
  mesh::BleTxStallWatchdog watchdog;

  EXPECT_FALSE(watchdog.noteBlocked(0, 25));
  EXPECT_FALSE(watchdog.noteBlocked(24, 25));
  EXPECT_TRUE(watchdog.noteBlocked(25, 25));
}

TEST(BleTxStallWatchdog, ElapsedTimeSurvivesMillisRollover) {
  mesh::BleTxStallWatchdog watchdog;
  const uint32_t start = UINT32_MAX - 50U;

  EXPECT_FALSE(watchdog.noteBlocked(start, 100));
  EXPECT_FALSE(watchdog.noteBlocked(48, 100));
  EXPECT_TRUE(watchdog.noteBlocked(49, 100));
}

TEST(BleElapsedAtLeast, HandlesExactBoundaryAndMillisRollover) {
  EXPECT_FALSE(mesh::bleElapsedAtLeast(1099, 100, 1000));
  EXPECT_TRUE(mesh::bleElapsedAtLeast(1100, 100, 1000));

  const uint32_t start = UINT32_MAX - 50U;
  EXPECT_FALSE(mesh::bleElapsedAtLeast(48, start, 100));
  EXPECT_TRUE(mesh::bleElapsedAtLeast(49, start, 100));
}

TEST(BleDisconnectRecovery, AttemptsImmediatelyThenThrottlesRetries) {
  mesh::BleDisconnectRecovery recovery;

  EXPECT_FALSE(recovery.pending());
  EXPECT_FALSE(recovery.shouldAttempt(100, 1000));

  recovery.begin();
  EXPECT_TRUE(recovery.pending());
  EXPECT_TRUE(recovery.shouldAttempt(100, 1000));
  EXPECT_FALSE(recovery.shouldAttempt(1099, 1000));
  EXPECT_TRUE(recovery.shouldAttempt(1100, 1000));
}

TEST(BleDisconnectRecovery, RetryTimingSurvivesMillisRollover) {
  mesh::BleDisconnectRecovery recovery;
  const uint32_t start = UINT32_MAX - 50U;

  recovery.begin();
  EXPECT_TRUE(recovery.shouldAttempt(start, 100));
  EXPECT_FALSE(recovery.shouldAttempt(48, 100));
  EXPECT_TRUE(recovery.shouldAttempt(49, 100));
}

TEST(BleDisconnectRecovery, CompletionStopsRetries) {
  mesh::BleDisconnectRecovery recovery;

  recovery.begin();
  EXPECT_TRUE(recovery.shouldAttempt(0, 100));
  recovery.complete();

  EXPECT_FALSE(recovery.pending());
  EXPECT_FALSE(recovery.shouldAttempt(1000, 100));
}

int main(int argc, char** argv) {
  ::testing::InitGoogleTest(&argc, argv);
  return RUN_ALL_TESTS();
}
