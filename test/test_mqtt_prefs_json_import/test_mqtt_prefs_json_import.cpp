#include <gtest/gtest.h>

#include <cstring>
#include <string>
#include <vector>

#define WITH_MQTT_BRIDGE 1
#define PROGMEM
#include "helpers/MQTTPrefsAtomicStore.h"
#include "helpers/MQTTPrefsCodec.h"
#include "helpers/MQTTPrefsJsonImport.h"

namespace Import = MQTTPrefsJsonImport;
namespace Atomic = MQTTPrefsAtomicStore;

namespace {

class InputStream : public Stream {
public:
  explicit InputStream(const std::string& text) : _text(text) {}
  int available() override { return static_cast<int>(_text.size() - _pos); }
  int read() override {
    return _pos < _text.size()
        ? static_cast<unsigned char>(_text[_pos++]) : -1;
  }
  int peek() override {
    return _pos < _text.size()
        ? static_cast<unsigned char>(_text[_pos]) : -1;
  }
private:
  std::string _text;
  size_t _pos = 0;
};

bool validPreset(const char* preset) {
  return strcmp(preset, "analyzer-us") == 0 ||
      strcmp(preset, "analyzer-eu") == 0;
}

MQTTPrefs makeDefaults(uint8_t wifi_power_save = 1) {
  MQTTPrefs prefs = {};
  prefs.mqtt_status_enabled = 1;
  prefs.mqtt_packets_enabled = 1;
  prefs.mqtt_tx_enabled = 2;
  prefs.mqtt_rx_enabled = 1;
  prefs.mqtt_status_interval = 300000;
  prefs.wifi_power_save = wifi_power_save;
  prefs.timezone_offset = -7;
  prefs.radio_watchdog_minutes = 5;
  prefs.alert_wifi_minutes = 30;
  prefs.alert_mqtt_minutes = 240;
  prefs.alert_min_interval_min = 60;
  prefs.mqtt_neighbors_interval = MQTT_NEIGHBORS_DEFAULT_INTERVAL_MS;
  prefs.display_timeout_secs = DISPLAY_TIMEOUT_DEFAULT_SECS;
  strcpy(prefs.snmp_community, "public");
  for (int i = 0; i < MQTT_PREFS_SLOT_COUNT; ++i) {
    strcpy(prefs.mqtt_slot_preset[i], "none");
    prefs.mqtt_slot_packet_filter[i] = 0xffff;
  }
  return prefs;
}

Import::Result decodeText(const std::string& text,
                          const MQTTPrefs& defaults,
                          MQTTPrefs* candidate) {
  InputStream stream(text);
  return Import::decode(stream, defaults, candidate, validPreset);
}

class CommitStore {
public:
  explicit CommitStore(bool commit_ok) : _commit_ok(commit_ok) {
    primary = {'o', 'l', 'd'};
  }
  bool begin() {
    staging.clear();
    open = true;
    return true;
  }
  size_t write(const uint8_t* data, size_t size) {
    if (!open) return 0;
    staging.insert(staging.end(), data, data + size);
    return size;
  }
  bool finish() {
    open = false;
    finished = true;
    return true;
  }
  bool commit() {
    if (!_commit_ok) return false;
    primary = staging;
    finished = false;
    return true;
  }
  void abort() {
    open = false;
    if (!finished) staging.clear();
    finished = false;
  }

  std::vector<uint8_t> primary;
  std::vector<uint8_t> staging;
  bool open = false;
  bool finished = false;
private:
  bool _commit_ok;
};

Atomic::Result commitPrefs(CommitStore* store, const MQTTPrefs& prefs) {
  std::vector<uint8_t> encoded(MQTTPrefsCodec::kEncodedSize);
  const size_t encoded_size =
      MQTTPrefsCodec::encode(prefs, encoded.data(), encoded.size());
  EXPECT_GT(encoded_size, sizeof(MQTTPrefsHeader));
  return Atomic::write(
      *store, encoded.data(), sizeof(MQTTPrefsHeader),
      encoded.data() + sizeof(MQTTPrefsHeader),
      encoded_size - sizeof(MQTTPrefsHeader));
}

}  // namespace

TEST(MQTTPrefsJsonImport, BinaryAndRecoveryArtifactsAlwaysTakePrecedence) {
  using D = Import::Decision;
  EXPECT_EQ(D::BinaryAuthoritative,
            Import::select(false, true, false, false, true, false, false));
  EXPECT_EQ(D::BinaryAuthoritative,
            Import::select(true, false, false, false, true, false, false));
  EXPECT_EQ(D::BinaryAuthoritative,
            Import::select(false, false, true, false, true, false, false));
  EXPECT_EQ(D::BinaryAuthoritative,
            Import::select(false, false, false, true, true, false, false));
  EXPECT_EQ(D::BinaryAuthoritative,
            Import::select(false, true, false, false, false, true, true));
}

