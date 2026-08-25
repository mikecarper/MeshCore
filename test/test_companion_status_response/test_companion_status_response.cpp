#include <gtest/gtest.h>

#include <helpers/CompanionStatusResponse.h>

TEST(CompanionStatusResponse, RequiresTheExactPendingRequestTag) {
  EXPECT_FALSE(mesh::companionStatusTagMatches(0, 0));
  EXPECT_FALSE(mesh::companionStatusTagMatches(0, 0x12345678));
  EXPECT_FALSE(mesh::companionStatusTagMatches(0x12345678, 0x87654321));
  EXPECT_TRUE(mesh::companionStatusTagMatches(0x12345678, 0x12345678));
}

TEST(CompanionStatusResponse, RejectsThreeEntryAclPayloadAsStatus) {
  // An ACL response is tag + seven bytes per entry. Three entries produced
  // the 21-byte payload that the app incorrectly attempted to parse as stats.
  constexpr size_t three_entry_acl_response = 4 + (3 * 7);
  EXPECT_FALSE(
      mesh::companionStatusResponseIsLongEnough(three_entry_acl_response));
}

TEST(CompanionStatusResponse, AcceptsLegacyMinimumAndCurrentStatusSizes) {
  EXPECT_FALSE(mesh::companionStatusResponseIsLongEnough(
      mesh::COMPANION_MIN_STATUS_RESPONSE_SIZE - 1));
  EXPECT_TRUE(mesh::companionStatusResponseIsLongEnough(
      mesh::COMPANION_MIN_STATUS_RESPONSE_SIZE));
  EXPECT_TRUE(mesh::companionStatusResponseIsLongEnough(4 + 56));
}

int main(int argc, char** argv) {
  ::testing::InitGoogleTest(&argc, argv);
  return RUN_ALL_TESTS();
}
