#include <gtest/gtest.h>
#include <Ed25519.h>
#include <helpers/FleetCommand.h>
#include <SHA256.h>
#include <algorithm>
#include <array>
#include <string>
#include <utility>
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

struct RegionContext {
  std::vector<std::string> configured;
  std::vector<std::string> home;
  std::vector<std::pair<Fleet::RegionTarget, std::string>> seen;

  static bool match(void* context, Fleet::RegionTarget kind, const char* name, size_t length) {
    auto& regions = *static_cast<RegionContext*>(context);
    const std::string label(name, length);
    regions.seen.emplace_back(kind, label);
    const auto& names = kind == Fleet::RegionTarget::Configured ? regions.configured : regions.home;
    return std::find(names.begin(), names.end(), label) != names.end();
  }
};

struct GeoContext {
  bool known = true;
  int32_t latitude_e6 = 47606200, longitude_e6 = -122332100;
  unsigned calls = 0;
  int32_t received_latitude = 0, received_longitude = 0;
  uint32_t received_radius = 0;

  static bool match(void* context, int32_t latitude, int32_t longitude, uint32_t radius) {
    auto& location = *static_cast<GeoContext*>(context);
    ++location.calls;
    location.received_latitude = latitude;
    location.received_longitude = longitude;
    location.received_radius = radius;
    return location.known && Fleet::withinRadius(latitude, longitude, radius,
                                                location.latitude_e6, location.longitude_e6);
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
    std::vector<uint8_t> bytes(Fleet::MaxEnvelopeLength);
    const size_t size = Fleet::encode(publisher, ChannelKey, sequence, expires,
        target ? target : all.data(), command, bytes.data(), bytes.size());
    bytes.resize(size);
    return bytes;
  }

  bool decode(const std::vector<uint8_t>& bytes, uint32_t now = Now) {
    return Fleet::decode(publisher, ChannelKey, bytes.data(), bytes.size(),
                         now, publisher.pub_key, decoded);
  }

  bool decodeRegions(const std::vector<uint8_t>& bytes, RegionContext& regions) {
    return Fleet::decode(publisher, ChannelKey, bytes.data(), bytes.size(), Now,
                         publisher.pub_key, decoded, RegionContext::match, &regions);
  }

  bool decodeGeo(const std::vector<uint8_t>& bytes, GeoContext& location,
                 RegionContext* regions = nullptr) {
    return Fleet::decode(publisher, ChannelKey, bytes.data(), bytes.size(), Now,
                         publisher.pub_key, decoded, regions ? RegionContext::match : nullptr,
                         regions, GeoContext::match, &location);
  }

