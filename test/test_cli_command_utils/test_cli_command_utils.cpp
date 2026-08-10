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

TEST(CLICommandUtils, ParsesTerminalChannelMessagesWithUtf8Text) {
  using mesh::cli::TerminalChannelCommandMatch;
  mesh::cli::TerminalChannelMessage message;

  EXPECT_EQ(TerminalChannelCommandMatch::Valid,
            mesh::cli::parseTerminalChannelMessage(
                "channel #rgdata Hello from Eugene 👋", message));
  EXPECT_EQ(7u, message.selector_len);
  EXPECT_EQ(0, memcmp("#rgdata", message.selector, message.selector_len));
  EXPECT_STREQ("Hello from Eugene 👋", message.text);
  EXPECT_TRUE(mesh::cli::terminalChannelNameMatches(message, "#rgdata"));
  EXPECT_FALSE(mesh::cli::terminalChannelNameMatches(message, "#RGDATA"));
}

TEST(CLICommandUtils, ParsesTerminalChannelSlotsStrictly) {
  using mesh::cli::TerminalChannelCommandMatch;
  mesh::cli::TerminalChannelMessage message;
  size_t channel_index = 99;

  EXPECT_EQ(TerminalChannelCommandMatch::Valid,
            mesh::cli::parseTerminalChannelMessage(
                "channel 2 Slot message", message));
  EXPECT_TRUE(mesh::cli::parseTerminalChannelIndex(message, 8,
                                                   channel_index));
  EXPECT_EQ(2u, channel_index);

  EXPECT_EQ(TerminalChannelCommandMatch::Valid,
            mesh::cli::parseTerminalChannelMessage(
                "channel 8 Out of range", message));
  EXPECT_FALSE(mesh::cli::parseTerminalChannelIndex(message, 8,
                                                    channel_index));

  EXPECT_EQ(TerminalChannelCommandMatch::Valid,
            mesh::cli::parseTerminalChannelMessage(
                "channel 2name Named channel", message));
  EXPECT_FALSE(mesh::cli::parseTerminalChannelIndex(message, 8,
                                                    channel_index));
  EXPECT_TRUE(mesh::cli::terminalChannelNameMatches(message, "2name"));
}

TEST(CLICommandUtils, RejectsIncompleteTerminalChannelMessages) {
  using mesh::cli::TerminalChannelCommandMatch;
  mesh::cli::TerminalChannelMessage message;

  EXPECT_EQ(TerminalChannelCommandMatch::MissingSelector,
            mesh::cli::parseTerminalChannelMessage("channel", message));
  EXPECT_EQ(TerminalChannelCommandMatch::MissingMessage,
            mesh::cli::parseTerminalChannelMessage("channel #rgdata", message));
  EXPECT_EQ(TerminalChannelCommandMatch::NoMatch,
            mesh::cli::parseTerminalChannelMessage("channels", message));
  EXPECT_EQ(TerminalChannelCommandMatch::NoMatch,
            mesh::cli::parseTerminalChannelMessage("channelized test", message));
}

TEST(CLICommandUtils, ParsesTerminalLoginAndRemoteCommandArguments) {
  using mesh::cli::TerminalArgumentCommandMatch;
  const char* argument = nullptr;

  EXPECT_EQ(TerminalArgumentCommandMatch::Valid,
            mesh::cli::parseTerminalArgumentCommand(
                "login admin password", "login", argument));
  EXPECT_STREQ("admin password", argument);

  EXPECT_EQ(TerminalArgumentCommandMatch::Valid,
            mesh::cli::parseTerminalArgumentCommand(
                "LOGIN CaseSensitivePassword", "login", argument));
  EXPECT_STREQ("CaseSensitivePassword", argument);

  EXPECT_EQ(TerminalArgumentCommandMatch::Valid,
            mesh::cli::parseTerminalArgumentCommand(
                "cmd\tget stats", "cmd", argument));
  EXPECT_STREQ("get stats", argument);

  EXPECT_EQ(TerminalArgumentCommandMatch::MissingArgument,
            mesh::cli::parseTerminalArgumentCommand(
                "login   ", "login", argument));
  EXPECT_EQ(nullptr, argument);
  EXPECT_EQ(TerminalArgumentCommandMatch::NoMatch,
            mesh::cli::parseTerminalArgumentCommand(
                "logins password", "login", argument));
}

