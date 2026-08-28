#include <gtest/gtest.h>

#include <string>

#include <helpers/CLICommandUtils.h>
#include <helpers/ContactListOrder.h>
#include <helpers/TerminalCommandTracker.h>
#include <helpers/TerminalDisplayFilter.h>
#include <helpers/WiFiChannelPolicy.h>
#include <helpers/WiFiPowerSave.h>
#include <helpers/bridges/ESPNowBridgeFormat.h>
#include <helpers/radiolib/RadioPowerLimits.h>

TEST(ContactListOrder, SortsFavoritesBeforeNewerNonFavorites) {
  EXPECT_LT(mesh::compareContactListOrder(0x01, 100, 0x00, 200), 0);
  EXPECT_GT(mesh::compareContactListOrder(0x00, 200, 0x01, 100), 0);
}

TEST(ContactListOrder, SortsEachGroupByNewestAdvertisement) {
  EXPECT_LT(mesh::compareContactListOrder(0x01, 200, 0x01, 100), 0);
  EXPECT_GT(mesh::compareContactListOrder(0x00, 100, 0x00, 200), 0);
  EXPECT_EQ(0, mesh::compareContactListOrder(0x01, 100, 0x01, 100));
}

TEST(CLICommandUtils, LabelsInboundDirectRouteAsRouted) {
  EXPECT_STREQ("ROUTED", mesh::cli::terminalInboundRouteLabel(true));
  EXPECT_STREQ("FLOOD", mesh::cli::terminalInboundRouteLabel(false));
}

TEST(TerminalDisplayFilter, UsesQuietDefaultsWithEmergencyVisible) {
  mesh::TerminalDisplayFilter filter;

  EXPECT_FALSE(filter.shouldShowAdvert());
  EXPECT_FALSE(filter.shouldShowChannel(false));
  EXPECT_TRUE(filter.shouldShowChannel(true));
}

TEST(TerminalDisplayFilter, KeepsChannelAndEmergencyControlsIndependent) {
  mesh::TerminalDisplayFilter filter;

  filter.setEnabled(mesh::TerminalDisplayCategory::Channels, true);
  filter.setEnabled(mesh::TerminalDisplayCategory::Emergency, false);
  filter.setEnabled(mesh::TerminalDisplayCategory::Adverts, true);

  EXPECT_TRUE(filter.shouldShowAdvert());
  EXPECT_TRUE(filter.shouldShowChannel(false));
  EXPECT_FALSE(filter.shouldShowChannel(true));
}

TEST(TerminalDisplayFilter, ParsesStatusAndUpdatesCaseInsensitively) {
  using mesh::TerminalDisplayCategory;
  using mesh::TerminalDisplayParseResult;
  mesh::TerminalDisplayCommand command;

  EXPECT_EQ(TerminalDisplayParseResult::StatusAll,
            mesh::parseTerminalDisplayCommand(NULL, command));
  EXPECT_EQ(TerminalDisplayParseResult::StatusOne,
            mesh::parseTerminalDisplayCommand(" channels ", command));
  EXPECT_EQ(TerminalDisplayCategory::Channels, command.category);
  EXPECT_EQ(TerminalDisplayParseResult::Updated,
            mesh::parseTerminalDisplayCommand(" Emergency OFF ", command));
  EXPECT_EQ(TerminalDisplayCategory::Emergency, command.category);
  EXPECT_FALSE(command.enabled);
  EXPECT_EQ(TerminalDisplayParseResult::Updated,
            mesh::parseTerminalDisplayCommand("advertisements on", command));
  EXPECT_EQ(TerminalDisplayCategory::Adverts, command.category);
  EXPECT_TRUE(command.enabled);
}

