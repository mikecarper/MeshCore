#include <gtest/gtest.h>

#include <helpers/nrf52/InternalSecondaryFsRepair.h>

using mesh::storage::InternalSecondaryFsRepairResult;
using mesh::storage::InternalSecondaryFsBootResult;
using mesh::storage::isInternalExtraFsReservedByApplication;
using mesh::storage::isExpectedInternalExtraFsGeometry;
using mesh::storage::prepareInternalSecondaryFilesystem;
using mesh::storage::repairInternalSecondaryFilesystem;

TEST(InternalSecondaryFsBoot, RequiresExact100KiBGeometry) {
  EXPECT_TRUE(isExpectedInternalExtraFsGeometry(0xD4000, 0x19000, 128));
  EXPECT_FALSE(isExpectedInternalExtraFsGeometry(0xED000, 0x7000, 128));
  EXPECT_FALSE(isExpectedInternalExtraFsGeometry(0xD4000, 0x7000, 128));
  EXPECT_FALSE(isExpectedInternalExtraFsGeometry(0xD4000, 0x19000, 4096));
  EXPECT_TRUE(isInternalExtraFsReservedByApplication(0xD4000));
  EXPECT_TRUE(isInternalExtraFsReservedByApplication(0xC0000));
  EXPECT_FALSE(isInternalExtraFsReservedByApplication(0xD4001));
  EXPECT_FALSE(isInternalExtraFsReservedByApplication(0xED000));
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

int main(int argc, char** argv) {
  ::testing::InitGoogleTest(&argc, argv);
  return RUN_ALL_TESTS();
}
