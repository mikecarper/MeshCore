#include <gtest/gtest.h>

#include <helpers/ESP32PartitionMigrationPolicy.h>

namespace policy = mesh::esp32_partition_migration;

TEST(Esp32PartitionMigrationPolicy, AcceptsOnlyKnownFlashSizesWithLegacyLayout) {
  EXPECT_TRUE(policy::isLegacyLayout(policy::kLegacyLayout));
  EXPECT_TRUE(policy::canMigrate(policy::kSmallRequiredFlashBytes,
                                 policy::kLegacyLayout));
  EXPECT_TRUE(policy::canMigrate(policy::kXiaoRequiredFlashBytes,
                                 policy::kLegacyLayout));
  EXPECT_TRUE(policy::canMigrate(policy::kRequiredFlashBytes,
                                 policy::kLegacyLayout));
  EXPECT_FALSE(policy::canMigrate(policy::kRequiredFlashBytes + 1,
                                  policy::kLegacyLayout));
  EXPECT_FALSE(policy::canMigrate(policy::kRequiredFlashBytes - 1,
                                  policy::kLegacyLayout));

  policy::PartitionGeometry shifted = policy::kLegacyLayout;
  shifted.spiffs_address += policy::kSectorBytes;
  EXPECT_FALSE(policy::isLegacyLayout(shifted));
  EXPECT_FALSE(policy::canMigrate(policy::kRequiredFlashBytes, shifted));
}

TEST(Esp32PartitionMigrationPolicy, ExpandedLayoutRetainsAndGrowsSpiffs) {
  EXPECT_TRUE(policy::isExpandedLayout(policy::kExpandedLayout));
  EXPECT_EQ(0x9000U, policy::kExpandedLayout.nvs_address);
  EXPECT_EQ(policy::kLegacyLayout.nvs_size, policy::kExpandedLayout.nvs_size);
  EXPECT_EQ(policy::kLegacyLayout.otadata_address,
            policy::kExpandedLayout.otadata_address);
  EXPECT_LT(policy::kLegacyLayout.spiffs_address,
            policy::kExpandedLayout.spiffs_address);
  EXPECT_GE(policy::kExpandedLayout.spiffs_size,
            policy::kLegacyLayout.spiffs_size);
  EXPECT_EQ(policy::kExpandedLayout.app1_address + policy::kExpandedLayout.app1_size,
            policy::kExpandedLayout.spiffs_address);
}

TEST(Esp32PartitionMigrationPolicy, GenericPlansAcceptStableFourEightAndSixteenMiBLayouts) {
  EXPECT_EQ(&policy::kTarget4MB, policy::targetForFlash(4U * 1024U * 1024U));
  EXPECT_EQ(&policy::kTarget8MB, policy::targetForFlash(8U * 1024U * 1024U));
  EXPECT_EQ(&policy::kTarget16MB, policy::targetForFlash(16U * 1024U * 1024U));
  EXPECT_EQ(nullptr, policy::targetForFlash(2U * 1024U * 1024U));
  EXPECT_TRUE(policy::isTargetLayout(4U * 1024U * 1024U,
                                     policy::kExpanded4MBLayout));
  EXPECT_TRUE(policy::isTargetLayout(8U * 1024U * 1024U,
                                     policy::kExpanded8MBLayout));
  EXPECT_TRUE(policy::canMigrateGeneric(8U * 1024U * 1024U,
                                         policy::kLegacyLayout));
  EXPECT_TRUE(policy::canMigrateGeneric(4U * 1024U * 1024U,
                                         policy::kLegacyLayout));
  EXPECT_TRUE(policy::canMigrateGeneric(16U * 1024U * 1024U,
                                         policy::kLegacyLayout));

  policy::PartitionGeometry moved_metadata = policy::kLegacyLayout;
  moved_metadata.nvs_address += policy::kSectorBytes;
  EXPECT_FALSE(policy::canMigrateGeneric(8U * 1024U * 1024U,
                                          moved_metadata));

  policy::PartitionGeometry out_of_bounds = policy::kLegacyLayout;
  out_of_bounds.app1_address = 8U * 1024U * 1024U - policy::kSectorBytes;
  EXPECT_FALSE(policy::canMigrateGeneric(8U * 1024U * 1024U,
                                          out_of_bounds));
}

