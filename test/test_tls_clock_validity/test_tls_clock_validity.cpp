#include <gtest/gtest.h>

#include <helpers/esp32/SntpOperationCoordinator.h>
#include <helpers/esp32/TlsClockValidity.h>

namespace Clock = mesh::tls_clock;

extern "C" void* sntpCoordinatorPeerAddress();
extern "C" bool sntpCoordinatorPeerAcquire();
extern "C" bool sntpCoordinatorPeerRelease();

namespace {

int cleanup_calls = 0;

void recordCleanup() {
  ++cleanup_calls;
}

}  // namespace

TEST(TlsClockValidity, RejectsNegativeAndPreMinimumEpochs) {
  EXPECT_FALSE(Clock::timeIsValid((time_t)-1));
  EXPECT_FALSE(Clock::timeIsValid(Clock::kMinimumValidEpoch - 1));
}

TEST(TlsClockValidity, AcceptsTheExactMinimumAndLaterEpochs) {
  EXPECT_TRUE(Clock::timeIsValid(Clock::kMinimumValidEpoch));
  EXPECT_TRUE(Clock::timeIsValid(Clock::kMinimumValidEpoch + 1));
}

TEST(TlsClockValidity, RequiresFreshProofAndConnectedWiFi) {
  const time_t now = Clock::kMinimumValidEpoch;
  EXPECT_FALSE(Clock::proofIsValid(false, true, now));
  EXPECT_FALSE(Clock::proofIsValid(true, false, now));
  EXPECT_FALSE(Clock::proofIsValid(true, true, now - 1));
  EXPECT_TRUE(Clock::proofIsValid(true, true, now));
}

TEST(TlsClockValidity, ProofAgeIsBoundedAndMillisRolloverSafe) {
  EXPECT_FALSE(Clock::proofAgeIsValid(false, 100, 90, 10));
  EXPECT_TRUE(Clock::proofAgeIsValid(true, 100, 90, 10));
  EXPECT_FALSE(Clock::proofAgeIsValid(true, 101, 90, 10));

  const uint32_t before_wrap = UINT32_MAX - 5U;
  EXPECT_TRUE(Clock::proofAgeIsValid(true, 4, before_wrap, 10));
  EXPECT_FALSE(Clock::proofAgeIsValid(true, 5, before_wrap, 10));
}

TEST(TlsClockValidity, LateCallbackGenerationFailsClosed) {
  EXPECT_FALSE(Clock::proofGenerationIsValid(false, 7, 7));
  EXPECT_FALSE(Clock::proofGenerationIsValid(true, 7, 0));
  EXPECT_FALSE(Clock::proofGenerationIsValid(true, 6, 7));
  EXPECT_TRUE(Clock::proofGenerationIsValid(true, 7, 7));
}

TEST(SntpOperationCoordinator, IsNonBlockingAndOnlyOwnerRunsCleanup) {
  mesh::sntp_coord::OperationCoordinator coordinator;
  cleanup_calls = 0;

  mesh::sntp_coord::OperationLease first(coordinator, recordCleanup);
  mesh::sntp_coord::OperationLease second(coordinator, recordCleanup);
  ASSERT_TRUE(first.tryAcquire());
  const uint32_t first_generation = first.generation();
  EXPECT_NE(first_generation, 0U);
  EXPECT_TRUE(first.owns());
  EXPECT_FALSE(second.tryAcquire());
  EXPECT_EQ(cleanup_calls, 0);

  EXPECT_TRUE(first.release());
  EXPECT_EQ(cleanup_calls, 1);
  EXPECT_FALSE(first.release());
  EXPECT_EQ(cleanup_calls, 1);

  ASSERT_TRUE(second.tryAcquire());
  EXPECT_NE(second.generation(), first_generation);
  EXPECT_TRUE(second.owns());
  // A stale/repeated release from the first lease cannot clear the new owner.
  EXPECT_FALSE(first.release());
  EXPECT_TRUE(second.owns());
  EXPECT_EQ(cleanup_calls, 1);
  EXPECT_TRUE(second.release());
  EXPECT_EQ(cleanup_calls, 2);
}

TEST(SntpOperationCoordinator, ProcessWideAccessorReturnsOneInstance) {
  EXPECT_EQ(&mesh::sntp_coord::processWideCoordinator(),
            sntpCoordinatorPeerAddress());

  mesh::sntp_coord::OperationLease local(
      mesh::sntp_coord::processWideCoordinator());
  ASSERT_TRUE(local.tryAcquire());
  EXPECT_FALSE(sntpCoordinatorPeerAcquire());
  ASSERT_TRUE(local.release());
  EXPECT_TRUE(sntpCoordinatorPeerAcquire());
  EXPECT_TRUE(sntpCoordinatorPeerRelease());
}

int main(int argc, char** argv) {
  ::testing::InitGoogleTest(&argc, argv);
  return RUN_ALL_TESTS();
}
