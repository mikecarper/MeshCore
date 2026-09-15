#include <gtest/gtest.h>
#include <helpers/FloodFilterPolicy.h>
#include "../fixtures/radio_profiles/mocks/helpers/IdentityStore.h"

// Compile the production parser, authenticated matcher, ordering, rate handling
// and atomic FPF7 reader/writer against an in-memory filesystem. Crypto and
// packets are the real native-build implementations. These tests use no regions.
#define MESH_ENABLE_ROOM_FLOOD_RULE_ENGINE 1
#include "../../examples/simple_room_server/FloodRuleEngine.cpp"
RegionEntry* RegionMap::findByName(const char*) { return nullptr; }
RegionEntry* RegionMap::findByNamePrefix(const char*) { return nullptr; }
int RegionMap::getTransportKeysFor(const RegionEntry&, TransportKey[], int) { return 0; }
uint16_t TransportKey::calcTransportCode(const mesh::Packet*) const { return 0; }
bool TransportKey::isNull() const { return true; }
bool RegionMap::is_name_char(uint8_t c) {
  return c == '-' || c == '$' || c == '#' || (c >= '0' && c <= '9') || c >= 'A';
}

using namespace FloodFilterPolicy;

int main(int argc, char** argv) {
  testing::InitGoogleTest(&argc, argv);
  return RUN_ALL_TESTS();
}

TEST(TransportModes, StoragePreservesLegacyAndEverySupportedCombination) {
  for (uint8_t modes : {0, 2, 4, 6}) {
    bool active = false;
    uint8_t restored = 255;
    ASSERT_TRUE(decodeStoredRuleActive(encodeStoredRuleActive(true, modes), active, restored));
    EXPECT_TRUE(active);
    EXPECT_EQ(modes, restored);
    ASSERT_TRUE(decodeStoredRuleActive(encodeStoredRuleActive(false, modes), active, restored));
    EXPECT_FALSE(active);
    EXPECT_EQ(RULE_MODE_RADIO, restored);
  }
  for (uint8_t invalid : {2, 4, 6, 8, 9, 255}) {
    bool active; uint8_t modes;
    EXPECT_FALSE(decodeStoredRuleActive(invalid, active, modes));
  }
}

TEST(TransportModes, SelectorsAreStrictAndCanonical) {
  for (const char* text : {"radio", "bridge", "cross", "bridge,cross", "CROSS,BRIDGE"}) {
    uint8_t modes;
    ASSERT_TRUE(parseRuleModes(text, modes));
    uint8_t roundtrip;
    ASSERT_TRUE(parseRuleModes(ruleModeName(modes), roundtrip));
    EXPECT_EQ(modes, roundtrip);
  }
  for (const char* text : {"", "all", "bridge,", ",cross", "bridge,bridge", "radio,cross", "cross,unknown"}) {
    uint8_t modes;
    EXPECT_FALSE(parseRuleModes(text, modes));
  }
}

class TransportFilter : public testing::Test {
protected:
  MemoryFS fs;
  FloodRuleEngine rules;
  char reply[160] = {};
  void SetUp() override { rules.begin(&fs, nullptr); }
  void command(const char* text) { ASSERT_TRUE(rules.handleCommand(text, reply)); }
  void install(const char* modes = "bridge,cross") {
    std::string cmd = "set flood.rule.3 type=any channel=#wardriving hops=all mode=";
    cmd += modes; cmd += " drop";
    command(cmd.c_str()); ASSERT_EQ(0, strncmp(reply, "OK", 2)) << reply;
  }
  mesh::Packet packet(const char* channel = "#wardriving", uint8_t type = PAYLOAD_TYPE_GRP_TXT,
                      uint8_t route = ROUTE_TYPE_FLOOD, uint8_t hops = 0) {
    mesh::Packet p;
    p.header = (type << PH_TYPE_SHIFT) | route;
    p.setPathHashSizeAndCount(1, hops);
    memset(p.path, 0x12, hops);
    uint8_t key[PUB_KEY_SIZE] = {};
    mesh::Utils::sha256(key, CIPHER_KEY_SIZE, (const uint8_t*)channel, strlen(channel));
    mesh::Utils::sha256(p.payload, 1, key, CIPHER_KEY_SIZE);
    const uint8_t text[] = "wardrive: sample";
    p.payload_len = 1 + mesh::Utils::encryptThenMAC(key, p.payload + 1, text, sizeof(text));
    return p;
  }
  bool blocked(mesh::Packet& p, uint8_t mode, bool temporary = false) {
    auto mask = rules.evaluate(&p, temporary, true, nullptr, mode);
    return rules.shouldBlock(&p, mask, 100);
  }
};