TEST(TerminalDisplayFilter, RejectsUnknownCategoriesAndValues) {
  mesh::TerminalDisplayCommand command;

  EXPECT_EQ(mesh::TerminalDisplayParseResult::InvalidCategory,
            mesh::parseTerminalDisplayCommand("messages on", command));
  EXPECT_EQ(mesh::TerminalDisplayParseResult::InvalidValue,
            mesh::parseTerminalDisplayCommand("channels maybe", command));
  EXPECT_EQ(mesh::TerminalDisplayParseResult::InvalidValue,
            mesh::parseTerminalDisplayCommand("emergency off extra", command));
}

TEST(TerminalCommandTracker, MatchesReplyAndReportsRoundTrip) {
  mesh::TerminalCommandTracker<4> tracker;
  const uint8_t target[] = {0x11, 0x22, 0x33, 0x44};
  const uint8_t other[] = {0x11, 0x22, 0x33, 0x45};
  uint32_t elapsed = 99;

  ASSERT_TRUE(tracker.begin(target, 1000, 3000));
  EXPECT_FALSE(tracker.begin(target, 1100, 3000));
  EXPECT_FALSE(tracker.takeReply(other, 1500, elapsed));
  EXPECT_EQ(0UL, elapsed);
  EXPECT_TRUE(tracker.isPending());
  EXPECT_TRUE(tracker.takeReply(target, 1750, elapsed));
  EXPECT_EQ(750UL, elapsed);
  EXPECT_FALSE(tracker.isPending());
}

TEST(TerminalCommandTracker, ExpiresSafelyAcrossMillisRollover) {
  mesh::TerminalCommandTracker<4> tracker;
  const uint8_t target[] = {0xAA, 0xBB, 0xCC, 0xDD};
  uint32_t elapsed = 0;

  ASSERT_TRUE(tracker.begin(target, 0xFFFFFFF0UL, 32));
  EXPECT_FALSE(tracker.expire(0x0000000FUL, elapsed));
  EXPECT_TRUE(tracker.expire(0x00000010UL, elapsed));
  EXPECT_EQ(32UL, elapsed);
  EXPECT_FALSE(tracker.isPending());
}

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

