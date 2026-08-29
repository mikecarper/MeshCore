#include <gtest/gtest.h>

#include <cstring>

#include <helpers/CompanionTerminalDiagnostics.h>

TEST(CompanionTerminalDiagnostics, ReportsEveryEsp32MemoryField) {
  const mesh::CompanionMemoryDiagnostics diagnostics = {
    66244, 2516, 63428, 61004, 42812, 6442475, 8386055, 3, 512
  };
  char output[160] = {};

  ASSERT_TRUE(mesh::formatCompanionMemoryDiagnostics(
      output, sizeof(output), diagnostics));
  EXPECT_STREQ(
      "Heap free=66244 min=2516 max=63428 int=61004/42812 "
      "PSRAM=6442475/8386055 queue=3/512",
      output);
}

TEST(CompanionTerminalDiagnostics, TruthfullyReportsNoPsram) {
  const mesh::CompanionMemoryDiagnostics diagnostics = {
    104857, 98234, 90112, 104857, 90112, 0, 0, 0, 16
  };
  char output[160] = {};

  ASSERT_TRUE(mesh::formatCompanionMemoryDiagnostics(
      output, sizeof(output), diagnostics));
  EXPECT_NE(nullptr, strstr(output, "PSRAM=0/0"));
  EXPECT_NE(nullptr, strstr(output, "queue=0/16"));
}

TEST(CompanionTerminalDiagnostics, DetectsAReplyBufferThatIsTooSmall) {
  const mesh::CompanionMemoryDiagnostics diagnostics = {
    UINT32_MAX, UINT32_MAX, UINT32_MAX, UINT32_MAX, UINT32_MAX,
    UINT32_MAX, UINT32_MAX, 512, 512
  };
  char output[24] = {};

  EXPECT_FALSE(mesh::formatCompanionMemoryDiagnostics(
      output, sizeof(output), diagnostics));
  EXPECT_EQ('\0', output[sizeof(output) - 1]);
}

int main(int argc, char** argv) {
  ::testing::InitGoogleTest(&argc, argv);
  return RUN_ALL_TESTS();
}