TEST_F(TransportFilter, OneRuleBlocksBothDirectionsAndKeepsRadioEligible) {
  install();
  for (uint8_t type : {PAYLOAD_TYPE_GRP_TXT, PAYLOAD_TYPE_GRP_DATA})
    for (uint8_t route : {ROUTE_TYPE_FLOOD, ROUTE_TYPE_TRANSPORT_FLOOD, ROUTE_TYPE_DIRECT, ROUTE_TYPE_TRANSPORT_DIRECT})
      for (uint8_t hops : {0, 4, 5, 63}) {
        auto p = packet("#wardriving", type, route, hops);
        EXPECT_TRUE(blocked(p, RULE_MODE_BRIDGE));
        EXPECT_TRUE(blocked(p, RULE_MODE_CROSS, true));
        EXPECT_FALSE(blocked(p, RULE_MODE_RADIO));
      }
  command("get flood.rule.3");
  EXPECT_NE(nullptr, strstr(reply, "mode=bridge,cross"));
  command("get flood.rule");
  EXPECT_NE(nullptr, strstr(reply, "~bridge,cross"));
}

TEST_F(TransportFilter, TemporaryWindowSuspendsOtaRuleWithoutDisablingWardrivingRule) {
  install();
  auto ota = packet("#wardriving", PAYLOAD_TYPE_OTA);
  EXPECT_TRUE(blocked(ota, RULE_MODE_RADIO, false));
  EXPECT_FALSE(blocked(ota, RULE_MODE_RADIO, true));
  auto wardriving = packet();
  EXPECT_TRUE(blocked(wardriving, RULE_MODE_BRIDGE, true));
  EXPECT_TRUE(blocked(wardriving, RULE_MODE_CROSS, true));
  EXPECT_TRUE(blocked(ota, RULE_MODE_RADIO, false)); // normal rule resumes on expiry
}

TEST_F(TransportFilter, AuthenticationRejectsHashCollisionAndBadMac) {
  install();
  auto wardriving = packet();
  mesh::Packet collision;
  bool found = false;
  for (unsigned i = 0; i < 10000; i++) {
    std::string name = "#different-" + std::to_string(i);
    collision = packet(name.c_str());
    if (collision.payload[0] == wardriving.payload[0]) { found = true; break; }
  }
  ASSERT_TRUE(found);
  EXPECT_FALSE(blocked(collision, RULE_MODE_BRIDGE));
  EXPECT_FALSE(blocked(collision, RULE_MODE_CROSS));
  wardriving.payload[1] ^= 1;
  EXPECT_FALSE(blocked(wardriving, RULE_MODE_BRIDGE));
  wardriving.payload_len = 1;
  EXPECT_FALSE(blocked(wardriving, RULE_MODE_CROSS));
  auto other_type = packet("#wardriving", PAYLOAD_TYPE_ADVERT);
  EXPECT_FALSE(blocked(other_type, RULE_MODE_CROSS));
}

