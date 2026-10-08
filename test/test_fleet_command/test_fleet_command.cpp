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

TEST_F(FleetCommandTest, NoGenericAdminNamespaceOrPrefixEscapes) {
  for (const char* command : {
      "get prv.key", "backup prv.key 1234567890ABCDEF", "get wifi.password",
      "set wifi.password abc", "setperm abc 3", "reboot", "start ota",
      "get fleet", "set fleet.channel abc", "region add usa", "get radio",
      "set radio 910.5,500,5,5", "get radio2.reply", "get radio2.scan",
      "set tx.reply both", "set radioat2 910.5,500,5,5,rxtx,+5",
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

} // namespace

int main(int argc, char** argv) {
  ::testing::InitGoogleTest(&argc, argv);
  return RUN_ALL_TESTS();
}
