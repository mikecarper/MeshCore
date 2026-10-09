#include <gtest/gtest.h>
#include <Ed25519.h>
#include <helpers/FleetCommand.h>
#include <SHA256.h>
#include <array>
#include <string>
#include <vector>

namespace {

using Fleet = mesh::FleetCommand;
constexpr uint32_t Now = 1791500000UL;
constexpr uint8_t ChannelKey[16] = {
  1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16
};

class FixedRng : public mesh::RNG {
public:
  void random(uint8_t* dest, size_t size) override {
    for (size_t i = 0; i < size; ++i) dest[i] = uint8_t(i + 1);
  }
};

class FleetCommandTest : public ::testing::Test {
protected:
  FixedRng rng;
  mesh::LocalIdentity publisher{&rng};
  std::array<uint8_t, Fleet::TargetSize> all{};
  Fleet::Decoded decoded{};
  bool previous_verifier = g_mock_ed25519_verify_result;

  void SetUp() override { g_mock_ed25519_verify_result = true; }
  void TearDown() override { g_mock_ed25519_verify_result = previous_verifier; }

  std::vector<uint8_t> encode(const char* command = "get radio2",
                              uint32_t sequence = Now,
                              uint32_t expires = Now + 120,
                              const uint8_t* target = nullptr) {
    std::vector<uint8_t> bytes(Fleet::MaxPayloadLength);
    const size_t size = Fleet::encode(publisher, ChannelKey, sequence, expires,
        target ? target : all.data(), command, bytes.data(), bytes.size());
    bytes.resize(size);
    return bytes;
  }

  bool decode(const std::vector<uint8_t>& bytes, uint32_t now = Now) {
    return Fleet::decode(publisher, ChannelKey, bytes.data(), bytes.size(),
                         now, publisher.pub_key, decoded);
  }