TEST_F(TransportFilter, RadioHopLimitAndStopCannotOverrideTransportDrop) {
  install();
  command("set flood.rule.2 type=any channel=#wardriving hops=5+ drop");
  auto near = packet(); auto far = packet("#wardriving", PAYLOAD_TYPE_GRP_TXT, ROUTE_TYPE_FLOOD, 5);
  EXPECT_FALSE(blocked(near, RULE_MODE_RADIO));
  EXPECT_TRUE(blocked(far, RULE_MODE_RADIO));
  command("set flood.rule.4 type=any priority=255 stop");
  EXPECT_FALSE(blocked(far, RULE_MODE_RADIO));
  EXPECT_TRUE(blocked(near, RULE_MODE_BRIDGE));
  EXPECT_TRUE(blocked(far, RULE_MODE_CROSS));
  command("set flood.rule.4 type=any mode=bridge priority=255 stop");
  EXPECT_FALSE(blocked(near, RULE_MODE_BRIDGE));
  EXPECT_TRUE(blocked(near, RULE_MODE_CROSS));
}

TEST_F(TransportFilter, PersistenceDeletionAndSeparateModes) {
  install("cross,bridge");
  rules.begin(&fs, nullptr);
  auto p = packet();
  EXPECT_TRUE(blocked(p, RULE_MODE_BRIDGE));
  EXPECT_TRUE(blocked(p, RULE_MODE_CROSS));
  EXPECT_FALSE(blocked(p, RULE_MODE_RADIO));
  install("bridge");
  EXPECT_TRUE(blocked(p, RULE_MODE_BRIDGE));
  EXPECT_FALSE(blocked(p, RULE_MODE_CROSS));
  install("cross");
  EXPECT_FALSE(blocked(p, RULE_MODE_BRIDGE));
  EXPECT_TRUE(blocked(p, RULE_MODE_CROSS));
  command("del flood.rule.3");
  rules.begin(&fs, nullptr);
  EXPECT_FALSE(blocked(p, RULE_MODE_BRIDGE));
  EXPECT_FALSE(blocked(p, RULE_MODE_CROSS));
}

TEST_F(TransportFilter, LegacyFpf6RemainsRadioOnlyAfterUpgrade) {
  // One old-format generic group-data deny, with no optional match/action fields.
  fs.files.clear();
  auto& old = fs.files["/flood_filter"];
  old = {'F', 'P', 'F', '6', 1, 1, PAYLOAD_TYPE_GRP_DATA, 0, 63, 0};
  old.resize(45, 0);  // 32-byte scope name and three legacy boolean selectors
  rules.begin(&fs, nullptr);
  auto p = packet("#wardriving", PAYLOAD_TYPE_GRP_DATA);
  EXPECT_TRUE(blocked(p, RULE_MODE_RADIO));
  EXPECT_FALSE(blocked(p, RULE_MODE_BRIDGE));
  EXPECT_FALSE(blocked(p, RULE_MODE_CROSS));
  install();  // Saving a transport rule upgrades the same table to FPF7.
  rules.begin(&fs, nullptr);
  EXPECT_TRUE(blocked(p, RULE_MODE_RADIO));
  EXPECT_TRUE(blocked(p, RULE_MODE_BRIDGE));
  EXPECT_TRUE(blocked(p, RULE_MODE_CROSS));
  command("get flood.rule.1");
  EXPECT_NE(nullptr, strstr(reply, "mode=radio"));
}

TEST_F(TransportFilter, InvalidModesAndActionsDoNotReplaceSavedRule) {
  install();
  const auto saved = fs.files;
  for (const char* tail : {"mode=bridge,", "mode=all", "mode=radio,cross",
        "mode=bridge mode=cross", "mode=cross retry", "mode=bridge scope=test", "mode=cross tx=slow"}) {
    std::string cmd = "set flood.rule.3 type=any "; cmd += tail; cmd += " drop";
    command(cmd.c_str()); EXPECT_EQ(0, strncmp(reply, "Err", 3)) << reply;
    EXPECT_EQ(saved, fs.files);
  }
  fs.fail_write = true;
  command("set flood.rule.3 type=any mode=radio stop");
  EXPECT_EQ(0, strncmp(reply, "Err", 3));
  auto p = packet();
  EXPECT_TRUE(blocked(p, RULE_MODE_CROSS));
  EXPECT_FALSE(blocked(p, RULE_MODE_RADIO));
}

