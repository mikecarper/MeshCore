#include <gtest/gtest.h>

#include <helpers/nrf52/InternalSecondaryFsRepair.h>

using mesh::storage::InternalSecondaryFsRepairResult;
using mesh::storage::InternalSecondaryFsBootResult;
using mesh::storage::InternalSecondaryFsRecoveryResult;
using mesh::storage::isInternalExtraFsReservedByApplication;
using mesh::storage::isErasedFlashRange;
using mesh::storage::isExpectedInternalExtraFsGeometry;
using mesh::storage::prepareInternalSecondaryFilesystem;
using mesh::storage::repairInternalSecondaryFilesystem;
using mesh::storage::recoverInternalSecondaryFilesystem;

TEST(InternalSecondaryFsBoot, RequiresExact100KiBGeometry) {
  EXPECT_TRUE(isExpectedInternalExtraFsGeometry(0xD4000, 0x19000, 128));
  EXPECT_FALSE(isExpectedInternalExtraFsGeometry(0xED000, 0x7000, 128));
  EXPECT_FALSE(isExpectedInternalExtraFsGeometry(0xD4000, 0x7000, 128));
  EXPECT_FALSE(isExpectedInternalExtraFsGeometry(0xD4000, 0x19000, 4096));
  EXPECT_FALSE(isExpectedInternalExtraFsGeometry(0xD3000, 0x1A000, 128));
  EXPECT_FALSE(isExpectedInternalExtraFsGeometry(0xD4000, 0x19001, 128));
  EXPECT_FALSE(isExpectedInternalExtraFsGeometry(0xD4000, 0x18FFF, 128));
  EXPECT_FALSE(isExpectedInternalExtraFsGeometry(0xD4000, 0, 128));
  EXPECT_TRUE(isInternalExtraFsReservedByApplication(0xD4000));
  EXPECT_TRUE(isInternalExtraFsReservedByApplication(0xC0000));
  EXPECT_FALSE(isInternalExtraFsReservedByApplication(0xD4001));
  EXPECT_FALSE(isInternalExtraFsReservedByApplication(0xED000));
}

TEST(InternalSecondaryFsBoot, ErasedRangeScansEveryWordExactlyOnce) {
  const uint32_t start = 0xED000;
  const uint32_t size = 7 * 4096;
  uint32_t reads = 0;
  EXPECT_TRUE(isErasedFlashRange(
      start, size, [&](uint32_t address) {
        EXPECT_EQ(address, start + reads * sizeof(uint32_t));
        reads++;
        return 0xFFFFFFFFUL;
      }));
  EXPECT_EQ(reads, size / sizeof(uint32_t));
}

TEST(InternalSecondaryFsBoot, ErasedRangeRejectsAnyProgrammedWord) {
  const uint32_t start = 0xED000;
  const uint32_t size = 7 * 4096;
  const uint32_t programmed_address = start + size - sizeof(uint32_t);
  EXPECT_FALSE(isErasedFlashRange(
      start, size, [&](uint32_t address) {
        return address == programmed_address ? 0xFFFFFFFEUL : 0xFFFFFFFFUL;
      }));
}

TEST(InternalSecondaryFsBoot, ErasedRangeRejectsIncompleteGeometry) {
  auto erased = [](uint32_t) { return 0xFFFFFFFFUL; };
  EXPECT_FALSE(isErasedFlashRange(0xED000, 0, erased));
  EXPECT_FALSE(isErasedFlashRange(0xED001, 7 * 4096, erased));
  EXPECT_FALSE(isErasedFlashRange(0xED000, 7 * 4096 - 1, erased));
}

TEST(InternalSecondaryFsBoot, HealthyFilesystemMountsWithoutOtherAccess) {
  int blank_calls = 0;
  int format_calls = 0;
  int mount_calls = 0;
  const auto result = prepareInternalSecondaryFilesystem(
      [&]() { mount_calls++; return true; },
      [&]() { blank_calls++; return false; },
      [&]() { format_calls++; return true; });

  EXPECT_EQ(result, InternalSecondaryFsBootResult::Mounted);
  EXPECT_EQ(mount_calls, 1);
  EXPECT_EQ(blank_calls, 0);
  EXPECT_EQ(format_calls, 0);
}

TEST(InternalSecondaryFsBoot, FailedMountPreservesNonblankMedia) {
  int format_calls = 0;
  const auto result = prepareInternalSecondaryFilesystem(
      []() { return false; },
      []() { return false; },
      [&]() { format_calls++; return true; });

  EXPECT_EQ(result, InternalSecondaryFsBootResult::PreservedNonBlank);
  EXPECT_EQ(format_calls, 0);
}

