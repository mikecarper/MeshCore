#include <gtest/gtest.h>

#include <helpers/LazyPersistence.h>

TEST(LazyPersistence, ArmsOnlyTheFirstPendingDeadline) {
  unsigned long pending = 0;
  EXPECT_TRUE(mesh::armFirstLazyPersistence(pending, 5000, true));
  EXPECT_EQ(pending, 5000UL);
  EXPECT_FALSE(mesh::armFirstLazyPersistence(pending, 9000, true));
  EXPECT_EQ(pending, 5000UL);
  EXPECT_FALSE(mesh::armFirstLazyPersistence(pending, 9000, false));
}

TEST(LazyPersistence, SuccessfulSaveClearsPendingWrite) {
  unsigned long pending = 5000;
  mesh::completeLazyPersistenceSave(pending, true, 9000);
  EXPECT_EQ(pending, 0UL);
}

TEST(LazyPersistence, FailedSaveRearmsInsteadOfTightLooping) {
  unsigned long pending = 5000;
  mesh::completeLazyPersistenceSave(pending, false, 10000);
  EXPECT_EQ(pending, 10000UL);
}

TEST(LazyPersistence, WrappedDeadlineNeverLosesPendingWrite) {
  unsigned long pending = 0;
  EXPECT_TRUE(mesh::armFirstLazyPersistence(pending, 0, true));
  EXPECT_EQ(pending, 1UL);

  mesh::completeLazyPersistenceSave(pending, false, 0);
  EXPECT_EQ(pending, 1UL);
}

TEST(LazyPersistence, MutationDoesNotPostponeFirstPendingWrite) {
  unsigned long pending = 0;
  uint8_t failures = 0;
  EXPECT_TRUE(mesh::scheduleLazyPersistenceMutation(
      pending, failures, 5000));
  EXPECT_FALSE(mesh::scheduleLazyPersistenceMutation(
      pending, failures, 9000));
  EXPECT_EQ(pending, 5000UL);
  EXPECT_EQ(failures, 0);
}

TEST(LazyPersistence, FailedSavesBackOffAndCap) {
  uint8_t failures = 0;
  EXPECT_EQ(mesh::recordLazyPersistenceSaveFailure(
      failures, 5000, 300000), 5000u);
  EXPECT_EQ(mesh::recordLazyPersistenceSaveFailure(
      failures, 5000, 300000), 10000u);
  EXPECT_EQ(mesh::recordLazyPersistenceSaveFailure(
      failures, 5000, 300000), 20000u);
  for (int i = 0; i < 20; i++) {
    EXPECT_LE(mesh::recordLazyPersistenceSaveFailure(
        failures, 5000, 300000), 300000u);
  }
  EXPECT_EQ(mesh::recordLazyPersistenceSaveFailure(
      failures, 5000, 300000), 300000u);
}

TEST(LazyPersistence, MutationCannotDefeatFailedSaveBackoff) {
  unsigned long pending = 300000;
  uint8_t failures = 6;
  EXPECT_FALSE(mesh::scheduleLazyPersistenceMutation(
      pending, failures, 5000));
  EXPECT_EQ(pending, 300000UL);
  EXPECT_EQ(failures, 6);
}

TEST(LazyPersistence, SuccessfulSaveResetsTimerAndBackoff) {
  unsigned long pending = 300000;
  uint8_t failures = 6;
  mesh::resetLazyPersistenceAfterSuccess(pending, failures);
  EXPECT_EQ(pending, 0UL);
  EXPECT_EQ(failures, 0);
}

int main(int argc, char** argv) {
  ::testing::InitGoogleTest(&argc, argv);
  return RUN_ALL_TESTS();
}
