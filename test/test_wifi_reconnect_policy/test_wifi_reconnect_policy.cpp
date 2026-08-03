#include <gtest/gtest.h>
#include <limits>
#include <stdint.h>

#include "helpers/WiFiReconnectPolicy.h"

namespace Policy = WiFiReconnectPolicy;

TEST(WiFiReconnectPolicy, RetriesAtFiveMinuteBoundaryAndEveryFiveMinutesAfter) {
  Policy::Tracker tracker;
  tracker.noteDisconnected(1000U);

  EXPECT_FALSE(tracker.retryDue(1000U + Policy::kRetryIntervalMs - 1U));
  EXPECT_TRUE(tracker.retryDue(1000U + Policy::kRetryIntervalMs));

  tracker.noteAttempt(1000U + Policy::kRetryIntervalMs);
  EXPECT_FALSE(tracker.retryDue(1000U + 2U * Policy::kRetryIntervalMs - 1U));
  EXPECT_TRUE(tracker.retryDue(1000U + 2U * Policy::kRetryIntervalMs));
}

TEST(WiFiReconnectPolicy, ReconnectTimerResetsAfterAConnection) {
  Policy::Tracker tracker;
  tracker.noteDisconnected(1000U);
  tracker.noteConnected();

  EXPECT_FALSE(tracker.isTracking());
  EXPECT_FALSE(tracker.retryDue(1000U + Policy::kRetryIntervalMs));

  tracker.noteDisconnected(500000U);
  EXPECT_FALSE(tracker.retryDue(500000U + Policy::kRetryIntervalMs - 1U));
  EXPECT_TRUE(tracker.retryDue(500000U + Policy::kRetryIntervalMs));
}

TEST(WiFiReconnectPolicy, DuplicateDisconnectObservationsDoNotPostponeRetry) {
  Policy::Tracker tracker;
  tracker.noteDisconnected(1000U);
  tracker.noteDisconnected(200000U);

  EXPECT_TRUE(tracker.retryDue(1000U + Policy::kRetryIntervalMs));
}

TEST(WiFiReconnectPolicy, TimersSurviveMillisRollover) {
  Policy::Tracker tracker;
  const uint32_t start = std::numeric_limits<uint32_t>::max() - 999U;
  tracker.noteDisconnected(start);

  EXPECT_FALSE(tracker.retryDue(start + Policy::kRetryIntervalMs - 1U));
  EXPECT_TRUE(tracker.retryDue(start + Policy::kRetryIntervalMs));
}

TEST(WiFiReconnectPolicy, DisconnectedDurationUsesExactWrapSafeBoundary) {
  Policy::Tracker tracker;
  const uint32_t start = std::numeric_limits<uint32_t>::max() - 99U;
  tracker.noteDisconnected(start);

  EXPECT_FALSE(tracker.disconnectedFor(start + 119999U, 120000U));
  EXPECT_TRUE(tracker.disconnectedFor(start + 120000U, 120000U));
}

int main(int argc, char** argv) {
  ::testing::InitGoogleTest(&argc, argv);
  return RUN_ALL_TESTS();
}