TEST(MQTTPrefsJsonImport, ImportsOnlyCleanPrimaryAndHoldsJsonArtifacts) {
  using D = Import::Decision;
  EXPECT_EQ(D::ImportPrimary,
            Import::select(false, false, false, false, true, false, false));
  EXPECT_EQ(D::NoJsonSource,
            Import::select(false, false, false, false, false, false, false));
  EXPECT_EQ(D::HoldJsonArtifacts,
            Import::select(false, false, false, false, true, true, false));
  EXPECT_EQ(D::HoldJsonArtifacts,
            Import::select(false, false, false, false, false, false, true));
}

TEST(MQTTPrefsJsonImport, DecodesEveryObserverGroupAndDisplayTail) {
  const MQTTPrefs defaults = makeDefaults();
  MQTTPrefs candidate = defaults;
  const char* text =
      "{version:1,"
      "wifi:{ssid:\"mesh-net\",password:\"p\\\\\\\"ass\\nword\",power_save:2},"
      "time:{timezone:\"MST7MDT\",utc_offset:-6,ntp_server:\"time.example\"},"
      "mqtt:{origin:\"observer-one\",iata:\"sea\",packets_enabled:1,"
      "raw_enabled:1,tx_enabled:1,rx_enabled:0,"
      "status:{enabled:1,interval_ms:60000},"
      "neighbors:{enabled:1,interval_ms:43200000},"
      "owner:{public_key:\"0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef\","
      "email:\"owner@example.com\"},"
      "slot1:{preset:\"analyzer-us\",host:\"broker.example\",port:1883,"
      "username:\"user\",password:\"secret\",token:\"token\","
      "topic:\"mesh/{iata}\",audience:\"aud\",packet_filter:32769},"
      "slot6:{preset:\"custom\",host:\"six.example\",port:65535,"
      "username:\"u6\",password:\"p6\",token:\"t6\",topic:\"six\","
      "audience:\"a6\",packet_filter:0}},"
      "snmp:{enabled:1,community:\"private\"},radio:{watchdog_min:120},"
      "alert:{enabled:1,psk_hex:\"0123456789abcdef0123456789abcdef\","
      "wifi_minutes:45,mqtt_minutes:300,rate_limit_min:90,"
      "hashtag:\"#ops\",region:\"PNW\"},"
      "display:{timeout_s:45,flip:1}}";

  EXPECT_EQ(Import::Result::LoadedWithRepairs,
            decodeText(text, defaults, &candidate));
  EXPECT_STREQ("mesh-net", candidate.wifi_ssid);
  EXPECT_STREQ("p\\\"ass\nword", candidate.wifi_password);
  EXPECT_EQ(2, candidate.wifi_power_save);
  EXPECT_EQ(-6, candidate.timezone_offset);
  EXPECT_STREQ("time.example", candidate.mqtt_ntp_server);
  EXPECT_STREQ("observer-one", candidate.mqtt_origin);
  EXPECT_STREQ("SEA", candidate.mqtt_iata);
  EXPECT_EQ(1, candidate.mqtt_raw_enabled);
  EXPECT_EQ(0, candidate.mqtt_rx_enabled);
  EXPECT_EQ(60000u, candidate.mqtt_status_interval);
  EXPECT_EQ(MQTT_NEIGHBORS_MIN_INTERVAL_MS,
            candidate.mqtt_neighbors_interval);
  EXPECT_STREQ("analyzer-us", candidate.mqtt_slot_preset[0]);
  EXPECT_EQ(1883, candidate.mqtt_slot_port[0]);
  EXPECT_EQ(32769, candidate.mqtt_slot_packet_filter[0]);
  EXPECT_STREQ("custom", candidate.mqtt_slot_preset[5]);
  EXPECT_EQ(65535, candidate.mqtt_slot_port[5]);
  EXPECT_EQ(0, candidate.mqtt_slot_packet_filter[5]);
  EXPECT_EQ(1, candidate.snmp_enabled);
  EXPECT_STREQ("private", candidate.snmp_community);
  EXPECT_EQ(120, candidate.radio_watchdog_minutes);
  EXPECT_EQ(1, candidate.alert_enabled);
  EXPECT_STREQ("#ops", candidate.alert_hashtag);
  EXPECT_EQ(45, candidate.display_timeout_secs);
  EXPECT_EQ(1, candidate.display_flip);
}