TEST_F(TransportFilter, CombinedModeSharesRateAndResetsAfterMinute) {
  command("set flood.rule.3 type=any mode=bridge,cross rate=1/min");
  ASSERT_EQ(0, strncmp(reply, "OK", 2)) << reply;
  auto p = packet();
  auto mask = rules.evaluate(&p, false, true, nullptr, RULE_MODE_BRIDGE);
  EXPECT_FALSE(rules.shouldBlock(&p, mask, 100));
  rules.commitRates(&p, mask, 100);
  mask = rules.evaluate(&p, false, true, nullptr, RULE_MODE_CROSS);
  EXPECT_TRUE(rules.shouldBlock(&p, mask, 101));
  EXPECT_FALSE(rules.shouldBlock(&p, mask, 60100));
  mask = rules.evaluate(&p, false, true, nullptr, RULE_MODE_RADIO);
  EXPECT_FALSE(rules.shouldBlock(&p, mask, 101));
}

TEST_F(TransportFilter, ShorthandAndLongCommandsSaveIdenticalRules) {
  struct Pair { const char* full; const char* short_form; };
  for (const auto& pair : {
      Pair{"set flood.rule.3 type=any channel=#wardriving hops=all mode=bridge,cross drop",
           "set fr.3 * c=#wardriving m=bc d"},
      Pair{"set flood.rule.3 type=ota hops=all drop suspend=tempradio",
           "set fr.3 t=12 h=* d f=t"},
      Pair{"set flood.rule.3 type=grp_data hops=2-5 channel=#local in=none scope=test priority=100 stop tx=slow suspend=tempradio",
           "set fr.3 6 h=2-5 c=#local i=n s=test pri=100 s f=st"},
      Pair{"set flood.rule.3 type=any channel=hash:A7 prefix=1234,5678 in=scoped rate=3/min retry stop",
           "set fr.3 any c=hash:A7 p=1234,5678 i=s q=3 r s"}}) {
    command(pair.full); ASSERT_EQ(0, strncmp(reply, "OK", 2)) << reply;
    const auto full = fs.files["/flood_filter"];
    command(pair.short_form); ASSERT_EQ(0, strncmp(reply, "OK", 2)) << reply;
    EXPECT_EQ(full, fs.files["/flood_filter"]);
    // get fr.N emits a complete setter, with every non-default condition/action.
    command("get fr.3"); ASSERT_EQ(0, strncmp(reply, "set fr.3 ", 9)) << reply;
    const std::string paste = reply;
    command("del fr.3"); ASSERT_EQ(0, strncmp(reply, "OK", 2));
    command(paste.c_str()); ASSERT_EQ(0, strncmp(reply, "OK", 2)) << reply;
    EXPECT_EQ(full, fs.files["/flood_filter"]);
  }
}

TEST_F(TransportFilter, AliasesCanMixWithLongCommandsAndKeepStrictActions) {
  install();
  const auto full = fs.files["/flood_filter"];
  for (const char* modes : {"b,c", "c,b", "bc", "cb", "BC", "bridge,cross", "cross,bridge"}) {
    std::string cmd = "set flood.rule.3 t=any c=#wardriving h=all m=";
    cmd += modes; cmd += " d";
    command(cmd.c_str()); ASSERT_EQ(0, strncmp(reply, "OK", 2)) << reply;
    EXPECT_EQ(full, fs.files["/flood_filter"]);
  }
  command("get flood.rule.3");
  EXPECT_NE(nullptr, strstr(reply, "mode=bridge,cross"));
  command("get fr.3");
  EXPECT_STREQ("set fr.3 any m=bc c=#wardriving d", reply);
  auto p = packet();
  EXPECT_TRUE(blocked(p, RULE_MODE_CROSS));
  EXPECT_FALSE(blocked(p, RULE_MODE_RADIO));
  for (const char* tail : {"m=b mode=c d", "h=all hops=0 d", "s stop", "r retry",
       "d drop", "c=#local channel=#other d", "s=test scope=other", "m=radio,c d",
       "m=bc retry", "s=test d", "m=bc", "p=bl d", "r=unknown"}) {
    std::string cmd = "set fr.3 any "; cmd += tail;
    command(cmd.c_str()); EXPECT_EQ(0, strncmp(reply, "Err", 3)) << cmd << ": " << reply;
    EXPECT_EQ(full, fs.files["/flood_filter"]);
  }
}