TEST(InternalSecondaryFsBoot, BlankMediaIsFormattedThenMounted) {
  int mount_calls = 0;
  int format_calls = 0;
  const auto result = prepareInternalSecondaryFilesystem(
      [&]() { return ++mount_calls == 2; },
      []() { return true; },
      [&]() { format_calls++; return true; });

  EXPECT_EQ(result, InternalSecondaryFsBootResult::InitializedBlank);
  EXPECT_EQ(mount_calls, 2);
  EXPECT_EQ(format_calls, 1);
}

TEST(InternalSecondaryFsBoot, BlankMediaFormatFailureStaysDisabled) {
  int mount_calls = 0;
  const auto result = prepareInternalSecondaryFilesystem(
      [&]() { mount_calls++; return false; },
      []() { return true; },
      []() { return false; });

  EXPECT_EQ(result, InternalSecondaryFsBootResult::InitializationFailed);
  EXPECT_EQ(mount_calls, 1);
}

TEST(InternalSecondaryFsBoot, BlankMediaRemountFailureStaysDisabled) {
  int mount_calls = 0;
  const auto result = prepareInternalSecondaryFilesystem(
      [&]() { mount_calls++; return false; },
      []() { return true; },
      []() { return true; });

  EXPECT_EQ(result, InternalSecondaryFsBootResult::InitializationFailed);
  EXPECT_EQ(mount_calls, 2);
}

TEST(InternalSecondaryFsRepair, RunsFormatMountAndValidationInOrder) {
  int sequence = 0;
  int formatted_at = 0;
  int mounted_at = 0;
  int validated_at = 0;

  const auto result = repairInternalSecondaryFilesystem(
      [&]() {
        formatted_at = ++sequence;
        return true;
      },
      [&]() {
        mounted_at = ++sequence;
        return true;
      },
      [&]() {
        validated_at = ++sequence;
        return true;
      });

  EXPECT_EQ(result, InternalSecondaryFsRepairResult::Repaired);
  EXPECT_EQ(formatted_at, 1);
  EXPECT_EQ(mounted_at, 2);
  EXPECT_EQ(validated_at, 3);
}

TEST(InternalSecondaryFsRepair, FormatFailureStopsBeforeMount) {
  int mount_calls = 0;
  int validation_calls = 0;
  const auto result = repairInternalSecondaryFilesystem(
      []() { return false; },
      [&]() { mount_calls++; return true; },
      [&]() { validation_calls++; return true; });

  EXPECT_EQ(result, InternalSecondaryFsRepairResult::FormatFailed);
  EXPECT_EQ(mount_calls, 0);
  EXPECT_EQ(validation_calls, 0);
}

TEST(InternalSecondaryFsRepair, MountFailureStopsBeforeValidation) {
  int validation_calls = 0;
  const auto result = repairInternalSecondaryFilesystem(
      []() { return true; },
      []() { return false; },
      [&]() { validation_calls++; return true; });

  EXPECT_EQ(result, InternalSecondaryFsRepairResult::MountFailed);
  EXPECT_EQ(validation_calls, 0);
}

TEST(InternalSecondaryFsRepair, RejectsFilesystemWhichRemainsInvalid) {
  const auto result = repairInternalSecondaryFilesystem(
      []() { return true; },
      []() { return true; },
      []() { return false; });

  EXPECT_EQ(result, InternalSecondaryFsRepairResult::ValidationFailed);
}

TEST(InternalSecondaryFsRecovery, HealthyFilesystemNeverRemountsOrRepairs) {
  int ready_calls = 0;
  int remount_calls = 0;
  int repair_calls = 0;
  const auto result = recoverInternalSecondaryFilesystem(
      [&]() { ready_calls++; return true; },
      [&]() { remount_calls++; return true; },
      [&]() { repair_calls++; return true; });

  EXPECT_EQ(result, InternalSecondaryFsRecoveryResult::Ready);
  EXPECT_EQ(ready_calls, 1);
  EXPECT_EQ(remount_calls, 0);
  EXPECT_EQ(repair_calls, 0);
}