  std::vector<uint8_t> encodeTargets(const Fleet::Targets& targets,
                                    const char* command = "get radio2") {
    std::vector<uint8_t> bytes(Fleet::MaxEnvelopeLength);
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
  EXPECT_TRUE(decode(encodeTargets(Fleet::Targets{}, maximum.c_str())));
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

TEST_F(FleetCommandTest, LegacyPayloadLimitFitsAndCannotBeRaisedByDestinationCapacity) {
  const std::string prefix = "set flood.rule.1 ";
  const std::string maximum = prefix + std::string(Fleet::FixedTargetMaxCommandLength - prefix.size(), 'a');
  ASSERT_TRUE(Fleet::commandAllowed(maximum.c_str()));
  const auto bytes = encode(maximum.c_str());
  ASSERT_EQ(Fleet::MaxEnvelopeLength, bytes.size());
  EXPECT_TRUE(decode(bytes));
  EXPECT_STREQ(maximum.c_str(), decoded.command);
  EXPECT_TRUE(Fleet::commandAllowed((maximum + "a").c_str()));
  EXPECT_TRUE(encode((maximum + "a").c_str()).empty());
  std::array<uint8_t, Fleet::MaxEnvelopeLength + 64> output;
  output.fill(0xA5);
  EXPECT_EQ(0U, Fleet::encode(publisher, ChannelKey, Now, Now + 10, all.data(),
                            maximum.c_str(), output.data() + 1, Fleet::MaxEnvelopeLength - 1));
  for (uint8_t value : output) EXPECT_EQ(0xA5, value);
  for (size_t command_length : {Fleet::FixedTargetMaxCommandLength + 1, Fleet::MaxCommandLength}) {
    const std::string oversized = prefix + std::string(command_length - prefix.size(), 'a');
    ASSERT_TRUE(Fleet::commandAllowed(oversized.c_str()));
    EXPECT_EQ(0U, Fleet::encode(publisher, ChannelKey, Now, Now + 10, all.data(),
                              oversized.c_str(), output.data(), output.size()));
    for (uint8_t value : output) EXPECT_EQ(0xA5, value);
  }
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
  const std::string token = "A1B2C3D4;010203040506;" + full;
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
      std::string(""), std::string("all;01020304"), std::string("01020304;all"),
      std::string("all;all"), std::string(";01020304"),
      std::string("01020304;"), std::string("01020304;;05060708"),
      std::string("01020304;0506070g"), std::string("01020304 05060708"),
      std::string("01020304; 05060708"), std::string("01020304\n"),
      std::string("01"), std::string(10, '1'), std::string(16, '1'),
      std::string(32, '1'), std::string(63, '1'), std::string(65, '1'),
      std::string(64, '0'), std::string("01020304;") + std::string(64, '0')}) {
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
    if (!list.empty()) list += ';';
    list += "01020304";
    Fleet::Targets targets{};
    ASSERT_TRUE(Fleet::parseTargets(list.data(), list.size(), targets));
    EXPECT_EQ(count, targets.count);
    EXPECT_EQ(count * 5, targets.length);
    EXPECT_FALSE(encodeTargets(targets).empty()) << count;
  }
  Fleet::Targets targets{};
  list += ";01020304";
  EXPECT_FALSE(Fleet::parseTargets(list.data(), list.size(), targets));
  list.clear();
  const std::string full = "0102030405060708090a0b0c0d0e0f101112131415161718191a1b1c1d1e1f20";
  for (unsigned count = 1; count <= 5; ++count) {
    if (!list.empty()) list += ';';
    list += full;
    ASSERT_TRUE(Fleet::parseTargets(list.data(), list.size(), targets));
    EXPECT_EQ(count * 17, targets.length);
  }
  EXPECT_FALSE(encodeTargets(targets).empty());
  list += ';' + full;
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
  ASSERT_EQ(230U, maximum.size());
  ASSERT_EQ(Fleet::MaxEnvelopeLength, bytes.size());
  ASSERT_TRUE(Fleet::commandAllowed(maximum.c_str()));
  EXPECT_TRUE(decode(bytes));
  EXPECT_STREQ(maximum.c_str(), decoded.command);
  EXPECT_TRUE(decoded.broadcast);
  std::array<uint8_t, Fleet::MaxEnvelopeLength> output;
  output.fill(0xA5);
  EXPECT_EQ(0U, Fleet::encode(publisher, ChannelKey, Now, Now + 120, targets,
                            maximum.c_str(), output.data(), bytes.size() - 1));
  for (uint8_t byte : output) EXPECT_EQ(0xA5, byte);
  const std::string oversized = maximum + 'a';
  EXPECT_FALSE(Fleet::commandAllowed(oversized.c_str()));
  EXPECT_TRUE(encodeTargets(targets, oversized.c_str()).empty());
  for (const char invalid : {'\n', ';', char(0x80)}) {
    const std::string malformed = maximum.substr(0, maximum.size() - 1) + invalid;
    EXPECT_FALSE(Fleet::commandAllowed(malformed.c_str()));
    EXPECT_TRUE(encodeTargets(targets, malformed.c_str()).empty());
  }
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
  const std::string list = std::string(full) + ';' + full;
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
  const std::string list = prefix + ";01020304;" + full;
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
  bytes[13 + targets.length] = Fleet::MaxCommandLength + 1;
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

TEST_F(FleetCommandTest, NamedRegionAndHomeRecordsPreserveCaseAndCanonicalHashAlias) {
  const std::string list = "sea;pdx;#Sea;region:all;region:dead;region:zzzzzzzz;home:$Private";
  Fleet::Targets targets{};
  ASSERT_TRUE(Fleet::parseTargets(list.data(), list.size(), targets));
  ASSERT_EQ(7, targets.count);
  size_t cursor = 0;
  for (const auto& item : std::vector<std::pair<uint8_t, std::string>>{
      {Fleet::RegionRecordType, "sea"}, {Fleet::RegionRecordType, "pdx"},
      {Fleet::RegionRecordType, "Sea"}, {Fleet::RegionRecordType, "all"},
      {Fleet::RegionRecordType, "dead"}, {Fleet::RegionRecordType, "zzzzzzzz"},
      {Fleet::HomeRecordType, "$Private"}}) {
    EXPECT_EQ(item.first, targets.data[cursor++]);
    EXPECT_EQ(item.second.size(), targets.data[cursor++]);
    EXPECT_EQ(item.second, std::string(reinterpret_cast<char*>(targets.data + cursor), item.second.size()));
    cursor += item.second.size();
  }
  EXPECT_EQ(cursor, targets.length);
  Fleet::Targets canonical{}, alias{};
  ASSERT_TRUE(Fleet::parseTargets("home:#sea", 9, alias));
  ASSERT_TRUE(Fleet::parseTargets("home:sea", 8, canonical));
  EXPECT_EQ(encodeTargets(canonical), encodeTargets(alias));
  ASSERT_TRUE(Fleet::parseTargets("ALL", 3, targets));
  EXPECT_EQ(Fleet::RegionRecordType, targets.data[0]);
  EXPECT_EQ("ALL", std::string(reinterpret_cast<char*>(targets.data + 2), 3));
}

TEST_F(FleetCommandTest, MalformedKeysNeverFallBackToNamedRegionTargets) {
  for (const std::string& token : {
      std::string("zzzzzzzz"), std::string("not-a-key-id"), std::string(12, 'z'),
      std::string(64, 'g'), std::string("a"), std::string("dead"), std::string(16, 'a'),
      std::string("region:"), std::string("home:"), std::string("#"), std::string("region:#"),
      std::string("region:*"), std::string("home:*"), std::string("region:with.space"),
      std::string("region:with space"), std::string("region:with:colon"),
      std::string("region:bad\x7f"), std::string("region:bad\x80"),
      std::string("region:bad\n"), std::string("sea;all"), std::string("all;sea"),
      std::string("get"), std::string("set"), std::string("del"), std::string("time"), std::string("clock"),
      std::string("region:") + std::string(31, 'z'), std::string("home:") + std::string(31, 'z')}) {
    Fleet::Targets targets;
    memset(&targets, 0xA5, sizeof(targets));
    EXPECT_FALSE(Fleet::parseTargets(token.data(), token.size(), targets)) << token;
    EXPECT_EQ(0, targets.length);
    EXPECT_EQ(0, targets.count);
  }
  for (const char* token : {"region:a", "region:dead", "region:zzzzzzzz", "region:all",
                            "home:a", "home:zzzzzzzz", "home:all", "region:get", "home:get", "#get",
                            "region:set", "home:del", "region:time", "home:clock"}) {
    Fleet::Targets targets{};
    EXPECT_TRUE(Fleet::parseTargets(token, strlen(token), targets)) << token;
  }
}

TEST_F(FleetCommandTest, NamedTargetsRequireExactReadOnlyMatchingAndRemainBroadcast) {
  Fleet::Targets targets{};
  ASSERT_TRUE(Fleet::parseTargets("sea", 3, targets));
  const auto bytes = encodeTargets(targets);
  ASSERT_FALSE(bytes.empty());
  EXPECT_FALSE(decode(bytes)); // A role without RegionMap matching grants no named capability.
  for (const char* label : {"sea", "Sea", "se", "seattle", "$sea", "pdx"}) {
    RegionContext regions{{label}, {"sea"}, {}};
    EXPECT_EQ(!strcmp(label, "sea"), decodeRegions(bytes, regions)) << label;
    if (!strcmp(label, "sea")) EXPECT_TRUE(decoded.broadcast);
    else EXPECT_FALSE(decoded.broadcast);
    ASSERT_EQ(1U, regions.seen.size());
    EXPECT_EQ(Fleet::RegionTarget::Configured, regions.seen[0].first);
    EXPECT_EQ("sea", regions.seen[0].second);
  }
  ASSERT_TRUE(Fleet::parseTargets("home:#sea", 9, targets));
  const auto home = encodeTargets(targets);
  RegionContext configured_only{{"sea"}, {}, {}};
  EXPECT_FALSE(decodeRegions(home, configured_only));
  RegionContext home_only{{}, {"sea"}, {}};
  EXPECT_TRUE(decodeRegions(home, home_only));
  EXPECT_TRUE(decoded.broadcast);
  EXPECT_EQ(Fleet::RegionTarget::Home, home_only.seen[0].first);
  EXPECT_FALSE(Fleet::parseTargets("$Private", 8, targets)); // Raw key-length tokens are never labels.
  ASSERT_TRUE(Fleet::parseTargets("region:$Private", 15, targets));
  RegionContext private_name{{"$Private"}, {}, {}};
  EXPECT_TRUE(decodeRegions(encodeTargets(targets), private_name));
}

TEST_F(FleetCommandTest, MixedHomeRegionsAndPublicKeysMatchOnceButValidateAllRecords) {
  char full[PUB_KEY_SIZE * 2 + 1];
  mesh::Utils::toHex(full, publisher.pub_key, PUB_KEY_SIZE);
  const std::string list = "sea;home:pdx;" + std::string(full, 8) + ";region:$fleet";
  Fleet::Targets targets{};
  ASSERT_TRUE(Fleet::parseTargets(list.data(), list.size(), targets));
  const auto bytes = encodeTargets(targets);
  EXPECT_TRUE(decode(bytes)); // The exact key prefix remains sufficient without a region matcher.
  EXPECT_TRUE(decoded.broadcast);
  RegionContext regions{{"sea", "$fleet"}, {"pdx"}, {}};
  ASSERT_TRUE(decodeRegions(bytes, regions));
  EXPECT_EQ(3U, regions.seen.size());
  EXPECT_STREQ("get radio2", decoded.command);
  auto malformed = bytes;
  const size_t final_name = 13 + 5 + 5 + 5 + 2;
  malformed[final_name] = '*'; // Invalid final label after a matching region and key.
  EXPECT_FALSE(decodeRegions(malformed, regions));
  EXPECT_FALSE(decoded.broadcast);
  g_mock_ed25519_verify_result = false;
  EXPECT_FALSE(decodeRegions(bytes, regions));
  EXPECT_FALSE(decoded.broadcast);
}

TEST_F(FleetCommandTest, NamedTargetCountsAndBytesConsumeTheSameBoundedBudget) {
  std::string list;
  Fleet::Targets targets{};
  for (unsigned count = 1; count <= 17; ++count) {
    if (!list.empty()) list += ';';
    list += "sea";
    ASSERT_TRUE(Fleet::parseTargets(list.data(), list.size(), targets));
    EXPECT_EQ(count * 5, targets.length);
  }
  list += ";sea";
  EXPECT_FALSE(Fleet::parseTargets(list.data(), list.size(), targets));
  list = "region:" + std::string(30, 'z') + ";home:" + std::string(30, 'Y')
      + ";region:" + std::string(20, 'x');
  ASSERT_TRUE(Fleet::parseTargets(list.data(), list.size(), targets));
  EXPECT_EQ(Fleet::MaxTargetBytes, targets.length);
  const std::string prefix = "set flood.rule.1 ";
  const std::string command = prefix + std::string(Fleet::MaxCommandLength - targets.length - prefix.size(), 'a');
  const auto bytes = encodeTargets(targets, command.c_str());
  ASSERT_EQ(Fleet::MaxEnvelopeLength, bytes.size());
  RegionContext regions{{std::string(30, 'z')}, {}, {}};
  EXPECT_TRUE(decodeRegions(bytes, regions));
  EXPECT_TRUE(decoded.broadcast);
  EXPECT_TRUE(encodeTargets(targets, (command + 'a').c_str()).empty());
  std::array<uint8_t, Fleet::MaxPayloadLength> first{}, second{};
  ASSERT_EQ(first.size(), Fleet::fragment(bytes.data(), bytes.size(), 0, first.data(), first.size()));
  ASSERT_EQ(second.size(), Fleet::fragment(bytes.data(), bytes.size(), 1, second.data(), second.size()));
  Fleet::Fragment part{};
  ASSERT_TRUE(Fleet::parseFragment(second.data(), second.size(), part));
  EXPECT_EQ(154U, part.length);
  list += 'x';
  EXPECT_FALSE(Fleet::parseTargets(list.data(), list.size(), targets));
  EXPECT_EQ(0, targets.length);
}

TEST_F(FleetCommandTest, InvalidExternalNamedRecordsNeverWriteOutput) {
  std::array<uint8_t, Fleet::MaxEnvelopeLength> output;
  output.fill(0xA5);
  for (unsigned invalid = 0; invalid < 8; ++invalid) {
    Fleet::Targets targets{};
    targets.count = 1;
    targets.length = 5;
    targets.data[0] = Fleet::RegionRecordType;
    targets.data[1] = 3;
    memcpy(targets.data + 2, "sea", 3);
    if (invalid == 0) targets.data[1] = 0;
    if (invalid == 1) targets.data[1] = 31;
    if (invalid == 2) targets.data[2] = '*';
    if (invalid == 3) targets.data[2] = 0;
    if (invalid == 4) targets.data[2] = 0x80;
    if (invalid == 5) targets.data[2] = ';';
    if (invalid == 6) targets.length = 1;
    if (invalid == 7) targets.data[0] = 0x22;
    EXPECT_EQ(0U, Fleet::encode(publisher, ChannelKey, Now, Now + 120,
                               targets, "get radio2", output.data(), output.size()));
    for (uint8_t byte : output) EXPECT_EQ(0xA5, byte);
  }
}

TEST_F(FleetCommandTest, GpsTargetsUseExactSignedMicrodegreesAndMetres) {
  struct Case { const char* token; int32_t latitude, longitude; uint32_t radius; };
  for (const auto& item : std::vector<Case>{
      {"gps:47.6062,-122.3321:10", 47606200, -122332100, 10000},
      {"gps:+90,-180:0.001", 90000000, -180000000, 1},
      {"gps:-90,+180:20050.000", -90000000, 180000000, 20050000},
      {"gps:0.000001,-0.000001:1.001", 1, -1, 1001},
      {"gps:0,0:1", 0, 0, 1000}}) {
    SCOPED_TRACE(item.token);
    Fleet::Targets targets{};
    ASSERT_TRUE(Fleet::parseTargets(item.token, strlen(item.token), targets));
    ASSERT_EQ(1, targets.count);
    ASSERT_EQ(Fleet::GeoRecordLength, targets.length);
    EXPECT_EQ(Fleet::GeoRecordType, targets.data[0]);
    const uint32_t values[] = {uint32_t(item.latitude), uint32_t(item.longitude), item.radius};
    for (unsigned field = 0; field < 3; ++field)
      for (unsigned byte = 0; byte < 4; ++byte)
        EXPECT_EQ(uint8_t(values[field] >> (byte * 8)), targets.data[1 + field * 4 + byte]);
    EXPECT_FALSE(decode(encodeTargets(targets))); // No location provider means no geographic match.
    GeoContext location;
    location.latitude_e6 = item.latitude; location.longitude_e6 = item.longitude;
    ASSERT_TRUE(decodeGeo(encodeTargets(targets), location));
    EXPECT_TRUE(decoded.broadcast);
    EXPECT_EQ(item.latitude, location.received_latitude);
    EXPECT_EQ(item.longitude, location.received_longitude);
    EXPECT_EQ(item.radius, location.received_radius);
  }
}

TEST_F(FleetCommandTest, SemicolonsSeparateTargetsAndGpsOwnsOnlyItsCoordinateComma) {
  const std::string gps = "gps:47.6062,-122.3321:25";
  for (const std::string& token : {gps + ";sea;home:pdx", "sea;" + gps + ";home:pdx",
                                  "sea;home:pdx;" + gps}) {
    SCOPED_TRACE(token);
    Fleet::Targets targets{};
    const std::string input = token + " get radio2";
    ASSERT_TRUE(Fleet::parseTargets(input.data(), token.size(), targets));
    EXPECT_EQ(3, targets.count);
    EXPECT_EQ(23, targets.length);
    GeoContext location;
    RegionContext regions{{"sea"}, {"pdx"}, {}};
    ASSERT_TRUE(decodeGeo(encodeTargets(targets), location, &regions));
    EXPECT_EQ(1U, location.calls);
    EXPECT_EQ(25000U, location.received_radius);
    EXPECT_EQ(2U, regions.seen.size());
    EXPECT_TRUE(decoded.broadcast);
  }
  // The explicit substring bound must hold even without a terminating NUL.
  const std::vector<char> nonterminated(gps.begin(), gps.end());
  for (size_t length = 4; length <= nonterminated.size(); ++length) {
    Fleet::Targets targets;
    memset(&targets, 0xA5, sizeof(targets));
    const bool complete_radius = length == gps.size() || length == gps.size() - 1;
    EXPECT_EQ(complete_radius,
              Fleet::parseTargets(nonterminated.data(), length, targets)) << length;
    if (!complete_radius) {
      EXPECT_EQ(0, targets.count);
      EXPECT_EQ(0, targets.length);
    }
  }
}

TEST_F(FleetCommandTest, OldCommaListsAndMalformedGpsSeparatorsRejectTheEntireList) {
  for (const char* token : {
      "sea,pdx", "01020304,05060708", "region:sea,home:pdx", "sea;home:pdx,sea",
      "gps:47.6062:-122.3321:25", "gps:47.6062;-122.3321:25",
      "gps:47.6062,-122.3321,25", "gps:47.6062,,-122.3321:25",
      "gps:47.6062,-122.3321,:25", "gps:47.6062,-122.3321:25,sea",
      ";gps:47.6062,-122.3321:25", "gps:47.6062,-122.3321:25;",
      "sea;;gps:47.6062,-122.3321:25", "sea;gps:47.6062,-122.3321:25;;home:pdx",
      "sea;gps:47.6062;home:pdx", "sea;gps:47.6062,-122.3321;home:pdx",
      "sea;gps:47.6062,-122.3321:;home:pdx", "sea;gps:47.6062,-122.3321:25;home:pdx,sea"}) {
    SCOPED_TRACE(token);
    Fleet::Targets targets;
    memset(&targets, 0xA5, sizeof(targets));
    EXPECT_FALSE(Fleet::parseTargets(token, strlen(token), targets));
    EXPECT_EQ(0, targets.count);
    EXPECT_EQ(0, targets.length);
    for (uint8_t byte : targets.data) EXPECT_EQ(0, byte);
  }
}

TEST_F(FleetCommandTest, SemicolonMixedGpsListsEnforceRecordAndByteBudgets) {
  std::string list = "gps:47.6062,-122.3321:25";
  Fleet::Targets targets{};
  for (unsigned count = 2; count <= 17; ++count) {
    list += ";z";
    ASSERT_TRUE(Fleet::parseTargets(list.data(), list.size(), targets));
    EXPECT_EQ(count, targets.count);
    EXPECT_EQ(13 + (count - 1) * 3, targets.length);
  }
  list += ";z";
  EXPECT_FALSE(Fleet::parseTargets(list.data(), list.size(), targets));
  EXPECT_EQ(0, targets.count);
  list = "gps:47.6062,-122.3321:25";
  for (unsigned count = 0; count < 14; ++count) list += ";sea";
  ASSERT_TRUE(Fleet::parseTargets(list.data(), list.size(), targets));
  EXPECT_EQ(83, targets.length);
  list += ";sea";
  EXPECT_FALSE(Fleet::parseTargets(list.data(), list.size(), targets));
  EXPECT_EQ(0, targets.length);
}

TEST_F(FleetCommandTest, GpsDecimalGrammarAndBoundsRejectWithoutPartialTargets) {
  for (const char* token : {
      "gps:", "gps:0", "gps:0:0", "gps:0,0:", "gps:,0:1", "gps:0,:1",
      "gps:0,0:1:2", "gps:0,0:0", "gps:0,0:0.000", "gps:0,0:0.0001",
      "gps:0,0:20050.001", "gps:90.000001,0:1", "gps:-90.000001,0:1",
      "gps:0,180.000001:1", "gps:0,-180.000001:1", "gps:0.0000001,0:1",
      "gps:0,0.0000001:1", "gps:0,0:1.0001", "gps:0,0:+1", "gps:0,0:-1",
      "gps:NaN,0:1", "gps:0,Inf:1", "gps:0,0:1e3", "gps:1e1,0:1",
      "gps:.1,0:1", "gps:1.,0:1", "gps:0,0:.1", "gps:0,0:1.",
      "gps:01,0:1", "gps:0,00:1", "gps:0,0:01", "gps:--1,0:1",
      "gps:++1,0:1", "gps:+,0:1", "gps:0,0:4294967296", "gps:999999999999999999,0:1",
      "gps:0,0:1m", "gps:0,0:1\n", "gps: 0:0:1", "gps:0,0:1;all"}) {
    Fleet::Targets targets;
    memset(&targets, 0xA5, sizeof(targets));
    EXPECT_FALSE(Fleet::parseTargets(token, strlen(token), targets)) << token;
    EXPECT_EQ(0, targets.count);
    EXPECT_EQ(0, targets.length);
  }
}

TEST_F(FleetCommandTest, GeographicCircleIsDefensiveAtPolesDatelineAndAntipodes) {
  EXPECT_TRUE(Fleet::withinRadius(0, 0, 1, 0, 0));
  EXPECT_FALSE(Fleet::withinRadius(0, 0, 0, 0, 0));
  EXPECT_FALSE(Fleet::withinRadius(0, 0, Fleet::MaxRadiusMeters + 1, 0, 0));
  EXPECT_FALSE(Fleet::withinRadius(90000001, 0, 1, 0, 0));
  EXPECT_FALSE(Fleet::withinRadius(0, 180000001, 1, 0, 0));
  EXPECT_FALSE(Fleet::withinRadius(0, 0, 1, -90000001, 0));
  EXPECT_FALSE(Fleet::withinRadius(0, 0, 1, 0, -180000001));
  EXPECT_FALSE(Fleet::withinRadius(0, 0, 111195, 0, 1000000));
  EXPECT_TRUE(Fleet::withinRadius(0, 0, 111196, 0, 1000000));
  EXPECT_TRUE(Fleet::withinRadius(0, 179999999, 1, 0, -179999999));
  EXPECT_TRUE(Fleet::withinRadius(90000000, 0, 1, 90000000, 180000000));
  EXPECT_FALSE(Fleet::withinRadius(0, 0, 20015114, 0, 180000000));
  EXPECT_TRUE(Fleet::withinRadius(0, 0, 20015115, 0, 180000000));
  EXPECT_TRUE(Fleet::withinRadius(47606200, -122332100, 10000, 47610000, -122330000));
  EXPECT_FALSE(Fleet::withinRadius(47606200, -122332100, 10000, 45515200, -122678400));
}

TEST_F(FleetCommandTest, MixedGeographicRegionHomeAndKeyTargetsAreBoundedOrMatches) {
  char full[PUB_KEY_SIZE * 2 + 1];
  mesh::Utils::toHex(full, publisher.pub_key, PUB_KEY_SIZE);
  const std::string token = "gps:47.6062,-122.3321:1;sea;home:pdx;" + std::string(full, 8);
  Fleet::Targets targets{};
  ASSERT_TRUE(Fleet::parseTargets(token.data(), token.size(), targets));
  ASSERT_EQ(4, targets.count);
  EXPECT_EQ(13 + 5 + 5 + 5, targets.length);
  const auto bytes = encodeTargets(targets);
  EXPECT_TRUE(decode(bytes)); // Public-key prefix can match without either callback.
  GeoContext location;
  EXPECT_TRUE(decodeGeo(bytes, location));
  EXPECT_TRUE(decoded.broadcast);
  RegionContext regions{{"sea"}, {"pdx"}, {}};
  EXPECT_TRUE(decodeGeo(bytes, location, &regions));
  EXPECT_EQ(2U, regions.seen.size());
  EXPECT_STREQ("get radio2", decoded.command);
  const char* geo_only = "gps:47.6062,-122.3321:1";
  ASSERT_TRUE(Fleet::parseTargets(geo_only, strlen(geo_only), targets));
  const auto geo_bytes = encodeTargets(targets);
  location.known = false;
  EXPECT_FALSE(decodeGeo(geo_bytes, location));
  location.known = true; location.latitude_e6 = 45515200; location.longitude_e6 = -122678400;
  EXPECT_FALSE(decodeGeo(geo_bytes, location));
  // Invalid geometry after a matching key still rejects the complete request.
  const std::string reversed = std::string(full, 8) + ';' + geo_only;
  ASSERT_TRUE(Fleet::parseTargets(reversed.data(), reversed.size(), targets));
  auto malformed = encodeTargets(targets);
  memset(malformed.data() + 13 + 5 + 1 + 8, 0, 4); // Zero-radius final record.
  EXPECT_FALSE(decode(malformed));
  EXPECT_FALSE(decoded.broadcast);
}

TEST_F(FleetCommandTest, GeographicRecordUsesThirteenBytesAndTwoFragmentCommandBudget) {
  const char* token = "gps:47.6062,-122.3321:1";
  Fleet::Targets targets{};
  ASSERT_TRUE(Fleet::parseTargets(token, strlen(token), targets));
  const std::string prefix = "set flood.rule.1 ";
  const std::string maximum = prefix + std::string(217 - prefix.size(), 'a');
  auto bytes = encodeTargets(targets, maximum.c_str());
  ASSERT_EQ(Fleet::MaxEnvelopeLength, bytes.size());
  GeoContext location;
  EXPECT_TRUE(decodeGeo(bytes, location));
  EXPECT_TRUE(encodeTargets(targets, (maximum + 'a').c_str()).empty());
  std::string list;
  for (unsigned count = 1; count <= 6; ++count) {
    if (!list.empty()) list += ';';
    list += token;
    ASSERT_TRUE(Fleet::parseTargets(list.data(), list.size(), targets));
    EXPECT_EQ(count * 13, targets.length);
  }
  list += ';' + std::string(token);
  EXPECT_FALSE(Fleet::parseTargets(list.data(), list.size(), targets));
  EXPECT_EQ(0, targets.count);
  std::array<uint8_t, Fleet::MaxPayloadLength> part{};
  ASSERT_EQ(part.size(), Fleet::fragment(bytes.data(), bytes.size(), 0, part.data(), part.size()));
  Fleet::Fragment parsed{};
  ASSERT_TRUE(Fleet::parseFragment(part.data(), part.size(), parsed));
  EXPECT_FALSE(decodeGeo(std::vector<uint8_t>(part.begin(), part.end()), location));
}

TEST_F(FleetCommandTest, ExternalGeographicRecordBoundsRejectBeforeAnyOutputWrite) {
  std::array<uint8_t, Fleet::MaxEnvelopeLength> output;
  output.fill(0xA5);
  for (unsigned invalid = 0; invalid < 5; ++invalid) {
    Fleet::Targets targets{};
    const char* token = "gps:0,0:1";
    ASSERT_TRUE(Fleet::parseTargets(token, strlen(token), targets));
    if (invalid == 0) targets.length = 12;
    if (invalid == 1) memset(targets.data + 9, 0, 4);
    if (invalid == 2) memset(targets.data + 1, 0x7f, 4);
    if (invalid == 3) memset(targets.data + 5, 0x7f, 4);
    if (invalid == 4) memset(targets.data + 9, 0xff, 4);
    EXPECT_EQ(0U, Fleet::encode(publisher, ChannelKey, Now, Now + 120,
                               targets, "get radio2", output.data(), output.size()));
    for (uint8_t byte : output) EXPECT_EQ(0xA5, byte);
  }
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
  Fleet::Targets targets{};
  const char* three = "01020304;05060708;090A0B0C";
  ASSERT_TRUE(Fleet::parseTargets(three, strlen(three), targets));
  const std::string prefix = "set flood.rule.1 ";
  const std::string maximum = prefix + std::string(
      Fleet::MaxCommandLength - targets.length - prefix.size(), 'a');
  EXPECT_EQ(Fleet::MaxEnvelopeLength, encodeTargets(targets, maximum.c_str()).size());
  const char* four = "01020304;05060708;090A0B0C;01020304";
  ASSERT_TRUE(Fleet::parseTargets(four, strlen(four), targets));
  std::array<uint8_t, Fleet::MaxEnvelopeLength> output;
  output.fill(0xA5);
  EXPECT_EQ(0U, Fleet::encode(publisher, ChannelKey, Now, Now + 120,
                            targets, maximum.c_str(), output.data(), output.size()));
  for (uint8_t byte : output) EXPECT_EQ(0xA5, byte);
}

TEST_F(FleetCommandTest, EveryTargetFormUsesItsExactCommandBudgetWithoutPartialWrites) {
  char full[PUB_KEY_SIZE * 2 + 1];
  mesh::Utils::toHex(full, publisher.pub_key, PUB_KEY_SIZE);
  const std::string short_key(full, 8), long_key(full, 12);
  const std::string prefix = "set flood.rule.1 ";
  const std::vector<std::pair<std::string, size_t>> cases = {
    {"all", 230}, {short_key, 225}, {long_key, 223}, {full, 215},
    {short_key + ';' + short_key, 220}, {short_key + ';' + long_key, 218},
    {std::string(full) + ';' + short_key, 208}, {std::string(full) + ';' + full, 196}
  };
  for (const auto& item : cases) {
    SCOPED_TRACE(item.first);
    Fleet::Targets targets{};
    ASSERT_TRUE(Fleet::parseTargets(item.first.data(), item.first.size(), targets));
    const std::string maximum = prefix + std::string(item.second - prefix.size(), 'a');
    ASSERT_TRUE(Fleet::commandAllowed(maximum.c_str()));
    const auto bytes = encodeTargets(targets, maximum.c_str());
    ASSERT_EQ(Fleet::MaxEnvelopeLength, bytes.size());
    ASSERT_TRUE(decode(bytes));
    EXPECT_STREQ(maximum.c_str(), decoded.command);
    const std::string oversized = maximum + 'a';
    EXPECT_TRUE(encodeTargets(targets, oversized.c_str()).empty());
    std::array<uint8_t, Fleet::MaxEnvelopeLength + 64> output;
    output.fill(0xA5);
    EXPECT_EQ(0U, Fleet::encode(publisher, ChannelKey, Now, Now + 120, targets,
                              oversized.c_str(), output.data(), output.size()));
    for (uint8_t byte : output) EXPECT_EQ(0xA5, byte);
  }
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

TEST_F(FleetCommandTest, SinglePacketBoundariesRemainExactBeforeFragmentation) {
  char full[PUB_KEY_SIZE * 2 + 1];
  mesh::Utils::toHex(full, publisher.pub_key, PUB_KEY_SIZE);
  const std::string prefix = "set flood.rule.1 ";
  for (const auto& item : std::vector<std::pair<std::string, size_t>>{
      {"all", 87}, {std::string(full, 8), 82}, {std::string(full, 12), 80}, {full, 72}}) {
    Fleet::Targets targets{};
    ASSERT_TRUE(Fleet::parseTargets(item.first.data(), item.first.size(), targets));
    const std::string command = prefix + std::string(item.second - prefix.size(), 'a');
    const auto single = encodeTargets(targets, command.c_str());
    ASSERT_EQ(Fleet::MaxPayloadLength, single.size());
    std::array<uint8_t, Fleet::MaxPayloadLength> output;
    output.fill(0xA5);
    EXPECT_EQ(0U, Fleet::fragment(single.data(), single.size(), 0, output.data(), output.size()));
    for (uint8_t byte : output) EXPECT_EQ(0xA5, byte);
    const auto longer = encodeTargets(targets, (command + 'a').c_str());
    ASSERT_EQ(Fleet::MaxPayloadLength + 1, longer.size());
    EXPECT_EQ(Fleet::MaxPayloadLength,
              Fleet::fragment(longer.data(), longer.size(), 0, output.data(), output.size()));
    EXPECT_EQ(Fleet::FragmentHeaderSize + longer.size() - Fleet::FragmentDataLength,
              Fleet::fragment(longer.data(), longer.size(), 1, output.data(), output.size()));
  }
}

TEST_F(FleetCommandTest, TwoExactFragmentsReassembleInEitherOrderAndVerifyOnlyWholeEnvelope) {
  const std::string prefix = "set flood.rule.1 ";
  for (size_t command_length : {88U, 89U, 229U, 230U}) {
    const std::string command = prefix + std::string(command_length - prefix.size(), 'a');
    const auto envelope = encodeTargets(Fleet::Targets{}, command.c_str());
    ASSERT_EQ(Fleet::MinHeaderSize + command_length + Fleet::SignatureSize, envelope.size());
    std::array<std::vector<uint8_t>, 2> fragments;
    for (uint8_t index = 0; index < 2; ++index) {
      fragments[index].resize(Fleet::MaxPayloadLength);
      const size_t size = Fleet::fragment(envelope.data(), envelope.size(), index,
                                         fragments[index].data(), fragments[index].size());
      ASSERT_GT(size, 0U);
      ASSERT_LE(size, Fleet::MaxPayloadLength);
      fragments[index].resize(size);
    }
    for (bool reverse : {false, true}) {
      std::vector<uint8_t> assembled(envelope.size());
      const unsigned before = g_mock_ed25519_verify_calls;
      for (unsigned step = 0; step < 2; ++step) {
        const uint8_t index = reverse ? 1 - step : step;
        Fleet::Fragment part;
        ASSERT_TRUE(Fleet::parseFragment(fragments[index].data(), fragments[index].size(), part));
        EXPECT_EQ(Now, part.sequence);
        EXPECT_EQ(envelope.size(), part.total_length);
        EXPECT_EQ(index, part.index);
        EXPECT_EQ(index ? envelope.size() - Fleet::FragmentDataLength : Fleet::FragmentDataLength,
                  part.length);
        EXPECT_EQ(0, memcmp(part.data, envelope.data() + index * Fleet::FragmentDataLength, part.length));
        std::copy_n(part.data, part.length, assembled.begin() + index * Fleet::FragmentDataLength);
        EXPECT_FALSE(decode(fragments[index])); // A fragment cannot authorize a command.
        EXPECT_EQ(before, g_mock_ed25519_verify_calls);
        Fleet::Fragment duplicate;
        ASSERT_TRUE(Fleet::parseFragment(fragments[index].data(), fragments[index].size(), duplicate));
        EXPECT_EQ(part.data, duplicate.data);
        EXPECT_EQ(part.length, duplicate.length);
      }
      EXPECT_EQ(envelope, assembled);
      ASSERT_TRUE(decode(assembled));
      EXPECT_EQ(before + 1, g_mock_ed25519_verify_calls);
      EXPECT_STREQ(command.c_str(), decoded.command);
      EXPECT_TRUE(decoded.broadcast);
    }
  }
}

TEST_F(FleetCommandTest, FragmentEncoderRejectsInvalidInputBeforeWriting) {
  const std::string command = "set flood.rule.1 " + std::string(72, 'a');
  auto envelope = encodeTargets(Fleet::Targets{}, command.c_str());
  ASSERT_GT(envelope.size(), Fleet::MaxPayloadLength);
  std::array<uint8_t, Fleet::MaxPayloadLength + 64> output;
  output.fill(0xA5);
  for (size_t length : {size_t(0), Fleet::MaxPayloadLength, Fleet::MaxEnvelopeLength + 1})
    EXPECT_EQ(0U, Fleet::fragment(envelope.data(), length, 0, output.data(), output.size()));
  EXPECT_EQ(0U, Fleet::fragment(nullptr, envelope.size(), 0, output.data(), output.size()));
  EXPECT_EQ(0U, Fleet::fragment(envelope.data(), envelope.size(), 0, nullptr, output.size()));
  for (uint8_t index : {uint8_t(2), uint8_t(255)})
    EXPECT_EQ(0U, Fleet::fragment(envelope.data(), envelope.size(), index, output.data(), output.size()));
  EXPECT_EQ(0U, Fleet::fragment(envelope.data(), envelope.size(), 0, output.data(), 164));
  EXPECT_EQ(0U, Fleet::fragment(envelope.data(), envelope.size(), 1, output.data(),
                               Fleet::FragmentHeaderSize + envelope.size() - Fleet::FragmentDataLength - 1));
  envelope[0] = 'X';
  EXPECT_EQ(0U, Fleet::fragment(envelope.data(), envelope.size(), 0, output.data(), output.size()));
  for (uint8_t byte : output) EXPECT_EQ(0xA5, byte);
}

TEST_F(FleetCommandTest, FragmentParserRejectsMalformedLengthIndexAndMagicAndClearsOutput) {
  const std::string command = "set flood.rule.1 " + std::string(72, 'a');
  const auto envelope = encodeTargets(Fleet::Targets{}, command.c_str());
  std::vector<uint8_t> valid(Fleet::MaxPayloadLength);
  ASSERT_EQ(valid.size(), Fleet::fragment(envelope.data(), envelope.size(), 0, valid.data(), valid.size()));
  const auto reject = [&](const uint8_t* bytes, size_t length) {
    Fleet::Fragment part{123, 300, 1, valid.data(), 100};
    EXPECT_FALSE(Fleet::parseFragment(bytes, length, part));
    EXPECT_EQ(0U, part.sequence); EXPECT_EQ(0, part.total_length); EXPECT_EQ(0, part.index);
    EXPECT_EQ(nullptr, part.data); EXPECT_EQ(0U, part.length);
  };
  reject(nullptr, valid.size());
  for (size_t length = 0; length < valid.size(); ++length) reject(valid.data(), length);
  auto malformed = valid;
  malformed.push_back(0);
  reject(malformed.data(), malformed.size());
  malformed = valid; malformed[0] = 'X'; reject(malformed.data(), malformed.size());
  for (uint16_t total : {uint16_t(0), uint16_t(165), uint16_t(309), uint16_t(65535)}) {
    malformed = valid; malformed[8] = uint8_t(total); malformed[9] = uint8_t(total >> 8);
    reject(malformed.data(), malformed.size());
  }
  for (uint8_t index : {uint8_t(1), uint8_t(2), uint8_t(255)}) {
    malformed = valid; malformed[10] = index; reject(malformed.data(), malformed.size());
  }
  valid.resize(Fleet::MaxPayloadLength);
  valid.resize(Fleet::fragment(envelope.data(), envelope.size(), 1, valid.data(), valid.size()));
  ASSERT_FALSE(valid.empty());
  malformed = valid; malformed.pop_back(); reject(malformed.data(), malformed.size());
  malformed = valid; malformed.push_back(0); reject(malformed.data(), malformed.size());
  Fleet::Fragment part;
  ASSERT_TRUE(Fleet::parseFragment(valid.data(), valid.size(), part));
  EXPECT_EQ(envelope.size() - Fleet::FragmentDataLength, part.length);
}

} // namespace

int main(int argc, char** argv) {
  ::testing::InitGoogleTest(&argc, argv);
  return RUN_ALL_TESTS();
}