TEST(MQTTPrefsJsonImport, MissingFieldsRetainCurrentBuildDefaults) {
  MQTTPrefs defaults = makeDefaults(0);
  strcpy(defaults.mqtt_iata, "PDX");
  defaults.display_timeout_secs = 60;
  MQTTPrefs candidate = defaults;
  EXPECT_EQ(Import::Result::Loaded,
            decodeText("{version:1,mqtt:{origin:\"changed\"},display:{}}",
                       defaults, &candidate));
  EXPECT_STREQ("changed", candidate.mqtt_origin);
  EXPECT_STREQ("PDX", candidate.mqtt_iata);
  EXPECT_EQ(0, candidate.wifi_power_save);
  EXPECT_EQ(300000u, candidate.mqtt_status_interval);
  EXPECT_EQ(60, candidate.display_timeout_secs);
}

TEST(MQTTPrefsJsonImport, SemanticRepairsUseSchemaAndCurrentWifiDefaults) {
  MQTTPrefs defaults = makeDefaults(0);
  MQTTPrefs candidate = defaults;
  const char* text =
      "{version:1,wifi:{power_save:9},time:{utc_offset:99,ntp_server:\"bad/host\"},"
      "mqtt:{iata:\"bad!\",packets_enabled:-1,raw_enabled:2,tx_enabled:9,"
      "rx_enabled:2,status:{enabled:3,interval_ms:10},"
      "neighbors:{enabled:2,interval_ms:100},"
      "slot1:{preset:\"bogus\",port:-1,packet_filter:70000}},"
      "snmp:{enabled:2},radio:{watchdog_min:121},"
      "alert:{enabled:3,psk_hex:\"bad\",wifi_minutes:-1,mqtt_minutes:10081,"
      "rate_limit_min:1,hashtag:\"#stale\"},"
      "display:{timeout_s:99999,flip:7}}";
  ASSERT_EQ(Import::Result::LoadedWithRepairs,
            decodeText(text, defaults, &candidate));
  EXPECT_EQ(0, candidate.wifi_power_save);
  EXPECT_EQ(-7, candidate.timezone_offset);
  EXPECT_STREQ("", candidate.mqtt_ntp_server);
  EXPECT_STREQ("", candidate.mqtt_iata);
  EXPECT_EQ(1, candidate.mqtt_packets_enabled);
  EXPECT_EQ(0, candidate.mqtt_raw_enabled);
  EXPECT_EQ(2, candidate.mqtt_tx_enabled);
  EXPECT_EQ(1, candidate.mqtt_rx_enabled);
  EXPECT_EQ(300000u, candidate.mqtt_status_interval);
  EXPECT_EQ(MQTT_NEIGHBORS_DEFAULT_INTERVAL_MS,
            candidate.mqtt_neighbors_interval);
  EXPECT_STREQ("none", candidate.mqtt_slot_preset[0]);
  EXPECT_EQ(0, candidate.mqtt_slot_port[0]);
  EXPECT_EQ(0xffff, candidate.mqtt_slot_packet_filter[0]);
  EXPECT_EQ(0, candidate.snmp_enabled);
  EXPECT_EQ(5, candidate.radio_watchdog_minutes);
  EXPECT_EQ(0, candidate.alert_enabled);
  EXPECT_STREQ("", candidate.alert_psk_hex);
  EXPECT_STREQ("", candidate.alert_hashtag);
  EXPECT_EQ(DISPLAY_TIMEOUT_DEFAULT_SECS, candidate.display_timeout_secs);
  EXPECT_EQ(0, candidate.display_flip);
}

TEST(MQTTPrefsJsonImport, RejectsKnownTypeDuplicateLengthAndOverflowErrors) {
  const MQTTPrefs defaults = makeDefaults();
  for (const char* text : {
           "{version:\"1\"}",
           "{version:true}",
           "{version:1.0}",
           "{version:1,version:1}",
           "{version:1,wifi:{ssid:\"one\"},wifi:{password:\"two\"}}",
           "{version:1,wifi:{ssid:meshnet}}",
           "{version:1,wifi:{ssid:\"12345678901234567890123456789012\"}}",
           "{version:1,mqtt:{slot1:{port:999999999999}}}",
           "{version:{x:1}}",
           "{version:1,mqtt:1}",
           "{version:1,mqtt:{slot1:1}}",
           "{version:1,mqtt:{status:{enabled:1},status:{interval_ms:60000}}}"}) {
    MQTTPrefs candidate = defaults;
    EXPECT_FALSE(Import::loaded(decodeText(text, defaults, &candidate))) << text;
  }

  std::string embedded_nul = "{version:1,wifi:{ssid:\"before";
  embedded_nul.push_back('\0');
  embedded_nul += "after\"}}";
  MQTTPrefs candidate = defaults;
  EXPECT_EQ(Import::Result::InvalidSyntax,
            decodeText(embedded_nul, defaults, &candidate));
}