TEST(CLICommandUtils, ParsesTerminalDirectAndExplicitPaths) {
  using mesh::cli::TerminalPathMode;
  using mesh::cli::TerminalPathParseResult;
  uint8_t route[64] = {};
  mesh::cli::TerminalPath path;

  EXPECT_EQ(TerminalPathParseResult::Valid,
            mesh::cli::parseTerminalPath(
                "direct", route, sizeof(route), 63, path));
  EXPECT_EQ(TerminalPathMode::Direct, path.mode);
  EXPECT_EQ(0, path.encoded_len);
  EXPECT_EQ(0, path.hop_count);
  EXPECT_EQ(0u, path.byte_len);

  EXPECT_EQ(TerminalPathParseResult::Valid,
            mesh::cli::parseTerminalPath(
                "clear  ", route, sizeof(route), 63, path));
  EXPECT_EQ(TerminalPathMode::Clear, path.mode);

  EXPECT_EQ(TerminalPathParseResult::Valid,
            mesh::cli::parseTerminalPath(
                " A1B2C3, d4e5f6,010203 ", route, sizeof(route), 63,
                path));
  EXPECT_EQ(TerminalPathMode::Explicit, path.mode);
  EXPECT_EQ(3, path.hash_size);
  EXPECT_EQ(3, path.hop_count);
  EXPECT_EQ(0x83, path.encoded_len);
  EXPECT_EQ(9u, path.byte_len);
  const uint8_t expected[] = {
      0xA1, 0xB2, 0xC3, 0xD4, 0xE5, 0xF6, 0x01, 0x02, 0x03,
  };
  EXPECT_EQ(0, memcmp(expected, route, sizeof(expected)));
}

TEST(CLICommandUtils, RejectsMalformedTerminalPaths) {
  using mesh::cli::TerminalPathParseResult;
  uint8_t route[4] = {};
  mesh::cli::TerminalPath path;

  EXPECT_EQ(TerminalPathParseResult::Missing,
            mesh::cli::parseTerminalPath(
                nullptr, route, sizeof(route), 63, path));
  EXPECT_EQ(TerminalPathParseResult::Missing,
            mesh::cli::parseTerminalPath(
                "   ", route, sizeof(route), 63, path));
  EXPECT_EQ(TerminalPathParseResult::InvalidPrefix,
            mesh::cli::parseTerminalPath(
                "GG", route, sizeof(route), 63, path));
  EXPECT_EQ(TerminalPathParseResult::InvalidPrefix,
            mesh::cli::parseTerminalPath(
                "AA,", route, sizeof(route), 63, path));
  EXPECT_EQ(TerminalPathParseResult::MixedPrefixSize,
            mesh::cli::parseTerminalPath(
                "AA,BBBB", route, sizeof(route), 63, path));
  EXPECT_EQ(TerminalPathParseResult::InvalidSeparator,
            mesh::cli::parseTerminalPath(
                "AA BB", route, sizeof(route), 63, path));
  EXPECT_EQ(TerminalPathParseResult::TooManyHops,
            mesh::cli::parseTerminalPath(
                "AA,BB", route, sizeof(route), 1, path));
  EXPECT_EQ(TerminalPathParseResult::RouteTooLong,
            mesh::cli::parseTerminalPath(
                "A1B2C3,D4E5F6", route, sizeof(route), 63, path));
}