TEST(CLICommandUtils, ParsesCapitalizedRecipientWithRepeatedWhitespace) {
  using mesh::cli::TerminalArgumentCommandMatch;
  char command[] = "To  SEA Mercerwood";
  const char* recipient = nullptr;

  mesh::cli::normalizeCommandVerb(command);

  EXPECT_STREQ("to  SEA Mercerwood", command);
  EXPECT_EQ(TerminalArgumentCommandMatch::Valid,
            mesh::cli::parseTerminalArgumentCommand(
                command, "to", recipient));
  EXPECT_STREQ("SEA Mercerwood", recipient);
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

TEST(CLICommandUtils, ParsesSpaceSeparatedTerminalPath) {
  using mesh::cli::TerminalPathMode;
  using mesh::cli::TerminalPathParseResult;
  uint8_t route[6] = {};
  mesh::cli::TerminalPath path;

  ASSERT_EQ(TerminalPathParseResult::Valid,
            mesh::cli::parseTerminalPath(
                "7773D0 7E7662", route, sizeof(route), 63, path));

  const uint8_t expected[] = {0x77, 0x73, 0xD0, 0x7E, 0x76, 0x62};
  EXPECT_EQ(TerminalPathMode::Explicit, path.mode);
  EXPECT_EQ(3, path.hash_size);
  EXPECT_EQ(2, path.hop_count);
  EXPECT_EQ(0x82, path.encoded_len);
  EXPECT_EQ(sizeof(expected), path.byte_len);
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
  EXPECT_EQ(TerminalPathParseResult::InvalidPrefix,
            mesh::cli::parseTerminalPath(
                "AA,,BB", route, sizeof(route), 63, path));
  EXPECT_EQ(TerminalPathParseResult::TooManyHops,
            mesh::cli::parseTerminalPath(
                "AA,BB", route, sizeof(route), 1, path));
  EXPECT_EQ(TerminalPathParseResult::RouteTooLong,
            mesh::cli::parseTerminalPath(
                "A1B2C3,D4E5F6", route, sizeof(route), 63, path));
}

TEST(CLICommandUtils, MasksTerminalPasswordInput) {
  EXPECT_FALSE(mesh::cli::shouldMaskTerminalInput("login"));
  EXPECT_FALSE(mesh::cli::shouldMaskTerminalInput("login "));
  EXPECT_TRUE(mesh::cli::shouldMaskTerminalInput("login s"));
  EXPECT_TRUE(mesh::cli::shouldMaskTerminalInput("LOGIN s"));
  EXPECT_TRUE(mesh::cli::shouldMaskTerminalInput("  login secret phrase"));
  EXPECT_FALSE(mesh::cli::shouldMaskTerminalInput("set wifi.pwd"));
  EXPECT_FALSE(mesh::cli::shouldMaskTerminalInput("set wifi.pwd "));
  EXPECT_TRUE(mesh::cli::shouldMaskTerminalInput("set wifi.pwd s"));
  EXPECT_TRUE(mesh::cli::shouldMaskTerminalInput("SET WIFI.PWD secret phrase"));
  EXPECT_TRUE(mesh::cli::shouldMaskTerminalInput("  set   wifi.pwd secret"));
  EXPECT_FALSE(mesh::cli::shouldMaskTerminalInput("cmd login secret"));
  EXPECT_FALSE(mesh::cli::shouldMaskTerminalInput("cmd set wifi.pwd secret"));
  EXPECT_FALSE(mesh::cli::shouldMaskTerminalInput("set wifi.pwd-status secret"));
  EXPECT_FALSE(mesh::cli::shouldMaskTerminalInput("login-status"));

  const char* login = "  login secret phrase";
  EXPECT_STREQ("secret phrase", mesh::cli::terminalPasswordInput(login));
  const char* wifi = "set wifi.pwd secret phrase";
  EXPECT_STREQ("secret phrase", mesh::cli::terminalPasswordInput(wifi));
  EXPECT_EQ(nullptr, mesh::cli::terminalPasswordInput("set wifi.pwd "));
}

TEST(CLICommandUtils, BackspaceErasesAsciiAndUtf8Characters) {
  char ascii[] = "trace path 2 7773";
  size_t length = strlen(ascii);

  length = mesh::cli::eraseLastTerminalInput(ascii, length);
  EXPECT_STREQ("trace path 2 777", ascii);
  EXPECT_EQ(strlen(ascii), length);

  char utf8[] = "channel Public hi 👋";
  length = strlen(utf8);
  length = mesh::cli::eraseLastTerminalInput(utf8, length);
  EXPECT_STREQ("channel Public hi ", utf8);
  EXPECT_EQ(strlen(utf8), length);

  EXPECT_EQ(0u, mesh::cli::eraseLastTerminalInput(nullptr, 0));
  EXPECT_EQ(0u, mesh::cli::eraseLastTerminalInput(utf8, 0));
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

TEST(CLICommandUtils, ParsesSignedIntegersStrictly) {
  int32_t value = 0;

  EXPECT_TRUE(mesh::cli::parseIntegerStrict("22", value));
  EXPECT_EQ(22, value);
  EXPECT_TRUE(mesh::cli::parseIntegerStrict(" -9 ", value));
  EXPECT_EQ(-9, value);
  EXPECT_TRUE(mesh::cli::parseIntegerStrict("+12", value));
  EXPECT_EQ(12, value);
  EXPECT_TRUE(mesh::cli::parseIntegerStrict("-2147483648", value));
  EXPECT_EQ(INT32_MIN, value);
  EXPECT_TRUE(mesh::cli::parseIntegerStrict("2147483647", value));
  EXPECT_EQ(INT32_MAX, value);
}

TEST(CLICommandUtils, RejectsInvalidOrOverflowingIntegers) {
  int32_t value = 123;

  EXPECT_FALSE(mesh::cli::parseIntegerStrict(nullptr, value));
  EXPECT_FALSE(mesh::cli::parseIntegerStrict("", value));
  EXPECT_FALSE(mesh::cli::parseIntegerStrict("-", value));
  EXPECT_FALSE(mesh::cli::parseIntegerStrict("1.0", value));
  EXPECT_FALSE(mesh::cli::parseIntegerStrict("12dBm", value));
  EXPECT_FALSE(mesh::cli::parseIntegerStrict("2147483648", value));
  EXPECT_FALSE(mesh::cli::parseIntegerStrict("-2147483649", value));
  EXPECT_EQ(123, value);
}

TEST(CLICommandUtils, ParsesRadioTuplesWithoutFloatScanf) {
  float frequency = 0.0f;
  float bandwidth = 0.0f;
  uint8_t spreading_factor = 0;
  uint8_t coding_rate = 0;
  uint32_t timeout_minutes = 0;

  EXPECT_TRUE(mesh::cli::parseRadioTupleStrict(
      "910.525,62.5,7,5", frequency, bandwidth,
      spreading_factor, coding_rate));
  EXPECT_FLOAT_EQ(910.525f, frequency);
  EXPECT_FLOAT_EQ(62.5f, bandwidth);
  EXPECT_EQ(7, spreading_factor);
  EXPECT_EQ(5, coding_rate);

  EXPECT_TRUE(mesh::cli::parseTemporaryRadioTupleStrict(
      " 909.950 , 250 , 5 , 5 , 120 ", frequency, bandwidth,
      spreading_factor, coding_rate, timeout_minutes));
  EXPECT_FLOAT_EQ(909.950f, frequency);
  EXPECT_FLOAT_EQ(250.0f, bandwidth);
  EXPECT_EQ(5, spreading_factor);
  EXPECT_EQ(5, coding_rate);
  EXPECT_EQ(120u, timeout_minutes);
}

TEST(CLICommandUtils, RejectsMalformedRadioTuples) {
  float frequency = 123.0f;
  float bandwidth = 456.0f;
  uint8_t spreading_factor = 7;
  uint8_t coding_rate = 5;
  uint32_t timeout_minutes = 60;

  EXPECT_FALSE(mesh::cli::parseRadioTupleStrict(
      nullptr, frequency, bandwidth, spreading_factor, coding_rate));
  EXPECT_FALSE(mesh::cli::parseRadioTupleStrict(
      "910.525,62.5,7", frequency, bandwidth,
      spreading_factor, coding_rate));
  EXPECT_FALSE(mesh::cli::parseRadioTupleStrict(
      "910.525,62.5,7,5,1", frequency, bandwidth,
      spreading_factor, coding_rate));
  EXPECT_FALSE(mesh::cli::parseRadioTupleStrict(
      "910.525x,62.5,7,5", frequency, bandwidth,
      spreading_factor, coding_rate));
  EXPECT_FALSE(mesh::cli::parseRadioTupleStrict(
      "910.525,62.5,256,5", frequency, bandwidth,
      spreading_factor, coding_rate));

  EXPECT_FALSE(mesh::cli::parseTemporaryRadioTupleStrict(
      "909.950,250,5,5,1,99", frequency, bandwidth,
      spreading_factor, coding_rate, timeout_minutes));
  EXPECT_FALSE(mesh::cli::parseTemporaryRadioTupleStrict(
      "909.950,250,5,5,1x", frequency, bandwidth,
      spreading_factor, coding_rate, timeout_minutes));
  EXPECT_FALSE(mesh::cli::parseTemporaryRadioTupleStrict(
      "909.950,250,5,5,2147483648", frequency, bandwidth,
      spreading_factor, coding_rate, timeout_minutes));

  EXPECT_FLOAT_EQ(123.0f, frequency);
  EXPECT_FLOAT_EQ(456.0f, bandwidth);
  EXPECT_EQ(7, spreading_factor);
  EXPECT_EQ(5, coding_rate);
  EXPECT_EQ(60u, timeout_minutes);
}

TEST(RadioPowerLimits, LeavesUnspecifiedBackendRangeToDriver) {
  EXPECT_EQ(INT8_MIN, mesh::minLoRaTxPowerForFrequency(915.0f));
  EXPECT_EQ(INT8_MAX, mesh::maxLoRaTxPowerForFrequency(915.0f));
  EXPECT_TRUE(mesh::isLoRaTxPowerValid(INT8_MIN, 915.0f));
  EXPECT_TRUE(mesh::isLoRaTxPowerValid(INT8_MAX, 915.0f));
  EXPECT_FALSE(mesh::isLoRaTxPowerValid(128, 915.0f));
  EXPECT_EQ(INT8_MAX, mesh::clampLoRaTxPower(200, 915.0f));
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
  EXPECT_TRUE(mesh::cli::standaloneWiFiPasswordValid(
      "0123456789abcdefABCDEF0123456789abcdefABCDEF0123456789abcdef0123"));

  std::string non_hex_64(64, 'a');
  non_hex_64[31] = 'g';
  EXPECT_FALSE(mesh::cli::standaloneWiFiPasswordValid(non_hex_64.c_str()));
  non_hex_64[31] = ' ';
  EXPECT_FALSE(mesh::cli::standaloneWiFiPasswordValid(non_hex_64.c_str()));
  EXPECT_FALSE(mesh::cli::standaloneWiFiPasswordValid(
      std::string(65, 'a').c_str()));
  EXPECT_FALSE(mesh::cli::standaloneWiFiPasswordValid(nullptr));

  uint8_t power_save = 99;
  EXPECT_TRUE(mesh::cli::parseStandaloneWiFiPowerSave("min", power_save));
  EXPECT_EQ(0, power_save);
  EXPECT_TRUE(mesh::cli::parseStandaloneWiFiPowerSave("none", power_save));
  EXPECT_EQ(1, power_save);
  EXPECT_TRUE(mesh::cli::parseStandaloneWiFiPowerSave("max", power_save));
  EXPECT_EQ(2, power_save);
  EXPECT_FALSE(mesh::cli::parseStandaloneWiFiPowerSave("off", power_save));
}

TEST(CLICommandUtils, EnforcesWiFiSleepForBluetoothCoexistence) {
  EXPECT_EQ(mesh::wifi::kPowerSaveMin,
            mesh::wifi::effectivePowerSave(mesh::wifi::kPowerSaveMin, true));
  EXPECT_EQ(mesh::wifi::kPowerSaveMin,
            mesh::wifi::effectivePowerSave(mesh::wifi::kPowerSaveNone, true));
  EXPECT_EQ(mesh::wifi::kPowerSaveMax,
            mesh::wifi::effectivePowerSave(mesh::wifi::kPowerSaveMax, true));
  EXPECT_EQ(mesh::wifi::kPowerSaveNone,
            mesh::wifi::effectivePowerSave(mesh::wifi::kPowerSaveNone, false));
  EXPECT_EQ(mesh::wifi::kPowerSaveMin,
            mesh::wifi::effectivePowerSave(99, true));
  EXPECT_EQ(mesh::wifi::kPowerSaveNone,
            mesh::wifi::effectivePowerSave(99, false));
  EXPECT_EQ(mesh::wifi::kPowerSaveMin,
            mesh::wifi::effectivePowerSave(
                mesh::wifi::kPowerSaveMax, false, true));
  EXPECT_EQ(mesh::wifi::kPowerSaveMin,
            mesh::wifi::effectivePowerSave(
                mesh::wifi::kPowerSaveMax, true, true));
  EXPECT_EQ(mesh::wifi::kPowerSaveMin,
            mesh::wifi::effectivePowerSave(
                mesh::wifi::kPowerSaveNone, true, true));
  EXPECT_EQ(mesh::wifi::kPowerSaveNone,
            mesh::wifi::effectivePowerSave(
                mesh::wifi::kPowerSaveNone, false, true));
}

TEST(CLICommandUtils, DefaultsWiFiPowerSaveToNoneWithoutProfileOverride) {
  EXPECT_EQ(mesh::wifi::kPowerSaveNone, mesh::wifi::kDefaultPowerSave);
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

TEST(WiFiChannelPolicy, AcceptsTheSupportedEspNowChannelRange) {
  uint8_t channel = 0;

  EXPECT_TRUE(mesh::wifi::parseEspNowChannel("1", channel));
  EXPECT_EQ(1, channel);
  EXPECT_TRUE(mesh::wifi::parseEspNowChannel("6", channel));
  EXPECT_EQ(6, channel);
  EXPECT_TRUE(mesh::wifi::parseEspNowChannel("13", channel));
  EXPECT_EQ(13, channel);
  EXPECT_TRUE(mesh::wifi::parseEspNowChannel(" \t11\t ", channel));
  EXPECT_EQ(11, channel);

  EXPECT_FALSE(mesh::wifi::isValidEspNowChannel(0));
  EXPECT_TRUE(mesh::wifi::isValidEspNowChannel(1));
  EXPECT_TRUE(mesh::wifi::isValidEspNowChannel(13));
  EXPECT_FALSE(mesh::wifi::isValidEspNowChannel(14));
}

TEST(WiFiChannelPolicy, RejectsMalformedAndOutOfRangeValuesWithoutMutation) {
  const char* invalid[] = {
    nullptr, "", " \t", "0", "14", "+1", "-1", "1.0", "6GHz",
    "1 2", "1\n", "999999999999999999999999999999999999",
  };

  for (const char* value : invalid) {
    uint8_t channel = 7;
    EXPECT_FALSE(mesh::wifi::parseEspNowChannel(value, channel))
        << (value == nullptr ? "<null>" : value);
    EXPECT_EQ(7, channel);
  }
}

TEST(WiFiChannelPolicy, FallsBackOnlyWhenTheStoredChannelIsInvalid) {
  EXPECT_EQ(1, mesh::wifi::validEspNowChannelOrDefault(0, 1));
  EXPECT_EQ(6, mesh::wifi::validEspNowChannelOrDefault(6, 1));
  EXPECT_EQ(13, mesh::wifi::validEspNowChannelOrDefault(13, 1));
  EXPECT_EQ(1, mesh::wifi::validEspNowChannelOrDefault(14, 1));
}

TEST(CLICommandUtils, ObserverSettingsNeverStartADisabledBridge) {
  int calls = 0;
  EXPECT_TRUE(mesh::cli::restartBridgeIfEnabled(false, [&calls]() {
    ++calls;
    return true;
  }));
  EXPECT_EQ(0, calls);

  EXPECT_TRUE(mesh::cli::restartBridgeIfEnabled(true, [&calls]() {
    ++calls;
    return true;
  }));
  EXPECT_EQ(1, calls);

  EXPECT_FALSE(mesh::cli::restartBridgeIfEnabled(true, [&calls]() {
    ++calls;
    return false;
  }));
  EXPECT_EQ(2, calls);
}

TEST(ESPNowBridgeFormat, ParsesOnlyExplicitSupportedNames) {
  uint8_t format = 99;

  EXPECT_TRUE(mesh::bridge::parseEspNowFormat("wrapped", format));
  EXPECT_EQ(mesh::bridge::ESPNOW_FORMAT_WRAPPED, format);
  EXPECT_STREQ("wrapped", mesh::bridge::espNowFormatName(format));

  EXPECT_TRUE(mesh::bridge::parseEspNowFormat("raw", format));
  EXPECT_EQ(mesh::bridge::ESPNOW_FORMAT_RAW, format);
  EXPECT_STREQ("raw", mesh::bridge::espNowFormatName(format));

  const char* invalid[] = {
    nullptr, "", "auto", "both", "native", "RAW", "raw ", " wrapped",
  };
  for (const char* value : invalid) {
    format = 7;
    EXPECT_FALSE(mesh::bridge::parseEspNowFormat(value, format));
    EXPECT_EQ(7, format);
  }
}

TEST(ESPNowBridgeFormat, PreservesLegacyDefaultAndRawPayloadCapacity) {
  EXPECT_TRUE(mesh::bridge::isValidEspNowFormat(
      mesh::bridge::ESPNOW_FORMAT_WRAPPED));
  EXPECT_TRUE(mesh::bridge::isValidEspNowFormat(
      mesh::bridge::ESPNOW_FORMAT_RAW));
  EXPECT_FALSE(mesh::bridge::isValidEspNowFormat(2));

  EXPECT_EQ(246U, mesh::bridge::espNowMaxMeshPacketSize(
      mesh::bridge::ESPNOW_FORMAT_WRAPPED));
  EXPECT_EQ(255U, mesh::bridge::espNowMaxMeshPacketSize(
      mesh::bridge::ESPNOW_FORMAT_RAW));
  EXPECT_EQ(246U, mesh::bridge::espNowMaxMeshPacketSize(99));
}

TEST(ESPNowBridgeFormat, BothFormatsAcceptExactlyChannelsOneThroughThirteen) {
  const uint8_t formats[] = {
    mesh::bridge::ESPNOW_FORMAT_WRAPPED,
    mesh::bridge::ESPNOW_FORMAT_RAW,
  };

  for (uint8_t format : formats) {
    EXPECT_EQ(13, mesh::bridge::espNowMaxChannel(format));
    EXPECT_FALSE(mesh::bridge::isValidEspNowBridgeChannel(0, format));
    EXPECT_FALSE(mesh::bridge::isValidEspNowBridgeChannel(14, format));

    for (uint8_t expected = 1; expected <= 13; ++expected) {
      char text[4];
      snprintf(text, sizeof(text), "%u", (unsigned)expected);
      uint8_t channel = 99;
      EXPECT_TRUE(mesh::bridge::parseEspNowBridgeChannel(
          text, format, channel)) << "format=" << (unsigned)format
                                 << " channel=" << (unsigned)expected;
      EXPECT_EQ(expected, channel);
      EXPECT_TRUE(mesh::bridge::isValidEspNowBridgeChannel(channel, format));
    }
  }
}

TEST(ESPNowBridgeFormat, BothFormatsRejectMalformedChannelsWithoutMutation) {
  const uint8_t formats[] = {
    mesh::bridge::ESPNOW_FORMAT_WRAPPED,
    mesh::bridge::ESPNOW_FORMAT_RAW,
  };
  const char* invalid[] = {
    nullptr, "", " ", "0", "14", "15", "+6", "-6", "6junk", "1 2",
    "6.0", "6\n", "999999999999999999999999999999999",
  };

  for (uint8_t format : formats) {
    for (const char* value : invalid) {
      uint8_t channel = 7;
      EXPECT_FALSE(mesh::bridge::parseEspNowBridgeChannel(
          value, format, channel))
          << "format=" << (unsigned)format
          << " value=" << (value == nullptr ? "<null>" : value);
      EXPECT_EQ(7, channel);
    }

    uint8_t channel = 0;
    EXPECT_TRUE(mesh::bridge::parseEspNowBridgeChannel(
        " \t13\t ", format, channel));
    EXPECT_EQ(13, channel);
  }
}

int main(int argc, char** argv) {
  ::testing::InitGoogleTest(&argc, argv);
  return RUN_ALL_TESTS();
}