TEST(InternalSecondaryFsRecovery, SuccessfulRetryIsValidatedWithoutRepair) {
  int sequence = 0;
  int ready_calls = 0;
  int repair_calls = 0;
  const auto result = recoverInternalSecondaryFilesystem(
      [&]() {
        ready_calls++;
        EXPECT_EQ(++sequence, ready_calls == 1 ? 1 : 3);
        return ready_calls == 2;
      },
      [&]() { EXPECT_EQ(++sequence, 2); return true; },
      [&]() { repair_calls++; return true; });

  EXPECT_EQ(result, InternalSecondaryFsRecoveryResult::Remounted);
  EXPECT_EQ(ready_calls, 2);
  EXPECT_EQ(repair_calls, 0);
  EXPECT_EQ(sequence, 3);
}

TEST(InternalSecondaryFsRecovery, FailedRemountSkipsTraversalAndRepairsOnce) {
  int ready_calls = 0;
  int remount_calls = 0;
  int repair_calls = 0;
  const auto result = recoverInternalSecondaryFilesystem(
      [&]() { ready_calls++; return false; },
      [&]() { remount_calls++; return false; },
      [&]() {
        EXPECT_EQ(ready_calls, 1);
        EXPECT_EQ(remount_calls, 1);
        repair_calls++;
        return true;
      });

  EXPECT_EQ(result, InternalSecondaryFsRecoveryResult::Reinitialized);
  EXPECT_EQ(ready_calls, 1);
  EXPECT_EQ(remount_calls, 1);
  EXPECT_EQ(repair_calls, 1);
}

TEST(InternalSecondaryFsRecovery, MountedButStillCorruptFilesystemIsRepairedOnce) {
  int ready_calls = 0;
  int remount_calls = 0;
  int repair_calls = 0;
  const auto result = recoverInternalSecondaryFilesystem(
      [&]() { ready_calls++; return false; },
      [&]() { remount_calls++; return true; },
      [&]() {
        EXPECT_EQ(ready_calls, 2);
        repair_calls++;
        return true;
      });

  EXPECT_EQ(result, InternalSecondaryFsRecoveryResult::Reinitialized);
  EXPECT_EQ(ready_calls, 2);
  EXPECT_EQ(remount_calls, 1);
  EXPECT_EQ(repair_calls, 1);
}

TEST(InternalSecondaryFsRecovery, RepairFailureRemainsFailedWithoutRetryLoop) {
  int ready_calls = 0;
  int remount_calls = 0;
  int repair_calls = 0;
  const auto result = recoverInternalSecondaryFilesystem(
      [&]() { ready_calls++; return false; },
      [&]() { remount_calls++; return false; },
      [&]() { repair_calls++; return false; });

  EXPECT_EQ(result, InternalSecondaryFsRecoveryResult::Failed);
  EXPECT_EQ(ready_calls, 1);
  EXPECT_EQ(remount_calls, 1);
  EXPECT_EQ(repair_calls, 1);
}

TEST(InternalSecondaryFsRecovery, ReinitializationRequiresFormatMountAndValidation) {
  // The recovery policy delegates destructive work, but the real repair
  // callback must never report success after only formatting or mounting.
  for (int failing_step = 1; failing_step <= 3; failing_step++) {
    int sequence = 0;
    const auto result = recoverInternalSecondaryFilesystem(
        []() { return false; },
        []() { return false; },
        [&]() {
          const auto repaired = repairInternalSecondaryFilesystem(
              [&]() { return ++sequence != failing_step; },
              [&]() { return ++sequence != failing_step; },
              [&]() { return ++sequence != failing_step; });
          return repaired == InternalSecondaryFsRepairResult::Repaired;
        });
    EXPECT_EQ(result, InternalSecondaryFsRecoveryResult::Failed);
    EXPECT_EQ(sequence, failing_step);
  }
}

TEST(InternalSecondaryFsRecovery, VerifiedReinitializationCompletesExactlyOnce) {
  int sequence = 0;
  const auto result = recoverInternalSecondaryFilesystem(
      []() { return false; },
      []() { return false; },
      [&]() {
        const auto repaired = repairInternalSecondaryFilesystem(
            [&]() { EXPECT_EQ(++sequence, 1); return true; },
            [&]() { EXPECT_EQ(++sequence, 2); return true; },
            [&]() { EXPECT_EQ(++sequence, 3); return true; });
        return repaired == InternalSecondaryFsRepairResult::Repaired;
      });

  EXPECT_EQ(result, InternalSecondaryFsRecoveryResult::Reinitialized);
  EXPECT_EQ(sequence, 3);
}

int main(int argc, char** argv) {
  ::testing::InitGoogleTest(&argc, argv);
  return RUN_ALL_TESTS();
}