TEST(CLICommandUtils, MasksOnlyTerminalLoginPasswordInput) {
  EXPECT_FALSE(mesh::cli::shouldMaskTerminalInput("login"));
  EXPECT_FALSE(mesh::cli::shouldMaskTerminalInput("login "));
  EXPECT_TRUE(mesh::cli::shouldMaskTerminalInput("login s"));
  EXPECT_TRUE(mesh::cli::shouldMaskTerminalInput("LOGIN s"));
  EXPECT_TRUE(mesh::cli::shouldMaskTerminalInput("  login secret phrase"));
  EXPECT_FALSE(mesh::cli::shouldMaskTerminalInput("cmd login secret"));
  EXPECT_FALSE(mesh::cli::shouldMaskTerminalInput("login-status"));
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

TEST(CLICommandUtils, ParsesRecentRepeaterListAndPageQueries) {
  using mesh::cli::RecentRepeaterGetMatch;
  mesh::cli::RecentRepeaterGetQuery query;

  EXPECT_EQ(RecentRepeaterGetMatch::Valid,
            mesh::cli::parseRecentRepeaterGet("recent.repeater", query));
  EXPECT_EQ(1, query.page);
  EXPECT_EQ(0, query.search_prefix_len);

  EXPECT_EQ(RecentRepeaterGetMatch::Valid,
            mesh::cli::parseRecentRepeaterGet(
                "recent.repeaters page 17", query));
  EXPECT_EQ(17, query.page);
  EXPECT_EQ(0, query.search_prefix_len);

  EXPECT_EQ(RecentRepeaterGetMatch::Valid,
            mesh::cli::parseRecentRepeaterGet("recent.repeaters 0", query));
  EXPECT_EQ(1, query.page);
}

TEST(CLICommandUtils, ParsesRecentRepeaterHexSearchesAndOptionalPages) {
  using mesh::cli::RecentRepeaterGetMatch;
  mesh::cli::RecentRepeaterGetQuery query;

  EXPECT_EQ(RecentRepeaterGetMatch::Valid,
            mesh::cli::parseRecentRepeaterGet(
                "recent.repeaters search 86", query));
  ASSERT_EQ(1, query.search_prefix_len);
  EXPECT_EQ(0x86, query.search_prefix[0]);
  EXPECT_EQ(1, query.page);

  EXPECT_EQ(RecentRepeaterGetMatch::Valid,
            mesh::cli::parseRecentRepeaterGet(
                "recent.repeaters search 860c 2", query));
  ASSERT_EQ(2, query.search_prefix_len);
  EXPECT_EQ(0x86, query.search_prefix[0]);
  EXPECT_EQ(0x0c, query.search_prefix[1]);
  EXPECT_EQ(2, query.page);

  EXPECT_EQ(RecentRepeaterGetMatch::Valid,
            mesh::cli::parseRecentRepeaterGet(
                "recent.repeater search 860CCA page 3", query));
  ASSERT_EQ(3, query.search_prefix_len);
  EXPECT_EQ(0x86, query.search_prefix[0]);
  EXPECT_EQ(0x0c, query.search_prefix[1]);
  EXPECT_EQ(0xca, query.search_prefix[2]);
  EXPECT_EQ(3, query.page);
}

TEST(CLICommandUtils, RejectsMalformedRecentRepeaterSearches) {
  using mesh::cli::RecentRepeaterGetMatch;
  mesh::cli::RecentRepeaterGetQuery query;

  EXPECT_EQ(RecentRepeaterGetMatch::Invalid,
            mesh::cli::parseRecentRepeaterGet(
                "recent.repeaters search", query));
  EXPECT_EQ(RecentRepeaterGetMatch::Invalid,
            mesh::cli::parseRecentRepeaterGet(
                "recent.repeaters search 860", query));
  EXPECT_EQ(RecentRepeaterGetMatch::Invalid,
            mesh::cli::parseRecentRepeaterGet(
                "recent.repeaters search 8G", query));
  EXPECT_EQ(RecentRepeaterGetMatch::Invalid,
            mesh::cli::parseRecentRepeaterGet(
                "recent.repeaters search 860C extra", query));
  EXPECT_EQ(RecentRepeaterGetMatch::Invalid,
            mesh::cli::parseRecentRepeaterGet(
                "recent.repeaters nope", query));
  EXPECT_EQ(RecentRepeaterGetMatch::NoMatch,
            mesh::cli::parseRecentRepeaterGet(
                "recent.repeaters.extra", query));
}

TEST(CLICommandUtils, FormatsRecentRepeaterAgeWithCompactSuffix) {
  char age[12];

  mesh::cli::formatRecentRepeaterAge(age, sizeof(age), 0);
  EXPECT_STREQ("0s", age);
  mesh::cli::formatRecentRepeaterAge(age, sizeof(age), 59);
  EXPECT_STREQ("59s", age);
  mesh::cli::formatRecentRepeaterAge(age, sizeof(age), 60);
  EXPECT_STREQ("1m", age);
  mesh::cli::formatRecentRepeaterAge(age, sizeof(age), 3599);
  EXPECT_STREQ("59m", age);
  mesh::cli::formatRecentRepeaterAge(age, sizeof(age), 3600);
  EXPECT_STREQ("1h", age);
  mesh::cli::formatRecentRepeaterAge(age, sizeof(age), 26UL * 3600UL);
  EXPECT_STREQ("26h", age);
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
