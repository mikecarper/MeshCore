#include <gtest/gtest.h>

#include <helpers/CompanionWiFiNtpPolicy.h>

using mesh::wifi::CompanionWiFiNtpPolicy;

TEST(CompanionWiFiNtpPolicy, AttemptsImmediatelyAfterBoot) {
  CompanionWiFiNtpPolicy policy;
  EXPECT_TRUE(policy.attemptDue(0));
  EXPECT_TRUE(policy.attemptDue(123456));
}

TEST(CompanionWiFiNtpPolicy, RefreshesExactlyTwentyFourHoursAfterSuccess) {
  CompanionWiFiNtpPolicy policy;
  const uint32_t now = 1234;
  policy.noteSuccess(now);
  EXPECT_FALSE(policy.attemptDue(
      now + mesh::wifi::kCompanionNtpRefreshMillis - 1));
  EXPECT_TRUE(policy.attemptDue(
      now + mesh::wifi::kCompanionNtpRefreshMillis));
}

TEST(CompanionWiFiNtpPolicy, UsesShortBoundedFailureAndBusyRetries) {
  CompanionWiFiNtpPolicy policy;
  const uint32_t now = 500;
  policy.noteBusy(now);
  EXPECT_FALSE(policy.attemptDue(
      now + mesh::wifi::kCompanionNtpBusyRetryMillis - 1));
  EXPECT_TRUE(policy.attemptDue(
      now + mesh::wifi::kCompanionNtpBusyRetryMillis));

  policy.noteFailure(now);
  EXPECT_FALSE(policy.attemptDue(
      now + mesh::wifi::kCompanionNtpFailureRetryMillis - 1));
  EXPECT_TRUE(policy.attemptDue(
      now + mesh::wifi::kCompanionNtpFailureRetryMillis));
}

TEST(CompanionWiFiNtpPolicy, DeadlinesSurviveMillisRollover) {
  CompanionWiFiNtpPolicy policy;
  const uint32_t now = UINT32_MAX - 1000U;
  policy.noteBusy(now);
  EXPECT_FALSE(policy.attemptDue(now + 4999U));
  EXPECT_TRUE(policy.attemptDue(now + 5000U));
}

TEST(CompanionWiFiNtpPolicy, InterruptedAttemptCanResumeWhenConnectivityReturns) {
  CompanionWiFiNtpPolicy policy;
  policy.noteFailure(100);
  EXPECT_FALSE(policy.attemptDue(101));
  policy.requestNow();
  EXPECT_TRUE(policy.attemptDue(101));
}

int main(int argc, char** argv) {
  ::testing::InitGoogleTest(&argc, argv);
  return RUN_ALL_TESTS();
}