TEST(Esp32PartitionMigrationPolicy, LoRaResumePreservesTheOldApplicationInEitherSlot) {
  for (const auto& target : {policy::kExpanded8MBLayout,
                             policy::kExpandedLayout}) {
    const auto from_a = policy::planLoRaResume(policy::kLegacyLayout, target,
                                               policy::kLegacyLayout.app0_address);
    EXPECT_TRUE(from_a.valid);
    EXPECT_EQ(0, from_a.bridge_slot);
    EXPECT_EQ(1, from_a.resume_slot);
    EXPECT_TRUE(from_a.copy_app1);
    EXPECT_FALSE(from_a.restore_identity_in_full);

    const auto from_b = policy::planLoRaResume(policy::kLegacyLayout, target,
                                               policy::kLegacyLayout.app1_address);
    EXPECT_TRUE(from_b.valid);
    EXPECT_EQ(1, from_b.bridge_slot);
    EXPECT_EQ(0, from_b.resume_slot);
    EXPECT_TRUE(from_b.copy_app1);
    EXPECT_FALSE(from_b.restore_identity_in_full);
  }

  auto overlapping = policy::kExpanded8MBLayout;
  overlapping.app1_address = policy::kLegacyLayout.app1_address;
  EXPECT_FALSE(policy::planLoRaResume(policy::kLegacyLayout, overlapping,
                                       policy::kLegacyLayout.app0_address).valid);
  EXPECT_FALSE(policy::planLoRaResume(policy::kLegacyLayout,
                                       policy::kExpandedLayout, 0x300000).valid);
  EXPECT_FALSE(policy::planLoRaResume(policy::kLegacyLayout,
                                       policy::kExpanded4MBLayout,
                                       policy::kLegacyLayout.app0_address).valid);
  const auto small_from_b = policy::planLoRaResume(
      policy::kLegacyLayout, policy::kExpanded4MBLayout,
      policy::kLegacyLayout.app1_address);
  EXPECT_TRUE(small_from_b.valid);
  EXPECT_EQ(1, small_from_b.bridge_slot);
  EXPECT_EQ(0, small_from_b.resume_slot);
  EXPECT_FALSE(small_from_b.copy_app1);
  EXPECT_TRUE(small_from_b.restore_identity_in_full);
}

TEST(Esp32PartitionMigrationPolicy, GeneratedTablePrefixContainsAllSixEntriesAndMd5) {
  EXPECT_EQ(0xE0U, policy::kExpandedPartitionTablePrefixBytes);
  EXPECT_EQ(0xAA, policy::kExpandedPartitionTablePrefix[0]);
  EXPECT_EQ(0x50, policy::kExpandedPartitionTablePrefix[1]);
  EXPECT_EQ(0xAA, policy::kExpandedPartitionTablePrefix[0xA0]);
  EXPECT_EQ(0x50, policy::kExpandedPartitionTablePrefix[0xA1]);
  EXPECT_EQ(0xEB, policy::kExpandedPartitionTablePrefix[0xC0]);
  EXPECT_EQ(0xEB, policy::kExpandedPartitionTablePrefix[0xC1]);
  EXPECT_EQ(policy::kExpandedPartitionTablePrefixBytes,
            policy::kExpanded8MBPartitionTablePrefixBytes);
  EXPECT_EQ(0x33, policy::kExpanded8MBPartitionTablePrefix[0x4A]);
  EXPECT_EQ(0x34, policy::kExpanded8MBPartitionTablePrefix[0x66]);
  EXPECT_EQ(0x67, policy::kExpanded8MBPartitionTablePrefix[0x86]);
  EXPECT_EQ(0xC0U, policy::kExpanded4MBPartitionTablePrefixBytes);
  EXPECT_EQ(0xEB, policy::kExpanded4MBPartitionTablePrefix[0xA0]);
  EXPECT_EQ(0xEB, policy::kExpanded4MBPartitionTablePrefix[0xA1]);
}

int main(int argc, char** argv) {
  ::testing::InitGoogleTest(&argc, argv);
  return RUN_ALL_TESTS();
}
