#include <gtest/gtest.h>

#include <helpers/CLICommandUtils.h>

TEST(CLICommandUtils, NormalizesCapitalizedSetVerb) {
  char command[] = "Set altpath 600000,0d2784,F8DADA";

  mesh::cli::normalizeCommandVerb(command);

  EXPECT_STREQ("set altpath 600000,0d2784,F8DADA", command);
}

TEST(CLICommandUtils, NormalizesAnyCommandVerbCase) {
  char get_command[] = "GET outpath";
  char clear_command[] = "cLeAr recent.repeater";

  mesh::cli::normalizeCommandVerb(get_command);
  mesh::cli::normalizeCommandVerb(clear_command);

  EXPECT_STREQ("get outpath", get_command);
  EXPECT_STREQ("clear recent.repeater", clear_command);
}

TEST(CLICommandUtils, PreservesCaseSensitiveArguments) {
  char command[] = "SET name RidgeNode-A";

  mesh::cli::normalizeCommandVerb(command);

  EXPECT_STREQ("set name RidgeNode-A", command);
}

TEST(CLICommandUtils, HandlesLeadingWhitespaceAndSingleWordCommands) {
  char spaced_command[] = "  Get outpath";
  char single_command[] = "PowerSaving";

  mesh::cli::normalizeCommandVerb(spaced_command);
  mesh::cli::normalizeCommandVerb(single_command);

  EXPECT_STREQ("  get outpath", spaced_command);
  EXPECT_STREQ("powersaving", single_command);
}

int main(int argc, char** argv) {
  ::testing::InitGoogleTest(&argc, argv);
  return RUN_ALL_TESTS();
}
