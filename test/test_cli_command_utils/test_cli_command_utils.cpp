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

TEST(CLICommandUtils, ClassifiesStandaloneWiFiGetKeysExactly) {
  using mesh::cli::StandaloneWiFiKey;

  EXPECT_EQ(StandaloneWiFiKey::SSID,
            mesh::cli::classifyStandaloneWiFiGet("wifi.ssid"));
  EXPECT_EQ(StandaloneWiFiKey::Status,
            mesh::cli::classifyStandaloneWiFiGet("wifi.status"));
  EXPECT_EQ(StandaloneWiFiKey::PowerSave,
            mesh::cli::classifyStandaloneWiFiGet("wifi.powersave"));
  EXPECT_EQ(StandaloneWiFiKey::CLI,
            mesh::cli::classifyStandaloneWiFiGet("wifi.cli"));
  EXPECT_EQ(StandaloneWiFiKey::None,
            mesh::cli::classifyStandaloneWiFiGet("wifi.pwd"));
  EXPECT_EQ(StandaloneWiFiKey::None,
            mesh::cli::classifyStandaloneWiFiGet("wifi.status.extra"));
}

TEST(CLICommandUtils, ClassifiesStandaloneWiFiSetKeysAndPreservesValues) {
  using mesh::cli::StandaloneWiFiKey;
  const char* value = nullptr;

  EXPECT_EQ(StandaloneWiFiKey::SSID,
            mesh::cli::classifyStandaloneWiFiSet(
                "wifi.ssid Slow Fi", &value));
  EXPECT_STREQ("Slow Fi", value);

  EXPECT_EQ(StandaloneWiFiKey::Password,
            mesh::cli::classifyStandaloneWiFiSet("wifi.pwd", &value));
  EXPECT_STREQ("", value);

  EXPECT_EQ(StandaloneWiFiKey::PowerSave,
            mesh::cli::classifyStandaloneWiFiSet(
                "wifi.powersave max", &value));
  EXPECT_STREQ("max", value);

  EXPECT_EQ(StandaloneWiFiKey::CLI,
            mesh::cli::classifyStandaloneWiFiSet("wifi.cli on", &value));
  EXPECT_STREQ("on", value);

  EXPECT_EQ(StandaloneWiFiKey::None,
            mesh::cli::classifyStandaloneWiFiSet(
                "wifi.powersaver max", &value));
  EXPECT_EQ(nullptr, value);
}

TEST(CLICommandUtils, ValidatesStandaloneWiFiValues) {
  EXPECT_TRUE(mesh::cli::standaloneWiFiSSIDValid("SlowFi"));
  EXPECT_TRUE(mesh::cli::standaloneWiFiSSIDValid(
      "1234567890123456789012345678901"));
  EXPECT_FALSE(mesh::cli::standaloneWiFiSSIDValid(""));
  EXPECT_FALSE(mesh::cli::standaloneWiFiSSIDValid(
      "12345678901234567890123456789012"));

  EXPECT_TRUE(mesh::cli::standaloneWiFiPasswordValid(""));
  EXPECT_TRUE(mesh::cli::standaloneWiFiPasswordValid(
      "123456789012345678901234567890123456789012345678901234567890123"));
  EXPECT_FALSE(mesh::cli::standaloneWiFiPasswordValid(
      "1234567890123456789012345678901234567890123456789012345678901234"));

  uint8_t power_save = 99;
  EXPECT_TRUE(mesh::cli::parseStandaloneWiFiPowerSave("min", power_save));
  EXPECT_EQ(0, power_save);
  EXPECT_TRUE(mesh::cli::parseStandaloneWiFiPowerSave("none", power_save));
  EXPECT_EQ(1, power_save);
  EXPECT_TRUE(mesh::cli::parseStandaloneWiFiPowerSave("max", power_save));
  EXPECT_EQ(2, power_save);
  EXPECT_FALSE(mesh::cli::parseStandaloneWiFiPowerSave("off", power_save));
}

TEST(CLICommandUtils, MatchesDiscoverNeighborsWithoutPrefixCollisions) {
  using mesh::cli::NoArgCommandMatch;

  EXPECT_EQ(NoArgCommandMatch::Exact,
            mesh::cli::matchNoArgCommand(
                "discover.neighbors", "discover.neighbors"));
  EXPECT_EQ(NoArgCommandMatch::Exact,
            mesh::cli::matchNoArgCommand(
                "discover.neighbors   ", "discover.neighbors"));
  EXPECT_EQ(NoArgCommandMatch::HasArguments,
            mesh::cli::matchNoArgCommand(
                "discover.neighbors now", "discover.neighbors"));
  EXPECT_EQ(NoArgCommandMatch::NoMatch,
            mesh::cli::matchNoArgCommand(
                "discover.neighbors.extra", "discover.neighbors"));
}

TEST(CLICommandUtils, FormatsUsefulUnknownSettingErrors) {
  char reply[64] = "";

  mesh::cli::formatUnknownSetting(reply, sizeof(reply), "wifi.typo");

  EXPECT_STREQ("Error: unknown setting: wifi.typo", reply);
  EXPECT_EQ(nullptr, strstr(reply, "??:"));
}

int main(int argc, char** argv) {
  ::testing::InitGoogleTest(&argc, argv);
  return RUN_ALL_TESTS();
}