  std::vector<uint8_t> encodeTargets(const Fleet::Targets& targets,
                                    const char* command = "get radio2") {
    std::vector<uint8_t> bytes(Fleet::MaxPayloadLength);
    const size_t size = Fleet::encode(publisher, ChannelKey, Now, Now + 120,
                                     targets, command, bytes.data(), bytes.size());
    bytes.resize(size);
    return bytes;
  }
};

TEST_F(FleetCommandTest, ExplicitRadioAndFilterFamiliesAreAllowed) {
  for (const char* command : {
      "get radio2", "get tempradio2", "get radio2.status", "get radio2.timing",
      "get radio2.cross", "set radio2.cross auto", "set radio2.cross on",
      "set radio2.cross off", "set radio2 off", "set tempradio2 off",
      "set radio2 910.5,500,5,5,rxtx",
      "set tempradio2 910.5,500,5,5,rx,10,auto",
      "set radio2 910.5,500,5,5,rx,32",
      "get flood.filter", "get flood.filter.1", "get flood.filter.blacklist.2",
      "set flood.filter.2 grp_txt 4+", "set flood.filter.blacklist A1,B2",
      "del flood.filter.blacklist.2", "del flood.filter all",
      "get flood.rule.2", "set flood.rule.2 type=any rate=10/min stop",
      "del flood.rule.2", "del flood.rule all",
      "get flood.moderation.2", "set flood.moderation public '*' rate=2/min",
      "del flood.moderation all", "get flood.channel.scope.3",
      "set flood.channel.scope public scope=usa", "del flood.channel.scope.3",
      "get flood.channel.scope.require.2",
      "set flood.channel.scope.require.2 #fleet", "del flood.channel.scope.require all",
      "get flood.channel.data", "set flood.channel.data on",
      "get flood.channel.data.hops", "set flood.channel.data.hops 4",
      "get flood.max", "set flood.max 8", "get flood.max.unscoped",
      "set flood.max.unscoped 4", "get flood.max.advert", "set flood.max.advert 6"}) {
    EXPECT_TRUE(Fleet::commandAllowed(command)) << command;
  }
}

TEST_F(FleetCommandTest, ScheduledPrimaryAndSecondaryFamiliesAcceptCanonicalForms) {
  for (const char* family : {"radioat", "tempradioat", "radioat2", "tempradioat2"}) {
    for (const char* verb : {"get", "del"}) {
      for (const char* selector : {"", " all", " 1", " 3"}) {
        const std::string command = std::string(verb) + ' ' + family + selector;
        EXPECT_TRUE(Fleet::commandAllowed(command.c_str())) << command;
      }
    }
  }
  for (const char* command : {
      "get radioat 255", "del tempradioat 255", "get radioat2 4", "del tempradioat2 4",
      "set radioat 910.5,500,5,5,+1",
      "set radioat 910.5,500,5,5,1800000000,auto",
      "set radioat 910.5,500,5,5,+1,0",
      "set radioat 910.5,500,5,5,+1,8",
      "set radioat 910.5,500,5,5,4294967295,65528",
      "set tempradioat 910.5,500,5,5,+1,+2",
      "set tempradioat 910.5,500,5,5,1800000000,1800000600,32",
      "set tempradioat 910.5,500,5,5,+1,1800000600,auto",
      "set tempradioat 910.5,500,5,5,1800000000,+2,auto",
      "set radioat2 910.5,500,5,5,rx,+1",
      "set radioat2 910.5,500,5,5,rxtx,1800000000,0",
      "set radioat2 910.5,500,5,5,rxtx,+1,auto",
      "set tempradioat2 910.5,500,5,5,rx,+1,+2",
      "set tempradioat2 910.5,500,5,5,rxtx,1800000000,1800000600,65528",
      "set tempradioat2 910.5,500,5,5,rxtx,+1,1800000600,auto"}) {
    EXPECT_TRUE(Fleet::commandAllowed(command)) << command;
    const auto bytes = encode(command);
    ASSERT_FALSE(bytes.empty()) << command;
    ASSERT_TRUE(decode(bytes)) << command;
    EXPECT_STREQ(command, decoded.command);
  }
}

TEST_F(FleetCommandTest, ScheduledCommandsRejectMalformedTimesFieldsAndSelectors) {
  for (const char* command : {
      "get radioat 0", "get radioat 01", "get radioat +1", "get radioat -1",
      "get radioat 256", "del tempradioat 4294967295", "del radioat 1 all",
      "get radioat2 5", "del tempradioat2 5", "get radioat2 01", "get radioat2 ALL",
      "get radioat.all", "del tempradioat2.extra all", "get radioat  all",
      "set radioat off", "set tempradioat off", "set radioat2 off", "set tempradioat2 off",
      "set radioat 910.5,500,5,5", "set tempradioat 910.5,500,5,5,+1",
      "set radioat2 910.5,500,5,5,+1", "set tempradioat2 910.5,500,5,5,rx,+1",
      "set radioat 910.5,500,5,5,rx,+1",
      "set radioat2 910.5,500,5,5,rx&tx,+1", "set radioat2 910.5,500,5,5,RX,+1",
      "set radioat 910.5,500,5,5,+0", "set radioat 910.5,500,5,5,0",
      "set radioat 910.5,500,5,5,+", "set radioat 910.5,500,5,5,++1",
      "set radioat 910.5,500,5,5,+1m", "set radioat 910.5,500,5,5,+1h",
      "set radioat 910.5,500,5,5,+1.5", "set radioat 910.5,500,5,5,-1",
      "set radioat 910.5,500,5,5,1e9", "set radioat 910.5,500,5,5,4294967296",
      "set radioat 910.5,500,5,5,+71582789",
      "set tempradioat 910.5,500,5,5,+1,+0",
      "set tempradioat2 910.5,500,5,5,rx,+1,4294967296",
      "set radioat 910.5,500,256,5,+1", "set radioat 910.5,500,5,256,+1",
      "set radioat 910.5,500,+5,5,+1", "set radioat 910.5,500,5,5,+1,7",
      "set radioat 910.5,500,5,5,+1,65529", "set radioat 910.5,500,5,5,+1,AUTO",
      "set radioat 910.5,500,5,5,+1,+8", "set radioat 910.5,500,5,5,+1,auto,8",
      "set radioat 910.5,500,5,5,+1,", "set radioat 910.5,500,5,5, +1",
      "set tempradioat2 910.5,500,5,5,rx,+1,+2;reboot",
      "set tempradioat2 910.5,500,5,5,rx,+1,+2\nreboot"}) {
    EXPECT_FALSE(Fleet::commandAllowed(command)) << command;
    EXPECT_TRUE(encode(command).empty()) << command;
  }
  const std::string prefix = "set tempradioat2 910.5,500,5,5,rxtx,+";
  const std::string suffix = "1,+2,auto";
  const std::string maximum = prefix
      + std::string(Fleet::MaxCommandLength - prefix.size() - suffix.size(), '0') + suffix;
  ASSERT_EQ(Fleet::MaxCommandLength, maximum.size());
  EXPECT_TRUE(Fleet::commandAllowed(maximum.c_str()));
  EXPECT_TRUE(decode(encode(maximum.c_str())));
  const std::string oversized = prefix + '0' + maximum.substr(prefix.size());
  EXPECT_FALSE(Fleet::commandAllowed(oversized.c_str()));
}

TEST_F(FleetCommandTest, ClockManagementOnlyAdmitsExactBoundedCommands) {
  for (const char* command : {"clock", "clock sync", "time 1735689600",
                              "time 1800000000", "time 4294967295"}) {
    EXPECT_TRUE(Fleet::commandAllowed(command)) << command;
    ASSERT_TRUE(decode(encode(command))) << command;
    EXPECT_STREQ(command, decoded.command);
  }
  for (const char* command : {
      "get clock", "set clock 1800000000", "clock now", "clock sync now", "clock.sync",
      "clkreboot", "clock sync;reboot", "clock sync\nreboot", "clock  sync",
      "time", "time ", "time  1800000000", "time +1800000000", "time -1800000000",
      "time 1735689599", "time 0", "time 4294967296", "time 9999999999999999999",
      "time 1800000000.0", "time 1e9", "time 1800000000 now", "time 1800000000;reboot",
      "time 1800000000\nreboot", "time\t1800000000", "TIME 1800000000"}) {
    EXPECT_FALSE(Fleet::commandAllowed(command)) << command;
    EXPECT_TRUE(encode(command).empty()) << command;
  }
}

TEST_F(FleetCommandTest, ClockManagementDoesNotBypassEnvelopeClockAndExpiryPolicy) {
  for (const char* command : {"clock", "clock sync", "time 1800000000"}) {
    const auto bytes = encode(command);
    ASSERT_TRUE(decode(bytes)) << command;
    EXPECT_EQ(Now, decoded.sequence);
    EXPECT_FALSE(decode(bytes, 0)) << command;
    EXPECT_FALSE(decode(bytes, Fleet::MinEpoch - 1)) << command;
    EXPECT_FALSE(decode(bytes, Now - 61)) << command;
    EXPECT_FALSE(decode(bytes, Now + 121)) << command;
    EXPECT_TRUE(encode(command, Now, Now + 601).empty()) << command;
    EXPECT_TRUE(encode(command, Fleet::MinEpoch - 1, Fleet::MinEpoch).empty()) << command;
  }
}

TEST_F(FleetCommandTest, NoGenericAdminNamespaceOrPrefixEscapes) {
  for (const char* command : {
      "get prv.key", "backup prv.key 1234567890ABCDEF", "get wifi.password",
      "set wifi.password abc", "setperm abc 3", "reboot", "start ota",
      "get fleet", "set fleet.channel abc", "region add usa", "get radio",
      "set radio 910.5,500,5,5", "get radio2.reply", "get radio2.scan",
      "set tx.reply both", "get tempradio", "set tempradio 910.5,500,5,5,10",
      "get flood.filter.secret", "get flood.filtering", "get flood.rulebook",
      "get flood.rule.1.secret", "get flood.filter.blacklistx",
      "get flood.channel.scope.required", "get flood.channel.data.secret",
      "get flood.max.secret", "get flood.max.unscoped.extra", "get flood.rule.0",
      "get flood.rule.01", "get flood.rule.256", "get flood.rule.9999999999",
      "xy|set radio2 off", "GET radio2", "get  radio2", " get radio2",
      "get radio2 ", "get radio2 off", "del radio2", "set radio2",
      "set radio2.cross true", "del flood.channel.data", "set flood.filter.2",
      "del flood.rule.2 extra", "get flood.rule.1 all", "set flood.max",
      "set radio2 910.5,500,5,5,rxtx,auto,99",
      "set radio2 910.5,500,5,5,off", "set radio2 910.5,500,5,5,rxtx,",
      "set tempradio2 910.5,500,5,5,rxtx",
      "set tempradio2 910.5,500,5,5,rxtx,+10",
      "set radio2 910.5,500,5,5,rxtx;reboot", "get radio2\nreboot",
      "get radio2\r", "get\tradio2", "get radio2|reboot", "get radio2&reboot",
      "set flood.rule.1 type=any `reboot`", "set flood.rule.1 type=any\\reboot"}) {
    EXPECT_FALSE(Fleet::commandAllowed(command)) << command;
  }
  const std::string non_ascii = "set flood.rule.1 " + std::string(1, char(0x80));
  EXPECT_FALSE(Fleet::commandAllowed(non_ascii.c_str()));
  EXPECT_FALSE(Fleet::commandAllowed(nullptr));
  EXPECT_FALSE(Fleet::commandAllowed(""));
}

TEST_F(FleetCommandTest, OnlyCompletePublicKeyTargetsOrExplicitAll) {
  std::array<uint8_t, Fleet::TargetSize> target;
  target.fill(0xAA);
  EXPECT_TRUE(Fleet::parseTarget("all", target.data()));
  EXPECT_EQ(all, target);
  EXPECT_FALSE(Fleet::parseTarget("ALL", target.data()));
  EXPECT_FALSE(Fleet::parseTarget("1234", target.data()));
  EXPECT_FALSE(Fleet::parseTarget(std::string(64, '0').c_str(), target.data()));
  EXPECT_FALSE(Fleet::parseTarget(std::string(64, 'z').c_str(), target.data()));
  EXPECT_FALSE(Fleet::parseTarget(nullptr, target.data()));
  EXPECT_FALSE(Fleet::parseTarget("all", nullptr));
  const char* full = "0102030405060708090a0b0c0d0e0f101112131415161718191a1b1c1d1e1f20";
  ASSERT_TRUE(Fleet::parseTarget(full, target.data()));
  const std::array<uint8_t, 16> expected = {
    0xae, 0x21, 0x6c, 0x2e, 0xf5, 0x24, 0x7a, 0x37,
    0x82, 0xc1, 0x35, 0xef, 0xa2, 0x79, 0xa3, 0xe4
  };
  EXPECT_EQ(expected, target); // independently generated SHA-256 vector
}

TEST_F(FleetCommandTest, RejectsPublicZeroOrMissingPrivacyKeys) {
  const uint8_t public_key[16] = {
    0x8b, 0x33, 0x87, 0xe9, 0xc5, 0xcd, 0xea, 0x6a,
    0xc9, 0xe5, 0xed, 0xba, 0xa1, 0x15, 0xcd, 0x72
  };
  const uint8_t empty[16] = {};
  EXPECT_TRUE(Fleet::privateKeyAllowed(ChannelKey));
  EXPECT_FALSE(Fleet::privateKeyAllowed(public_key));
  EXPECT_FALSE(Fleet::privateKeyAllowed(empty));
  EXPECT_FALSE(Fleet::privateKeyAllowed(nullptr));
  std::array<uint8_t, Fleet::MaxPayloadLength> output;
  for (const uint8_t* key : {public_key, empty, static_cast<const uint8_t*>(nullptr)}) {
    EXPECT_EQ(0U, Fleet::encode(publisher, key, Now, Now + 10, all.data(),
                               "get radio2", output.data(), output.size()));
    const auto bytes = encode();
    EXPECT_FALSE(Fleet::decode(publisher, key, bytes.data(), bytes.size(), Now,
                              publisher.pub_key, decoded));
  }
}

TEST_F(FleetCommandTest, ExactEnvelopeRoundTripsThroughVerifierBoundary) {
  const auto bytes = encode();
  ASSERT_EQ(Fleet::HeaderSize + 10 + Fleet::SignatureSize, bytes.size());
  EXPECT_EQ(std::string("FMC1"), std::string(bytes.begin(), bytes.begin() + 4));
  EXPECT_EQ(10, bytes[28]);
  const unsigned calls = g_mock_ed25519_verify_calls;
  ASSERT_TRUE(decode(bytes));
  EXPECT_EQ(calls + 1, g_mock_ed25519_verify_calls);
  EXPECT_EQ(Now, decoded.sequence);
  EXPECT_EQ(Now + 120, decoded.expires);
  EXPECT_TRUE(decoded.broadcast);
  EXPECT_STREQ("get radio2", decoded.command);
}

TEST_F(FleetCommandTest, FailedSignatureVerificationNeverReturnsCommand) {
  const auto bytes = encode();
  g_mock_ed25519_verify_result = false;
  decoded.sequence = 123;
  strcpy(decoded.command, "stale");
  const unsigned calls = g_mock_ed25519_verify_calls;
  EXPECT_FALSE(decode(bytes));
  EXPECT_EQ(calls + 1, g_mock_ed25519_verify_calls);
  EXPECT_EQ(0U, decoded.sequence);
  EXPECT_EQ(0U, decoded.expires);
  EXPECT_STREQ("", decoded.command);
}

TEST_F(FleetCommandTest, FullPayloadLimitFitsWithoutOverflow) {
  const std::string prefix = "set flood.rule.1 ";
  const std::string maximum = prefix + std::string(Fleet::MaxCommandLength - prefix.size(), 'a');
  ASSERT_TRUE(Fleet::commandAllowed(maximum.c_str()));
  const auto bytes = encode(maximum.c_str());
  ASSERT_EQ(Fleet::MaxPayloadLength, bytes.size());
  EXPECT_TRUE(decode(bytes));
  EXPECT_STREQ(maximum.c_str(), decoded.command);
  EXPECT_FALSE(Fleet::commandAllowed((maximum + "a").c_str()));
  EXPECT_TRUE(encode((maximum + "a").c_str()).empty());
  std::array<uint8_t, Fleet::MaxPayloadLength + 2> output;
  output.fill(0xA5);
  EXPECT_EQ(0U, Fleet::encode(publisher, ChannelKey, Now, Now + 10, all.data(),
                            maximum.c_str(), output.data() + 1, 164));
  for (uint8_t value : output) EXPECT_EQ(0xA5, value);
}

TEST_F(FleetCommandTest, RejectsTruncationTrailingPaddingAndWrongMagic) {
  const auto original = encode();
  for (size_t size = 0; size < original.size(); ++size) {
    const std::vector<uint8_t> shortened(original.begin(), original.begin() + size);
    EXPECT_FALSE(decode(shortened)) << size;
  }
  auto bytes = original;
  bytes.push_back(0);
  EXPECT_FALSE(decode(bytes));
  bytes = original;
  bytes[0] ^= 1;
  EXPECT_FALSE(decode(bytes));
  bytes = original;
  bytes[28] = 0;
  EXPECT_FALSE(decode(bytes));
  bytes[28] = 73;
  EXPECT_FALSE(decode(bytes));
}

TEST_F(FleetCommandTest, RejectsEmbeddedNulAndForbiddenSignedCommandShape) {
  auto bytes = encode();
  bytes[Fleet::HeaderSize + 4] = 0;
  EXPECT_FALSE(decode(bytes));
  bytes = encode();
  memcpy(bytes.data() + Fleet::HeaderSize, "get radio1", 10);
  EXPECT_FALSE(decode(bytes));
  bytes = encode();
  bytes[Fleet::HeaderSize + 9] = '\n';
  EXPECT_FALSE(decode(bytes));
}

TEST_F(FleetCommandTest, MatchesHashedCompleteSelfIdentityOrAll) {
  std::array<uint8_t, Fleet::TargetSize> target;
  SHA256 hash;
  hash.update(publisher.pub_key, PUB_KEY_SIZE);
  hash.finalize(target.data(), target.size());
  ASSERT_TRUE(decode(encode("get radio2", Now, Now + 120, target.data())));
  EXPECT_FALSE(decoded.broadcast);
  target[15] ^= 1;
  EXPECT_FALSE(decode(encode("get radio2", Now, Now + 120, target.data())));
  EXPECT_TRUE(decode(encode()));
}

TEST_F(FleetCommandTest, RequiresPlausibleClockAndBoundedExpiry) {
  EXPECT_TRUE(encode("get radio2", Fleet::MinEpoch - 1, Fleet::MinEpoch + 10).empty());
  EXPECT_TRUE(encode("get radio2", Now, Now - 1).empty());
  EXPECT_TRUE(encode("get radio2", Now, Now + 601).empty());
  EXPECT_FALSE(decode(encode(), Fleet::MinEpoch - 1));
  EXPECT_TRUE(decode(encode(), Now + 120));
  EXPECT_FALSE(decode(encode(), Now + 121));
  EXPECT_TRUE(decode(encode("get radio2", Now + 60, Now + 120)));
  EXPECT_FALSE(decode(encode("get radio2", Now + 61, Now + 120)));
  EXPECT_TRUE(decode(encode("get radio2", Now, Now + 600)));
  EXPECT_TRUE(decode(encode("get radio2", Now, Now)));
}

TEST_F(FleetCommandTest, RejectsMissingAndZeroIdentityInputs) {
  const auto bytes = encode();
  const uint8_t empty[32] = {};
  const mesh::Identity absent(empty);
  EXPECT_FALSE(Fleet::decode(absent, ChannelKey, bytes.data(), bytes.size(),
                            Now, publisher.pub_key, decoded));
  EXPECT_FALSE(Fleet::decode(publisher, ChannelKey, bytes.data(), bytes.size(),
                            Now, empty, decoded));
  EXPECT_FALSE(Fleet::decode(publisher, ChannelKey, bytes.data(), bytes.size(),
                            Now, nullptr, decoded));
  EXPECT_FALSE(Fleet::decode(publisher, ChannelKey, nullptr, bytes.size(),
                            Now, publisher.pub_key, decoded));
}

TEST_F(FleetCommandTest, UnixRangeBoundaryDoesNotWrap) {
  const uint32_t maximum = UINT32_MAX;
  EXPECT_TRUE(decode(encode("get radio2", maximum, maximum), maximum - 60));
  EXPECT_FALSE(decode(encode("get radio2", maximum, maximum), maximum - 61));
  EXPECT_TRUE(encode("get radio2", maximum - 50, 1).empty());
}

TEST_F(FleetCommandTest, BoundedTargetTokenParsesMixedPrefixWidthsAndCompleteKeys) {
  Fleet::Targets targets{};
  const std::string full = "0102030405060708090a0b0c0d0e0f101112131415161718191a1b1c1d1e1f20";
  const std::string token = "A1B2C3D4,010203040506," + full;
  const std::string joined = token + " get radio2";
  ASSERT_TRUE(Fleet::parseTargets(joined.data(), token.size(), targets));
  EXPECT_EQ(3, targets.count);
  EXPECT_EQ(5 + 7 + 17, targets.length);
  EXPECT_EQ(4, targets.data[0]);
  EXPECT_EQ(0xA1, targets.data[1]);
  EXPECT_EQ(0xD4, targets.data[4]);
  EXPECT_EQ(6, targets.data[5]);
  EXPECT_EQ(6, targets.data[11]);
  EXPECT_EQ(16, targets.data[12]);
  EXPECT_EQ(0xAE, targets.data[13]);
  EXPECT_EQ(0xE4, targets.data[28]);
  const char nonterminated[8] = {'0','1','0','2','0','3','0','4'};
  EXPECT_TRUE(Fleet::parseTargets(nonterminated, sizeof(nonterminated), targets));
  EXPECT_EQ(1, targets.count);
  EXPECT_EQ(5, targets.length);
  EXPECT_TRUE(Fleet::parseTargets("all plus other text", 3, targets));
  EXPECT_EQ(0, targets.count);
  EXPECT_EQ(0, targets.length);
}

TEST_F(FleetCommandTest, RejectsMalformedWholeTargetListsWithoutPartialResults) {
  for (const std::string& token : {
      std::string(""), std::string("all,01020304"), std::string("01020304,all"),
      std::string("all,all"), std::string("ALL"), std::string(",01020304"),
      std::string("01020304,"), std::string("01020304,,05060708"),
      std::string("01020304,0506070g"), std::string("01020304 05060708"),
      std::string("01020304, 05060708"), std::string("01020304\n"),
      std::string("01"), std::string(10, '1'), std::string(16, '1'),
      std::string(32, '1'), std::string(63, '1'), std::string(65, '1'),
      std::string(64, '0'), std::string("01020304,") + std::string(64, '0')}) {
    Fleet::Targets targets;
    memset(&targets, 0xA5, sizeof(targets));
    EXPECT_FALSE(Fleet::parseTargets(token.data(), token.size(), targets)) << token;
    EXPECT_EQ(0, targets.length);
    EXPECT_EQ(0, targets.count);
  }
  Fleet::Targets targets{};
  EXPECT_FALSE(Fleet::parseTargets(nullptr, 8, targets));
  EXPECT_FALSE(Fleet::parseTargets("01020304", SIZE_MAX, targets));
  EXPECT_FALSE(Fleet::parseTargets("01020304", 0, targets));
}

TEST_F(FleetCommandTest, TargetRecordAndPayloadBudgetsAreSeparateAndBounded) {
  std::string list;
  for (unsigned count = 1; count <= 17; ++count) {
    if (!list.empty()) list += ',';
    list += "01020304";
    Fleet::Targets targets{};
    ASSERT_TRUE(Fleet::parseTargets(list.data(), list.size(), targets));
    EXPECT_EQ(count, targets.count);
    EXPECT_EQ(count * 5, targets.length);
    EXPECT_EQ(count <= 15, !encodeTargets(targets).empty()) << count;
  }
  Fleet::Targets targets{};
  list += ",01020304";
  EXPECT_FALSE(Fleet::parseTargets(list.data(), list.size(), targets));
  list.clear();
  const std::string full = "0102030405060708090a0b0c0d0e0f101112131415161718191a1b1c1d1e1f20";
  for (unsigned count = 1; count <= 5; ++count) {
    if (!list.empty()) list += ',';
    list += full;
    ASSERT_TRUE(Fleet::parseTargets(list.data(), list.size(), targets));
    EXPECT_EQ(count * 17, targets.length);
  }
  EXPECT_TRUE(encodeTargets(targets).empty());
  list += ',' + full;
  EXPECT_FALSE(Fleet::parseTargets(list.data(), list.size(), targets));
}

TEST_F(FleetCommandTest, ZeroTargetsUsesCompactBroadcastWhileLegacyAllStillDecodes) {
  Fleet::Targets targets{};
  const auto implicit = encodeTargets(targets);
  ASSERT_TRUE(Fleet::parseTargets("all", 3, targets));
  const auto explicit_all = encodeTargets(targets);
  ASSERT_EQ(implicit, explicit_all);
  ASSERT_EQ(Fleet::MinHeaderSize + strlen("get radio2") + Fleet::SignatureSize,
            implicit.size());
  EXPECT_EQ(15U, encode().size() - implicit.size());
  EXPECT_EQ(std::string("FMC2"), std::string(implicit.begin(), implicit.begin() + 4));
  EXPECT_EQ(0, implicit[12]);
  ASSERT_TRUE(decode(implicit));
  EXPECT_TRUE(decoded.broadcast);
  EXPECT_STREQ("get radio2", decoded.command);
  std::array<uint8_t, PUB_KEY_SIZE> unrelated;
  unrelated.fill(0xAA);
  ASSERT_TRUE(Fleet::decode(publisher, ChannelKey, implicit.data(), implicit.size(),
                           Now, unrelated.data(), decoded));
  EXPECT_TRUE(decoded.broadcast);
  ASSERT_TRUE(decode(encode()));
  EXPECT_TRUE(decoded.broadcast);
}

TEST_F(FleetCommandTest, CompactBroadcastCapacityAndCommandLimitRemainBounded) {
  Fleet::Targets targets{};
  const std::string prefix = "set flood.rule.1 ";
  const std::string maximum = prefix + std::string(Fleet::MaxCommandLength - prefix.size(), 'a');
  const auto bytes = encodeTargets(targets, maximum.c_str());
  ASSERT_EQ(150U, bytes.size());
  EXPECT_EQ(15U, encode(maximum.c_str()).size() - bytes.size());
  EXPECT_TRUE(decode(bytes));
  EXPECT_TRUE(decoded.broadcast);
  std::array<uint8_t, Fleet::MaxPayloadLength> output;
  output.fill(0xA5);
  EXPECT_EQ(0U, Fleet::encode(publisher, ChannelKey, Now, Now + 120, targets,
                            maximum.c_str(), output.data(), bytes.size() - 1));
  for (uint8_t byte : output) EXPECT_EQ(0xA5, byte);
  const std::string oversized = maximum + 'a';
  EXPECT_TRUE(encodeTargets(targets, oversized.c_str()).empty());
  for (size_t length = 0; length < bytes.size(); ++length) {
    const std::vector<uint8_t> truncated(bytes.begin(), bytes.begin() + length);
    EXPECT_FALSE(decode(truncated));
  }
  auto trailing = bytes;
  trailing.push_back(0);
  EXPECT_FALSE(decode(trailing));
  g_mock_ed25519_verify_result = false;
  EXPECT_FALSE(decode(bytes));
  EXPECT_FALSE(decoded.broadcast);
}

TEST_F(FleetCommandTest, SingleCompleteKeyKeepsLegacyFmc1Encoding) {
  Fleet::Targets targets{};
  char full[PUB_KEY_SIZE * 2 + 1];
  mesh::Utils::toHex(full, publisher.pub_key, PUB_KEY_SIZE);
  uint8_t hash[Fleet::TargetSize];
  ASSERT_TRUE(Fleet::parseTarget(full, hash));
  ASSERT_TRUE(Fleet::parseTargets(full, 64, targets));
  EXPECT_EQ(encode("get radio2", Now, Now + 120, hash), encodeTargets(targets));
  EXPECT_TRUE(decode(encodeTargets(targets)));
  EXPECT_FALSE(decoded.broadcast);
}

TEST_F(FleetCommandTest, PrefixesAndListsUseFmc2WithSignedFraming) {
  char full[PUB_KEY_SIZE * 2 + 1];
  mesh::Utils::toHex(full, publisher.pub_key, PUB_KEY_SIZE);
  for (size_t width : {8U, 12U}) {
    Fleet::Targets targets{};
    ASSERT_TRUE(Fleet::parseTargets(full, width, targets));
    const auto bytes = encodeTargets(targets);
    ASSERT_FALSE(bytes.empty());
    EXPECT_EQ(std::string("FMC2"), std::string(bytes.begin(), bytes.begin() + 4));
    EXPECT_EQ(1, bytes[12]);
    EXPECT_EQ(width / 2, bytes[13]);
    EXPECT_TRUE(decode(bytes));
    EXPECT_TRUE(decoded.broadcast);
  }
  const std::string list = std::string(full) + ',' + full;
  Fleet::Targets targets{};
  ASSERT_TRUE(Fleet::parseTargets(list.data(), list.size(), targets));
  const auto bytes = encodeTargets(targets);
  EXPECT_TRUE(decode(bytes));
  EXPECT_TRUE(decoded.broadcast);
  EXPECT_EQ(2, bytes[12]);
}

TEST_F(FleetCommandTest, AnyMatchingEntryIsAcceptedButMalformedLaterEntryIsRejected) {
  char full[PUB_KEY_SIZE * 2 + 1];
  mesh::Utils::toHex(full, publisher.pub_key, PUB_KEY_SIZE);
  const std::string prefix(full, 8);
  Fleet::Targets targets{};
  const std::string list = prefix + ",01020304," + full;
  ASSERT_TRUE(Fleet::parseTargets(list.data(), list.size(), targets));
  auto bytes = encodeTargets(targets);
  ASSERT_TRUE(decode(bytes));
  bytes[18] = 7; // Unknown type after the first matching four-byte prefix.
  EXPECT_FALSE(decode(bytes));
  EXPECT_FALSE(decoded.broadcast);
  bytes = encodeTargets(targets);
  bytes[12] = 18;
  EXPECT_FALSE(decode(bytes));
  bytes = encodeTargets(targets);
  bytes[12] = 4;
  EXPECT_FALSE(decode(bytes));
  bytes = encodeTargets(targets);
  bytes[13 + targets.length] = 0;
  EXPECT_FALSE(decode(bytes));
  bytes = encodeTargets(targets);
  bytes[13 + targets.length] = 73;
  EXPECT_FALSE(decode(bytes));
  for (size_t length = 0; length < bytes.size(); ++length) {
    const std::vector<uint8_t> truncated(bytes.begin(), bytes.begin() + length);
    EXPECT_FALSE(decode(truncated));
  }
}

TEST_F(FleetCommandTest, RawPrefixesIncludeLegitimateZeroPrefixesAndCollisions) {
  Fleet::Targets targets{};
  ASSERT_TRUE(Fleet::parseTargets("00000000", 8, targets));
  const auto bytes = encodeTargets(targets);
  uint8_t first[PUB_KEY_SIZE] = {}, second[PUB_KEY_SIZE] = {};
  first[4] = 1;
  second[4] = 2;
  ASSERT_TRUE(Fleet::decode(publisher, ChannelKey, bytes.data(), bytes.size(),
                           Now, first, decoded));
  EXPECT_TRUE(decoded.broadcast);
  ASSERT_TRUE(Fleet::decode(publisher, ChannelKey, bytes.data(), bytes.size(),
                           Now, second, decoded));
  ASSERT_TRUE(Fleet::parseTargets("000000000100", 12, targets));
  const auto narrow = encodeTargets(targets);
  EXPECT_TRUE(Fleet::decode(publisher, ChannelKey, narrow.data(), narrow.size(),
                           Now, first, decoded));
  EXPECT_FALSE(Fleet::decode(publisher, ChannelKey, narrow.data(), narrow.size(),
                            Now, second, decoded));
  first[0] = 1;
  EXPECT_FALSE(Fleet::decode(publisher, ChannelKey, bytes.data(), bytes.size(),
                            Now, first, decoded));
}

TEST_F(FleetCommandTest, InvalidExternalTargetStructuresNeverWriteOutput) {
  std::array<uint8_t, Fleet::MaxPayloadLength> output;
  output.fill(0xA5);
  for (unsigned invalid = 0; invalid < 7; ++invalid) {
    Fleet::Targets targets{};
    if (invalid == 0) targets.length = 1;
    if (invalid == 1) targets.count = 1;
    if (invalid == 2) targets.count = 18;
    if (invalid == 3) targets.length = 87;
    if (invalid == 4) { targets.count = 1; targets.length = 5; targets.data[0] = 3; }
    if (invalid == 5) { targets.count = 2; targets.length = 5; targets.data[0] = 4; }
    if (invalid == 6) { targets.count = 1; targets.length = 4; targets.data[0] = 4; }
    EXPECT_EQ(0U, Fleet::encode(publisher, ChannelKey, Now, Now + 120,
                               targets, "get radio2", output.data(), output.size()));
    for (uint8_t byte : output) EXPECT_EQ(0xA5, byte);
  }
}

TEST_F(FleetCommandTest, LargestFmc2EnvelopeFitsAndOversizedCombinationWritesNothing) {
  const std::string prefix = "set flood.rule.1 ";
  const std::string maximum = prefix + std::string(Fleet::MaxCommandLength - prefix.size(), 'a');
  Fleet::Targets targets{};
  const char* three = "01020304,05060708,090A0B0C";
  ASSERT_TRUE(Fleet::parseTargets(three, strlen(three), targets));
  EXPECT_EQ(Fleet::MaxPayloadLength, encodeTargets(targets, maximum.c_str()).size());
  const char* four = "01020304,05060708,090A0B0C,01020304";
  ASSERT_TRUE(Fleet::parseTargets(four, strlen(four), targets));
  std::array<uint8_t, Fleet::MaxPayloadLength> output;
  output.fill(0xA5);
  EXPECT_EQ(0U, Fleet::encode(publisher, ChannelKey, Now, Now + 120,
                            targets, maximum.c_str(), output.data(), output.size()));
  for (uint8_t byte : output) EXPECT_EQ(0xA5, byte);
}

TEST_F(FleetCommandTest, UnrepresentableZeroDigestNeverBecomesAllDuringLegacyEncoding) {
  Fleet::Targets targets{};
  targets.count = 1;
  targets.length = 17;
  targets.data[0] = 16;
  const auto bytes = encodeTargets(targets);
  ASSERT_FALSE(bytes.empty());
  EXPECT_EQ(std::string("FMC2"), std::string(bytes.begin(), bytes.begin() + 4));
  EXPECT_FALSE(decode(bytes));
}

} // namespace

int main(int argc, char** argv) {
  ::testing::InitGoogleTest(&argc, argv);
  return RUN_ALL_TESTS();
}
