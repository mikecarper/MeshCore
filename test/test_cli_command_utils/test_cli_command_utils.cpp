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

TEST(CLICommandUtils, ParsesStrictDecimalValues) {
  float value = 0.0f;

  EXPECT_TRUE(mesh::cli::parseDecimalStrict("910.525", value));
  EXPECT_FLOAT_EQ(910.525f, value);
  EXPECT_TRUE(mesh::cli::parseDecimalStrict(" -122.25 ", value));
  EXPECT_FLOAT_EQ(-122.25f, value);
  EXPECT_TRUE(mesh::cli::parseDecimalStrict("+.5", value));
  EXPECT_FLOAT_EQ(0.5f, value);
  EXPECT_TRUE(mesh::cli::parseDecimalStrict("7.", value));
  EXPECT_FLOAT_EQ(7.0f, value);
}

TEST(CLICommandUtils, RejectsNonDecimalOrOverflowValues) {
  float value = 123.0f;

  EXPECT_FALSE(mesh::cli::parseDecimalStrict(nullptr, value));
  EXPECT_FALSE(mesh::cli::parseDecimalStrict("", value));
  EXPECT_FALSE(mesh::cli::parseDecimalStrict(".", value));
  EXPECT_FALSE(mesh::cli::parseDecimalStrict("1.2x", value));
  EXPECT_FALSE(mesh::cli::parseDecimalStrict("1e3", value));
  EXPECT_FALSE(mesh::cli::parseDecimalStrict("4294967296", value));
  EXPECT_FLOAT_EQ(123.0f, value);
}

int main(int argc, char** argv) {
  ::testing::InitGoogleTest(&argc, argv);
  return RUN_ALL_TESTS();
}