TEST(MQTTPrefsJsonImport, RejectsTrailingMaterialAndMissingObjectSeparators) {
  const MQTTPrefs defaults = makeDefaults();
  for (const char* text : {
           "{version:1}garbage",
           "{version:1},",
           "{version:1,}",
           "{version:1,wifi:{ssid:\"x\",}}",
           "{version:1,wifi:{} mqtt:{}}"}) {
    MQTTPrefs candidate = defaults;
    EXPECT_EQ(Import::Result::InvalidSyntax,
              decodeText(text, defaults, &candidate)) << text;
  }

  MQTTPrefs candidate = defaults;
  EXPECT_EQ(Import::Result::Loaded,
            decodeText("{version:1,wifi:{}} \r\n\t", defaults, &candidate));
}

TEST(MQTTPrefsJsonImport, AcceptsSafelyParseableAppendOnlyUnknownFields) {
  const MQTTPrefs defaults = makeDefaults();
  MQTTPrefs candidate = defaults;
  EXPECT_EQ(
      Import::Result::Loaded,
      decodeText(
          "{version:1,display:{timeout_s:45},future:{thing:1,nested:{x:2}}}",
          defaults, &candidate));
  EXPECT_EQ(45, candidate.display_timeout_secs);
}

TEST(MQTTPrefsJsonImport, FutureVersionIsOpaqueEvenWithFutureGrammar) {
  const MQTTPrefs defaults = makeDefaults();
  MQTTPrefs candidate = defaults;
  EXPECT_EQ(Import::Result::UnsupportedVersion,
            decodeText("{version:2,future:[1]}", defaults, &candidate));
  candidate = defaults;
  EXPECT_EQ(Import::Result::InvalidSchema,
            decodeText("{version:0}", defaults, &candidate));
  candidate = defaults;
  EXPECT_EQ(Import::Result::InvalidSchema,
            decodeText("{wifi:{ssid:\"x\"}}", defaults, &candidate));
}

TEST(MQTTPrefsJsonImport, ParseFailureNeverTouchesLivePreferences) {
  const MQTTPrefs defaults = makeDefaults();
  MQTTPrefs live = defaults;
  strcpy(live.wifi_ssid, "live-network");
  MQTTPrefs scratch = defaults;
  EXPECT_EQ(Import::Result::InvalidSyntax,
            decodeText("{version:1,wifi:{ssid:\"uncommitted\"}",
                       defaults, &scratch));
  EXPECT_STREQ("live-network", live.wifi_ssid);
}

TEST(MQTTPrefsJsonImport, ParsedDisplayPrefsRouteThroughFullAtomicBinaryCommit) {
  const MQTTPrefs defaults = makeDefaults();
  MQTTPrefs candidate = defaults;
  ASSERT_EQ(Import::Result::Loaded,
            decodeText("{version:1,display:{timeout_s:45,flip:1}}",
                       defaults, &candidate));
  EXPECT_EQ(MQTT_PREFS_V1_FULL_PAYLOAD_SIZE,
            MQTTPrefsCodec::payloadLenFor(candidate));

  CommitStore store(true);
  ASSERT_EQ(Atomic::Result::Committed, commitPrefs(&store, candidate));
  ASSERT_EQ(sizeof(MQTTPrefsHeader) + MQTT_PREFS_V1_FULL_PAYLOAD_SIZE,
            store.primary.size());
  MQTTPrefsHeader header = {};
  memcpy(&header, store.primary.data(), sizeof(header));
  EXPECT_EQ(MQTT_PREFS_V1_FULL_PAYLOAD_SIZE, header.payload_len);
}

TEST(MQTTPrefsJsonImport, FailedAtomicPublishPreservesPreviousBinaryImage) {
  const MQTTPrefs defaults = makeDefaults();
  MQTTPrefs candidate = defaults;
  ASSERT_EQ(Import::Result::Loaded,
            decodeText("{version:1,wifi:{ssid:\"new\"}}",
                       defaults, &candidate));

  CommitStore store(false);
  const std::vector<uint8_t> old_primary = store.primary;
  EXPECT_EQ(Atomic::Result::CommitFailed, commitPrefs(&store, candidate));
  EXPECT_EQ(old_primary, store.primary);
  EXPECT_FALSE(store.staging.empty());
}

int main(int argc, char** argv) {
  ::testing::InitGoogleTest(&argc, argv);
  return RUN_ALL_TESTS();
}