TEST_F(TransportFilter, ShorthandAddReusesLongRuleAndDeletePersists) {
  install();
  const auto before = fs.files["/flood_filter"];
  command("set fr any c=#wardriving m=bc d");
  ASSERT_EQ(0, strncmp(reply, "OK", 2)) << reply;
  EXPECT_EQ(before, fs.files["/flood_filter"]);
  command("get fr"); EXPECT_NE(nullptr, strstr(reply, "~bridge,cross"));
  command("del fr.3");
  rules.begin(&fs, nullptr);
  command("get fr.3"); EXPECT_EQ(0, strncmp(reply, "Err", 3));
  for (const char* text : {"get fridge", "set frequency 910", "del frx.3", "get frx"})
    EXPECT_FALSE(rules.handleCommand(text, reply));
}

TEST_F(TransportFilter, OversizedCompactReplyNeverReturnsPartialSetter) {
  // A valid short input can fit in 192 command bytes but exceed a 160-byte reply.
  const std::string channel = "#" + std::string(30, 'b'), name(30, 'a');
  std::string cmd = "set fr.3 any 10+ c=" + channel + " p=112233,445566,778899 i=s:" + name
      + " s=" + name + " q=65534 pri=255 s f=str";
  ASSERT_LT(cmd.size() - strlen("set fr.3 "), 192U);
  command(cmd.c_str()); ASSERT_EQ(0, strncmp(reply, "OK", 2)) << reply;
  command("get fr.3");
  EXPECT_STREQ("Err - compact command exceeds reply size", reply);
  EXPECT_EQ(nullptr, strstr(reply, "set fr."));
}

TEST_F(TransportFilter, PrivateKeyReferencesCopyLocallyWithoutDisclosingKey) {
  const std::string key(64, '1');
  command(("set fr.3 any c=" + key + " m=bc d").c_str());
  ASSERT_EQ(0, strncmp(reply, "OK", 2)) << reply;
  const auto saved = fs.files["/flood_filter"];
  command("get fr.3");
  const std::string paste = reply;
  ASSERT_NE(std::string::npos, paste.find("c=key:"));
  EXPECT_EQ(std::string::npos, paste.find(key));
  command(paste.c_str()); ASSERT_EQ(0, strncmp(reply, "OK", 2)) << reply;
  EXPECT_EQ(saved, fs.files["/flood_filter"]);
  std::string copy = paste; copy[7] = '4';  // set fr.4 ...
  command(copy.c_str()); ASSERT_EQ(0, strncmp(reply, "OK", 2)) << reply;
  command("del fr.3");
  command(paste.c_str()); ASSERT_EQ(0, strncmp(reply, "OK", 2)) << reply;
  command("del fr all");
  const auto empty = fs.files["/flood_filter"];
  command(paste.c_str()); EXPECT_EQ(0, strncmp(reply, "Err - unknown", 13)) << reply;
  EXPECT_EQ(empty, fs.files["/flood_filter"]);
}

TEST(TransportModes, KeyFingerprintCollisionsCannotSelectAnArbitrarySecret) {
  struct Row {
    bool active = true;
    uint8_t channel_key_len = 16, channel_hash = 1;
    uint8_t channel_secret[32] = {};
    char channel_name[32] = "key:12345678";
  } rows[2];
  uint8_t key_len = 0, hash = 0, secret[32] = {};
  char name[32] = {};
  EXPECT_TRUE(FloodRuleCLI::copyChannelReference("KEY:12345678", rows, 2,
      key_len, hash, secret, name, sizeof(name)));
  rows[1].channel_secret[0] = 9;
  EXPECT_FALSE(FloodRuleCLI::copyChannelReference("key:12345678", rows, 2,
      key_len, hash, secret, name, sizeof(name)));
  EXPECT_FALSE(FloodRuleCLI::copyChannelReference("key:unknown", rows, 2,
      key_len, hash, secret, name, sizeof(name)));
}
