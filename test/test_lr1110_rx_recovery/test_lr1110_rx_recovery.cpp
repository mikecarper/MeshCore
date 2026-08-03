#include <gtest/gtest.h>

#include "helpers/radiolib/LR1110RxRecovery.h"

TEST(LR1110RxRecovery, RejectsNullAndShortBuffers) {
  EXPECT_FALSE(mesh::hasLR1110RxBufferShiftSignature(nullptr, 32));

  const uint8_t empty = 0;
  EXPECT_FALSE(mesh::hasLR1110RxBufferShiftSignature(&empty, 0));

  const uint8_t onlyPrefix[] = {0x00, 0x00, 0x00, 0x00};
  EXPECT_FALSE(mesh::hasLR1110RxBufferShiftSignature(
      onlyPrefix, sizeof(onlyPrefix)));
}

TEST(LR1110RxRecovery, DetectsCapturedFourByteShift) {
  const uint8_t captured[] = {
      0x00, 0x00, 0x00, 0x00, 0x15, 0x04, 0x1c, 0x7f, 0x7a, 0x56};
  EXPECT_TRUE(mesh::hasLR1110RxBufferShiftSignature(
      captured, sizeof(captured)));

  const uint8_t accumulatedShift[] = {
      0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x15, 0x04};
  EXPECT_TRUE(mesh::hasLR1110RxBufferShiftSignature(
      accumulatedShift, sizeof(accumulatedShift)));
}

TEST(LR1110RxRecovery, AllowsOrdinaryAndValidScopedPrefixes) {
  const uint8_t ordinaryFlood[] = {0x15, 0x04, 0x1c, 0x7f, 0x7a, 0x56};
  EXPECT_FALSE(mesh::hasLR1110RxBufferShiftSignature(
      ordinaryFlood, sizeof(ordinaryFlood)));

  // Header 00 is legal for a transport-flood request, but a real scope code
  // makes the prefix differ from the LR1110's four injected zero bytes.
  const uint8_t scopedRequest[] = {
      0x00, 0x34, 0x12, 0x78, 0x56, 0x01, 0xaa};
  EXPECT_FALSE(mesh::hasLR1110RxBufferShiftSignature(
      scopedRequest, sizeof(scopedRequest)));

  const uint8_t threeZeros[] = {0x00, 0x00, 0x00, 0x15, 0x04, 0x1c};
  EXPECT_FALSE(mesh::hasLR1110RxBufferShiftSignature(
      threeZeros, sizeof(threeZeros)));
}

int main(int argc, char** argv) {
  ::testing::InitGoogleTest(&argc, argv);
  return RUN_ALL_TESTS();
}
