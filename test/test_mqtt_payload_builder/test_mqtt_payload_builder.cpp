#include <gtest/gtest.h>

#include <ArduinoJson.h>
#include <cmath>
#include <cstdlib>
#include <cstring>
#include <string>
#include <vector>

#include "helpers/MQTTPayloadBuilder.h"
#include "helpers/TxtDataHelpers.h"

namespace {

constexpr const char* kTimestamp = "2026-07-18T12:34:56.123456+00:00";

struct RejectAllJsonAllocations : ArduinoJson::Allocator {
  void* allocate(size_t) override { return nullptr; }
  void deallocate(void*) override {}
  void* reallocate(void*, size_t) override { return nullptr; }
};

struct FailAfterJsonAllocations : ArduinoJson::Allocator {
  size_t remaining;
  size_t denied = 0;
  explicit FailAfterJsonAllocations(size_t allowed) : remaining(allowed) {}
  void* allocate(size_t size) override {
    if (!remaining) { ++denied; return nullptr; }
    --remaining;
    return std::malloc(size);
  }
  void deallocate(void* pointer) override { std::free(pointer); }
  void* reallocate(void* pointer, size_t size) override {
    if (!remaining) { ++denied; return nullptr; }
    --remaining;
    return std::realloc(pointer, size);
  }
};

static int buildMinimalStatus(JsonDocument& scratch, char* buffer, size_t buffer_size) {
  return MQTTPayloadBuilder::buildStatusMessage(
      scratch, "DEN Repeater", "0123456789ABCDEF", "Heltec V3", "v1.16.0",
      "915.000000,62.5,7,5", "MeshCore", "online", kTimestamp,
      buffer, buffer_size);
}

static int buildRepresentativePacket(JsonDocument& scratch, const char* direction,
                                     float score, const uint8_t* path, int path_hops,
                                     int path_hash_size, const char* raw,
                                     char* buffer, size_t buffer_size) {
  return MQTTPayloadBuilder::buildPacketMessage(
      scratch, "DEN Repeater", "0123456789ABCDEF", kTimestamp, direction,
      "12:34:56", "18/07/2026", 42, 4, "D", 20, raw,
      10.26f, -87, score, "89ABCDEF01234567", path, path_hops,
      path_hash_size, 64, buffer, buffer_size);
}

static int buildMinimalPacket(JsonDocument& doc, char* buffer, size_t capacity) {
  return buildRepresentativePacket(doc, "rx", 0.125f, nullptr, 0, 0, "AABB", buffer, capacity);
}

static int buildMinimalRaw(JsonDocument& doc, char* buffer, size_t capacity) {
  return MQTTPayloadBuilder::buildRawMessage(doc, "node", "id", kTimestamp, "AABB", buffer, capacity);
}

TEST(MQTTPayloadBuilder, StatusPacketAndRawRejectAllAllocationFailures) {
  for (auto build : {buildMinimalStatus, buildMinimalPacket, buildMinimalRaw}) {
    RejectAllJsonAllocations allocator;
    JsonDocument doc(&allocator);
    char buffer[2048];
    memset(buffer, 'x', sizeof(buffer));
    EXPECT_EQ(0, build(doc, buffer, sizeof(buffer)));
    EXPECT_TRUE(doc.overflowed());
    EXPECT_EQ('\0', buffer[0]);
  }
}

TEST(MQTTPayloadBuilder, StatusPacketAndRawRejectPartiallyPopulatedDocuments) {
  for (auto build : {buildMinimalStatus, buildMinimalPacket, buildMinimalRaw}) {
    bool found_partial_failure = false;
    for (size_t allowed = 1; allowed < 32; ++allowed) {
      FailAfterJsonAllocations allocator(allowed);
      JsonDocument doc(&allocator);
      char buffer[2048];
      memset(buffer, 'x', sizeof(buffer));
      const int result = build(doc, buffer, sizeof(buffer));
      if (allocator.denied && doc.size() > 0) {
        found_partial_failure = true;
        EXPECT_TRUE(doc.overflowed());
        EXPECT_EQ(0, result);
        EXPECT_EQ('\0', buffer[0]);
        // A document is reusable after memory becomes available again.
        allocator.remaining = 64;
        EXPECT_GT(build(doc, buffer, sizeof(buffer)), 0);
        EXPECT_FALSE(doc.overflowed());
        break;
      }
    }
    EXPECT_TRUE(found_partial_failure);
  }
}

TEST(MQTTPayloadBuilder, MinimalStatusHasExactRequiredContract) {
  JsonDocument scratch;
  char buffer[768];
  int len = buildMinimalStatus(scratch, buffer, sizeof(buffer));

  ASSERT_GT(len, 0);
  EXPECT_EQ(static_cast<size_t>(len), strlen(buffer));
  EXPECT_STREQ(
      "{\"status\":\"online\",\"timestamp\":\"2026-07-18T12:34:56.123456+00:00\","
      "\"origin\":\"DEN Repeater\",\"origin_id\":\"0123456789ABCDEF\","
      "\"model\":\"Heltec V3\",\"firmware_version\":\"v1.16.0\","
      "\"radio\":\"915.000000,62.5,7,5\",\"client_version\":\"MeshCore\"}",
      buffer);

  JsonDocument parsed;
  ASSERT_FALSE(deserializeJson(parsed, buffer));
  EXPECT_FALSE(parsed["repeat"].is<JsonVariant>());
  EXPECT_FALSE(parsed["stats"].is<JsonVariant>());
}

TEST(MQTTPayloadBuilder, StatusIncludesRepeatAndEveryRequestedStatistic) {
  JsonDocument scratch;
  char buffer[1024];
  int len = MQTTPayloadBuilder::buildStatusMessage(
      scratch, "node", "id", "model", "firmware", "radio", "client", "online",
      kTimestamp, buffer, sizeof(buffer), 4200, 86400, 3, 6, -112,
      11, 22, 4, 180864, 31, 47, "on");

  ASSERT_GT(len, 0);
  JsonDocument parsed;
  ASSERT_FALSE(deserializeJson(parsed, buffer));
  EXPECT_STREQ("on", parsed["repeat"].as<const char*>());
  JsonObject stats = parsed["stats"].as<JsonObject>();
  ASSERT_FALSE(stats.isNull());
  EXPECT_EQ(4200, stats["battery_mv"].as<int>());
  EXPECT_EQ(86400, stats["uptime_secs"].as<int>());
  EXPECT_EQ(3, stats["errors"].as<int>());
  EXPECT_EQ(6, stats["queue_len"].as<int>());
  EXPECT_EQ(-112, stats["noise_floor"].as<int>());
  EXPECT_EQ(11, stats["tx_air_secs"].as<int>());
  EXPECT_EQ(22, stats["rx_air_secs"].as<int>());
  EXPECT_EQ(4, stats["recv_errors"].as<int>());
  EXPECT_EQ(180864, stats["internal_heap"].as<int>());
  EXPECT_EQ(31, stats["packets_sent"].as<int>());
  EXPECT_EQ(47, stats["packets_received"].as<int>());
}

TEST(MQTTPayloadBuilder, StatusOmissionSentinelsRemainOmitted) {
  JsonDocument scratch;
  char buffer[768];
  int len = MQTTPayloadBuilder::buildStatusMessage(
      scratch, "node", "id", "model", "firmware", "radio", "client", "online",
      kTimestamp, buffer, sizeof(buffer), -1, -1, -1, -1, -999,
      -1, -1, -1, -1, -1, -1, nullptr);

  ASSERT_GT(len, 0);
  JsonDocument parsed;
  ASSERT_FALSE(deserializeJson(parsed, buffer));
  EXPECT_FALSE(parsed["stats"].is<JsonVariant>());
  EXPECT_FALSE(parsed["repeat"].is<JsonVariant>());
}

TEST(MQTTPayloadBuilder, StatusIncludesAllConnectionHealthFields) {
  JsonDocument scratch;
  char buffer[1024];
  MQTTConnHealth health;
  health.slots_up = 2;
  health.slots_total = 3;
  health.worst_outage_secs = 18432;
  health.heap_largest = 16116;
  health.connect_failures = 57;
  health.slots_breaker = 1;
  ASSERT_GT(MQTTPayloadBuilder::buildStatusMessage(
      scratch, "node", "id", "model", "firmware", "radio", "client", "online",
      kTimestamp, buffer, sizeof(buffer), -1, -1, -1, -1, -999,
      -1, -1, -1, -1, -1, -1, nullptr, health), 0);
  JsonDocument parsed;
  ASSERT_FALSE(deserializeJson(parsed, buffer));
  JsonObject stats = parsed["stats"].as<JsonObject>();
  EXPECT_EQ(2, stats["mqtt_slots_up"].as<int>());
  EXPECT_EQ(3, stats["mqtt_slots_total"].as<int>());
  EXPECT_EQ(18432, stats["mqtt_outage_secs"].as<int>());
  EXPECT_EQ(16116, stats["heap_largest"].as<int>());
  EXPECT_EQ(57, stats["mqtt_connect_failures"].as<int>());
  EXPECT_EQ(1, stats["mqtt_slots_breaker"].as<int>());
}

TEST(MQTTPayloadBuilder, HealthyStatusOmitsOutageAndZeroFailureKeys) {
  JsonDocument scratch;
  char buffer[1024];
  MQTTConnHealth health;
  health.slots_up = 3;
  health.slots_total = 3;
  health.heap_largest = 54260;
  health.connect_failures = 0;
  health.slots_breaker = 0;
  ASSERT_GT(MQTTPayloadBuilder::buildStatusMessage(
      scratch, "node", "id", "model", "firmware", "radio", "client", "online",
      kTimestamp, buffer, sizeof(buffer), -1, -1, -1, -1, -999,
      -1, -1, -1, -1, -1, -1, nullptr, health), 0);
  JsonDocument parsed;
  ASSERT_FALSE(deserializeJson(parsed, buffer));
  JsonObject stats = parsed["stats"].as<JsonObject>();
  EXPECT_EQ(3, stats["mqtt_slots_up"].as<int>());
  EXPECT_FALSE(stats["mqtt_outage_secs"].is<JsonVariant>());
  EXPECT_FALSE(stats["mqtt_connect_failures"].is<JsonVariant>());
  EXPECT_FALSE(stats["mqtt_slots_breaker"].is<JsonVariant>());
}

TEST(MQTTPayloadBuilder, FailedStartsAloneCreateHealthStats) {
  JsonDocument scratch;
  char buffer[1024];
  MQTTConnHealth health;
  health.connect_failures = 1;
  ASSERT_GT(MQTTPayloadBuilder::buildStatusMessage(
      scratch, "node", "id", "model", "firmware", "radio", "client", "online",
      kTimestamp, buffer, sizeof(buffer), -1, -1, -1, -1, -999,
      -1, -1, -1, -1, -1, -1, nullptr, health), 0);
  JsonDocument parsed;
  ASSERT_FALSE(deserializeJson(parsed, buffer));
  EXPECT_EQ(1, parsed["stats"]["mqtt_connect_failures"].as<int>());
  EXPECT_FALSE(parsed["stats"]["mqtt_slots_up"].is<JsonVariant>());
}

TEST(MQTTPayloadBuilder, WorstCaseStatusWithHealthFitsProductionCeiling) {
  // Actual bridge field maxima, including legal quote characters doubled by JSON.
  const std::string origin(31, '"'), origin_id(64, 'A'), model(63, 'M');
  const std::string firmware(63, 'F'), client(63, 'C');
  JsonDocument scratch;
  char buffer[1024];
  MQTTConnHealth health;
  health.slots_up = 0;
  health.slots_total = 5;
  health.worst_outage_secs = 4294967;
  health.heap_largest = 8388608;
  health.connect_failures = INT32_MAX;
  health.slots_breaker = 5;
  const int len = MQTTPayloadBuilder::buildStatusMessage(
      scratch, origin.c_str(), origin_id.c_str(), model.c_str(), firmware.c_str(),
      "9999.999999,1000.0,12,8", client.c_str(), "online", kTimestamp,
      buffer, sizeof(buffer), 4200, 2147483, INT32_MAX, 6, -128,
      2147483, 2147483, INT32_MAX, 8388608, INT32_MAX, INT32_MAX, "off", health);
  ASSERT_GT(len, 0);
  EXPECT_LT(len, 1024 - 32);
  JsonDocument parsed;
  ASSERT_FALSE(deserializeJson(parsed, buffer));
  EXPECT_EQ(INT32_MAX, parsed["stats"]["mqtt_connect_failures"].as<int>());
}

TEST(MQTTPayloadBuilder, StringsAreEscapedAndRoundTrip) {
  const char* origin = "node \"north\"\\rack\nline";
  const char* model = "Heltec\tV3";
  JsonDocument scratch;
  char buffer[1024];
  int len = MQTTPayloadBuilder::buildStatusMessage(
      scratch, origin, "id", model, "v1", "radio", "client", "online",
      kTimestamp, buffer, sizeof(buffer));

  ASSERT_GT(len, 0);
  EXPECT_NE(std::string::npos, std::string(buffer).find("\\\"north\\\""));
  EXPECT_NE(std::string::npos, std::string(buffer).find("\\\\rack\\nline"));
  JsonDocument parsed;
  ASSERT_FALSE(deserializeJson(parsed, buffer));
  EXPECT_STREQ(origin, parsed["origin"].as<const char*>());
  EXPECT_STREQ(model, parsed["model"].as<const char*>());
}

TEST(MQTTPayloadBuilder, RxPacketIncludesMetricsScaledScoreAndPath) {
  const uint8_t path[] = {0xAA, 0xBB, 0x01, 0x2F};
  JsonDocument scratch;
  char buffer[2048];
  int len = buildRepresentativePacket(
      scratch, "rx", 0.125f, path, 2, 2, "A0B1C2D3", buffer, sizeof(buffer));

  ASSERT_GT(len, 0);
  JsonDocument parsed;
  ASSERT_FALSE(deserializeJson(parsed, buffer));
  EXPECT_STREQ("PACKET", parsed["type"].as<const char*>());
  EXPECT_STREQ("rx", parsed["direction"].as<const char*>());
  EXPECT_STREQ("42", parsed["len"].as<const char*>());
  EXPECT_STREQ("4", parsed["packet_type"].as<const char*>());
  EXPECT_STREQ("20", parsed["payload_len"].as<const char*>());
  EXPECT_STREQ("10.3", parsed["SNR"].as<const char*>());
  EXPECT_STREQ("-87", parsed["RSSI"].as<const char*>());
  EXPECT_STREQ("125", parsed["score"].as<const char*>());
  JsonArray parsed_path = parsed["path"].as<JsonArray>();
  ASSERT_EQ(2U, parsed_path.size());
  EXPECT_STREQ("aabb", parsed_path[0].as<const char*>());
  EXPECT_STREQ("012f", parsed_path[1].as<const char*>());
}

TEST(MQTTPayloadBuilder, FixedFloatFormattingDoesNotRequirePrintfFloat) {
  char value[20];

  EXPECT_TRUE(StrHelper::ftoaFixed(value, sizeof(value), 915.0f, 6));
  EXPECT_STREQ("915.000000", value);
  EXPECT_TRUE(StrHelper::ftoaFixed(value, sizeof(value), 10.26f, 1));
  EXPECT_STREQ("10.3", value);
  EXPECT_TRUE(StrHelper::ftoaFixed(value, sizeof(value), -87.25f, 2));
  EXPECT_STREQ("-87.25", value);
  EXPECT_FALSE(StrHelper::ftoaFixed(value, sizeof(value), std::nanf(""), 1));
  EXPECT_STREQ("0", value);
}

TEST(MQTTPayloadBuilder, TxPacketOmitsReceiveOnlyMetricsAndAbsentPath) {
  JsonDocument scratch;
  char buffer[2048];
  int len = buildRepresentativePacket(
      scratch, "tx", 0.5f, nullptr, 0, 0, "A0B1", buffer, sizeof(buffer));

  ASSERT_GT(len, 0);
  JsonDocument parsed;
  ASSERT_FALSE(deserializeJson(parsed, buffer));
  EXPECT_FALSE(parsed["SNR"].is<JsonVariant>());
  EXPECT_FALSE(parsed["RSSI"].is<JsonVariant>());
  EXPECT_FALSE(parsed["score"].is<JsonVariant>());
  EXPECT_FALSE(parsed["path"].is<JsonVariant>());
}

TEST(MQTTPayloadBuilder, RxPacketOmitsUnknownNanScore) {
  JsonDocument scratch;
  char buffer[2048];
  int len = buildRepresentativePacket(
      scratch, "rx", std::nanf(""), nullptr, 0, 0, "A0B1", buffer, sizeof(buffer));

  ASSERT_GT(len, 0);
  JsonDocument parsed;
  ASSERT_FALSE(deserializeJson(parsed, buffer));
  EXPECT_TRUE(parsed["SNR"].is<const char*>());
  EXPECT_TRUE(parsed["RSSI"].is<const char*>());
  EXPECT_FALSE(parsed["score"].is<JsonVariant>());
}

TEST(MQTTPayloadBuilder, RxPacketOmitsUnknownRadioMetrics) {
  JsonDocument scratch;
  char buffer[2048];
  int len = MQTTPayloadBuilder::buildPacketMessage(
      scratch, "node", "id", kTimestamp, "rx", "12:34:56", "18/07/2026",
      42, 4, "D", 20, "A0B1", -999.0f, -999, std::nanf(""), "hash",
      nullptr, 0, 0, 64, buffer, sizeof(buffer));

  ASSERT_GT(len, 0);
  JsonDocument parsed;
  ASSERT_FALSE(deserializeJson(parsed, buffer));
  EXPECT_FALSE(parsed["SNR"].is<JsonVariant>());
  EXPECT_FALSE(parsed["RSSI"].is<JsonVariant>());
  EXPECT_FALSE(parsed["score"].is<JsonVariant>());
}

TEST(MQTTPayloadBuilder, RxPacketOmitsOnlyTheUnknownRadioMetric) {
  JsonDocument scratch;
  char buffer[2048];
  ASSERT_GT(MQTTPayloadBuilder::buildPacketMessage(
      scratch, "node", "id", kTimestamp, "rx", "12:34:56", "18/07/2026",
      42, 4, "D", 20, "A0B1", std::nanf(""), -93, std::nanf(""), "hash",
      nullptr, 0, 0, 64, buffer, sizeof(buffer)), 0);
  JsonDocument parsed;
  ASSERT_FALSE(deserializeJson(parsed, buffer));
  EXPECT_FALSE(parsed["SNR"].is<JsonVariant>());
  EXPECT_STREQ("-93", parsed["RSSI"].as<const char*>());
  ASSERT_GT(MQTTPayloadBuilder::buildPacketMessage(
      scratch, "node", "id", kTimestamp, "rx", "12:34:56", "18/07/2026",
      42, 4, "D", 20, "A0B1", 7.2f, -999, std::nanf(""), "hash",
      nullptr, 0, 0, 64, buffer, sizeof(buffer)), 0);
  parsed.clear();
  ASSERT_FALSE(deserializeJson(parsed, buffer));
  EXPECT_STREQ("7.2", parsed["SNR"].as<const char*>());
  EXPECT_FALSE(parsed["RSSI"].is<JsonVariant>());
}

TEST(MQTTPayloadBuilder, RawMessageHasExactContractAndEscapesData) {
  char buffer[512];
  JsonDocument doc;
  int len = MQTTPayloadBuilder::buildRawMessage(
      doc, "node \"A\"", "id\\1", kTimestamp, "AA\nBB", buffer, sizeof(buffer));

  ASSERT_GT(len, 0);
  EXPECT_EQ(static_cast<size_t>(len), strlen(buffer));
  EXPECT_STREQ(
      "{\"origin\":\"node \\\"A\\\"\",\"origin_id\":\"id\\\\1\","
      "\"timestamp\":\"2026-07-18T12:34:56.123456+00:00\","
      "\"type\":\"RAW\",\"data\":\"AA\\nBB\"}",
      buffer);
  JsonDocument parsed;
  ASSERT_FALSE(deserializeJson(parsed, buffer));
  EXPECT_STREQ("AA\nBB", parsed["data"].as<const char*>());
}

TEST(MQTTPayloadBuilder, ExactOutputSizeSucceedsAndOneByteShortFailsCleanly) {
  JsonDocument scratch;
  char reference[768];
  int reference_len = buildMinimalStatus(scratch, reference, sizeof(reference));
  ASSERT_GT(reference_len, 0);

  std::vector<char> exact(static_cast<size_t>(reference_len) + 1);
  EXPECT_EQ(reference_len, buildMinimalStatus(scratch, exact.data(), exact.size()));
  EXPECT_STREQ(reference, exact.data());

  std::vector<char> short_buffer(static_cast<size_t>(reference_len), 'x');
  EXPECT_EQ(0, buildMinimalStatus(scratch, short_buffer.data(), short_buffer.size()));
  EXPECT_EQ('\0', short_buffer[0]);
  EXPECT_EQ(0, buildMinimalStatus(scratch, short_buffer.data(), 1));
  EXPECT_EQ('\0', short_buffer[0]);
  EXPECT_EQ(0, buildMinimalStatus(scratch, nullptr, exact.size()));
}

TEST(MQTTPayloadBuilder, MaximumRepresentativePacketAndRawPayloadsRemainValid) {
  uint8_t path[64];
  for (size_t i = 0; i < sizeof(path); ++i) path[i] = static_cast<uint8_t>(i);
  std::string raw(510, 'A');

  JsonDocument scratch;
  char packet_buffer[2048];
  int packet_len = buildRepresentativePacket(
      scratch, "rx", 1.0f, path, 16, 4, raw.c_str(),
      packet_buffer, sizeof(packet_buffer));
  ASSERT_GT(packet_len, 0);
  JsonDocument packet;
  ASSERT_FALSE(deserializeJson(packet, packet_buffer));
  EXPECT_EQ(510U, strlen(packet["raw"].as<const char*>()));
  JsonArray parsed_path = packet["path"].as<JsonArray>();
  ASSERT_EQ(16U, parsed_path.size());
  EXPECT_STREQ("00010203", parsed_path[0].as<const char*>());
  EXPECT_STREQ("3c3d3e3f", parsed_path[15].as<const char*>());

  char raw_buffer[1024];
  JsonDocument raw_doc;
  int raw_len = MQTTPayloadBuilder::buildRawMessage(
      raw_doc, "node", "0123456789ABCDEF", kTimestamp, raw.c_str(),
      raw_buffer, sizeof(raw_buffer));
  ASSERT_GT(raw_len, 0);
  JsonDocument parsed_raw;
  ASSERT_FALSE(deserializeJson(parsed_raw, raw_buffer));
  EXPECT_EQ(510U, strlen(parsed_raw["data"].as<const char*>()));
}

TEST(MQTTPayloadBuilder, NeighborsMessageRoundTripsSelfAndEntries) {
  MQTTPayloadBuilder::NeighborsMessageEntry neighbors[] = {
      {"0011223344556677", 9.75f, 42, "DEN,APRS", "active"},
      {"8899AABBCCDDEEFF", -3.5f, 3600, "", "stale"},
  };
  neighbors[0].rssi = -87;
  neighbors[1].rssi = -110;

  JsonDocument scratch;
  char buffer[1024];
  int len = MQTTPayloadBuilder::buildNeighborsMessage(
      scratch, "DEN Repeater", "0123456789ABCDEF", kTimestamp, "DEN,APRS", "*",
      neighbors, 2, buffer, sizeof(buffer), 5, 2, true);

  ASSERT_GT(len, 0);
  EXPECT_EQ(static_cast<size_t>(len), strlen(buffer));
  JsonDocument parsed;
  ASSERT_FALSE(deserializeJson(parsed, buffer));
  EXPECT_STREQ("2026-07-18T12:34:56.123456+00:00", parsed["timestamp"].as<const char*>());
  EXPECT_STREQ("DEN Repeater", parsed["origin"].as<const char*>());
  EXPECT_STREQ("0123456789ABCDEF", parsed["origin_id"].as<const char*>());
  EXPECT_STREQ("DEN,APRS", parsed["self"]["scopes"].as<const char*>());
  EXPECT_STREQ("*", parsed["self"]["default_scope"].as<const char*>());
  EXPECT_EQ(5, parsed["total_neighbors"].as<int>());
  EXPECT_EQ(2, parsed["queried_neighbors"].as<int>());
  EXPECT_TRUE(parsed["truncated"].as<bool>());

  JsonArray arr = parsed["neighbors"].as<JsonArray>();
  ASSERT_EQ(2U, arr.size());
  EXPECT_STREQ("0011223344556677", arr[0]["pubkey"].as<const char*>());
  EXPECT_FLOAT_EQ(9.75f, arr[0]["snr"].as<float>());
  EXPECT_EQ(-87, arr[0]["rssi"].as<int>());
  EXPECT_EQ(42U, arr[0]["heard_secs_ago"].as<uint32_t>());
  EXPECT_STREQ("DEN,APRS", arr[0]["scopes"].as<const char*>());
  EXPECT_STREQ("active", arr[0]["status"].as<const char*>());
  EXPECT_STREQ("8899AABBCCDDEEFF", arr[1]["pubkey"].as<const char*>());
  EXPECT_EQ(-110, arr[1]["rssi"].as<int>());
  EXPECT_STREQ("", arr[1]["scopes"].as<const char*>());
  EXPECT_STREQ("stale", arr[1]["status"].as<const char*>());
}

TEST(MQTTPayloadBuilder, NeighborsMessageMeasurementsMatchCompletePayload) {
  MQTTPayloadBuilder::NeighborsMessageEntry neighbors[] = {
      {"0011223344556677", 9.75f, UINT32_MAX, "DEN,APRS", "responded"},
      {"8899AABBCCDDEEFF", -3.5f, UINT32_MAX, "", "timeout"},
  };
  neighbors[0].rssi = -87;
  neighbors[1].rssi = -110;

  size_t measured =
      MQTTPayloadBuilder::measureNeighborsMessageBase(
          "DEN Repeater", "0123456789ABCDEF", kTimestamp, "DEN,APRS", "*", 2)
      + MQTTPayloadBuilder::measureNeighborsMessageEntry(neighbors[0])
      + 1  // array comma
      + MQTTPayloadBuilder::measureNeighborsMessageEntry(neighbors[1]);

  JsonDocument scratch;
  char buffer[1024];
  int len = MQTTPayloadBuilder::buildNeighborsMessage(
      scratch, "DEN Repeater", "0123456789ABCDEF", kTimestamp, "DEN,APRS", "*",
      neighbors, 2, buffer, sizeof(buffer), 2, 2, false);

  ASSERT_GT(len, 0);
  EXPECT_EQ(measured, static_cast<size_t>(len));
}

TEST(MQTTPayloadBuilder, NeighborsMessageMeasuredPrefixStopsBeforeOverflow) {
  MQTTPayloadBuilder::NeighborsMessageEntry neighbors[20] = {};
  static char keys[20][17];
  static char long_scopes[96];
  memset(long_scopes, 'S', sizeof(long_scopes) - 1);
  long_scopes[sizeof(long_scopes) - 1] = '\0';
  for (int i = 0; i < 20; i++) {
    snprintf(keys[i], sizeof(keys[i]), "%016X", i);
    neighbors[i] = {
        keys[i], static_cast<float>(i), UINT32_MAX,
        long_scopes, "responded"};
    neighbors[i].rssi = -90 - i;
  }

  constexpr size_t kBufferSize = 512;
  size_t used = MQTTPayloadBuilder::measureNeighborsMessageBase(
      "node", "id", kTimestamp, "DEN", "*", 20);
  int published = 0;
  int queried = 0;
  while (queried < 20) {
    size_t added =
        MQTTPayloadBuilder::measureNeighborsMessageEntry(neighbors[queried])
        + (published > 0 ? 1U : 0U);
    queried++;
    if (used + added >= kBufferSize) break;
    used += added;
    published++;
  }

  ASSERT_GT(published, 0);
  ASSERT_LT(published, 20);
  ASSERT_EQ(published + 1, queried);  // only the non-fitting candidate was extra

  JsonDocument scratch;
  char buffer[kBufferSize];
  int len = MQTTPayloadBuilder::buildNeighborsMessage(
      scratch, "node", "id", kTimestamp, "DEN", "*",
      neighbors, published, buffer, sizeof(buffer),
      20, queried, true);

  ASSERT_GT(len, 0);
  EXPECT_LT(static_cast<size_t>(len), sizeof(buffer));
  JsonDocument parsed;
  ASSERT_FALSE(deserializeJson(parsed, buffer));
  EXPECT_EQ(static_cast<size_t>(published),
            parsed["neighbors"].as<JsonArray>().size());
  EXPECT_EQ(20, parsed["total_neighbors"].as<int>());
  EXPECT_EQ(queried, parsed["queried_neighbors"].as<int>());
  EXPECT_TRUE(parsed["truncated"].as<bool>());
}

TEST(MQTTPayloadBuilder, NeighborsMessageFallbackMarksTruncated) {
  MQTTPayloadBuilder::NeighborsMessageEntry neighbors[] = {
      {"0011223344556677", 9.75f, 1, "DEN", "responded"},
      {"8899AABBCCDDEEFF", -3.5f, 2, "APRS", "responded"},
  };
  neighbors[0].rssi = -87;
  neighbors[1].rssi = -110;
  size_t first_only_size =
      MQTTPayloadBuilder::measureNeighborsMessageBase(
          "node", "id", kTimestamp, "DEN", "*", 2)
      + MQTTPayloadBuilder::measureNeighborsMessageEntry(neighbors[0]);
  std::vector<char> buffer(first_only_size + 1);

  JsonDocument scratch;
  int len = MQTTPayloadBuilder::buildNeighborsMessage(
      scratch, "node", "id", kTimestamp, "DEN", "*",
      neighbors, 2, buffer.data(), buffer.size(),
      2, 2, false);

  ASSERT_GT(len, 0);
  JsonDocument parsed;
  ASSERT_FALSE(deserializeJson(parsed, buffer.data()));
  EXPECT_EQ(1U, parsed["neighbors"].as<JsonArray>().size());
  EXPECT_TRUE(parsed["truncated"].as<bool>());
}

TEST(MQTTPayloadBuilder, NeighborsMessageFailsCleanlyOnAllocationFailure) {
  MQTTPayloadBuilder::NeighborsMessageEntry neighbor = {
      "0011223344556677", 9.75f, 1, "DEN", "responded"};
  neighbor.rssi = -87;
  RejectAllJsonAllocations allocator;
  JsonDocument scratch(&allocator);
  char buffer[512];
  memset(buffer, 'x', sizeof(buffer));

  EXPECT_EQ(0, MQTTPayloadBuilder::buildNeighborsMessage(
      scratch, "node", "id", kTimestamp, "DEN", "*",
      &neighbor, 1, buffer, sizeof(buffer),
      1, 1, false));
  EXPECT_EQ('\0', buffer[0]);
}

TEST(MQTTPayloadBuilder, NeighborsMessageHandlesEmptyTableAndNullScopes) {
  JsonDocument scratch;
  char buffer[256];
  int len = MQTTPayloadBuilder::buildNeighborsMessage(
      scratch, "node", "id", kTimestamp, nullptr, nullptr, nullptr, 0,
      buffer, sizeof(buffer));

  ASSERT_GT(len, 0);
  JsonDocument parsed;
  ASSERT_FALSE(deserializeJson(parsed, buffer));
  EXPECT_STREQ("", parsed["self"]["scopes"].as<const char*>());
  EXPECT_STREQ("", parsed["self"]["default_scope"].as<const char*>());
  JsonArray arr = parsed["neighbors"].as<JsonArray>();
  ASSERT_TRUE(arr.isNull() == false);
  EXPECT_EQ(0U, arr.size());
}

TEST(MQTTPayloadBuilder, NeighborsMessageDropsTailWhenBufferFills) {
  // Twenty entries far exceed a tight buffer; the builder must emit a prefix
  // that still parses as complete JSON rather than truncating mid-document.
  MQTTPayloadBuilder::NeighborsMessageEntry neighbors[20] = {};
  static char keys[20][17];
  for (int i = 0; i < 20; i++) {
    snprintf(keys[i], sizeof(keys[i]), "%016X", i);
    neighbors[i].pubkey_hex = keys[i];
    neighbors[i].snr = static_cast<float>(i);
    neighbors[i].rssi = -90 - i;
    neighbors[i].heard_secs_ago = static_cast<uint32_t>(i) * 10U;
    neighbors[i].heard_unknown = false;
    neighbors[i].scopes = "DEN";
    neighbors[i].status = "active";
  }

  JsonDocument scratch;
  char buffer[512];
  int len = MQTTPayloadBuilder::buildNeighborsMessage(
      scratch, "node", "id", kTimestamp, "DEN", "*", neighbors, 20,
      buffer, sizeof(buffer));

  ASSERT_GT(len, 0);
  EXPECT_LT(static_cast<size_t>(len), sizeof(buffer));
  JsonDocument parsed;
  ASSERT_FALSE(deserializeJson(parsed, buffer));
  JsonArray arr = parsed["neighbors"].as<JsonArray>();
  ASSERT_FALSE(arr.isNull());
  EXPECT_GT(arr.size(), 0U);
  EXPECT_LT(arr.size(), 20U);
  EXPECT_FALSE(parsed["truncated"].is<JsonVariant>());
  // Kept entries are the head of the input, in order.
  EXPECT_STREQ(keys[0], arr[0]["pubkey"].as<const char*>());
}

TEST(MQTTPayloadBuilder, NeighborsMessageRendersUnknownHeardAgeAsNull) {
  // A neighbour stamped before the clock was set has no usable age. The key
  // stays present and null so a consumer cannot read it as "heard just now".
  MQTTPayloadBuilder::NeighborsMessageEntry neighbors[2] = {};
  neighbors[0] = {"0011223344556677", 9.75f, 42, "DEN", "responded"};
  neighbors[1] = {"8899AABBCCDDEEFF", -3.5f, 0, "DEN", "responded"};
  neighbors[0].rssi = -87;
  neighbors[1].rssi = -110;
  neighbors[1].heard_unknown = true;

  JsonDocument scratch;
  char buffer[1024];
  int len = MQTTPayloadBuilder::buildNeighborsMessage(
      scratch, "node", "id", kTimestamp, "DEN", "*", neighbors, 2,
      buffer, sizeof(buffer), 2, 2, false);

  ASSERT_GT(len, 0);
  JsonDocument parsed;
  ASSERT_FALSE(deserializeJson(parsed, buffer));
  JsonArray arr = parsed["neighbors"].as<JsonArray>();
  ASSERT_EQ(2U, arr.size());
  EXPECT_EQ(42U, arr[0]["heard_secs_ago"].as<uint32_t>());
  EXPECT_FALSE(arr[0]["heard_secs_ago"].isNull());
  EXPECT_TRUE(arr[1]["heard_secs_ago"].isNull());
  EXPECT_NE(nullptr, strstr(buffer, "\"heard_secs_ago\":null"));
  // The rest of the entry still renders.
  EXPECT_STREQ("8899AABBCCDDEEFF", arr[1]["pubkey"].as<const char*>());
  EXPECT_STREQ("responded", arr[1]["status"].as<const char*>());
}

TEST(MQTTPayloadBuilder, NeighborsMessageUnknownHeardAgeFitsMeasuredWidth) {
  // Paced discovery measures each entry with a known UINT32_MAX age; a null age
  // must never serialize wider than what that reserved.
  MQTTPayloadBuilder::NeighborsMessageEntry measured = {
      "0011223344556677", 9.75f, UINT32_MAX, "DEN", "responded"};
  measured.rssi = -87;
  MQTTPayloadBuilder::NeighborsMessageEntry unknown = measured;
  unknown.heard_unknown = true;
  unknown.heard_secs_ago = 0;

  EXPECT_LE(MQTTPayloadBuilder::measureNeighborsMessageEntry(unknown),
            MQTTPayloadBuilder::measureNeighborsMessageEntry(measured));
}

// ---- radio2 (MediaMesh) -------------------------------------------------------
// Main-radio messages stay byte-for-byte as before: the exact-contract tests
// above (status, raw) and the golden packet below pass no radio id.

TEST(MQTTPayloadBuilder, MainRadioPacketHasNoRadioKeys) {
  JsonDocument doc;
  char with_default[1024];
  char explicit_null[1024];
  int a = buildRepresentativePacket(doc, "rx", 0.125f, nullptr, 0, 0, "AABB",
                                    with_default, sizeof(with_default));
  int b = MQTTPayloadBuilder::buildPacketMessage(
      doc, "DEN Repeater", "0123456789ABCDEF", kTimestamp, "rx",
      "12:34:56", "18/07/2026", 42, 4, "D", 20, "AABB",
      10.26f, -87, 0.125f, "89ABCDEF01234567", nullptr, 0, 0, 64,
      explicit_null, sizeof(explicit_null), nullptr);
  ASSERT_GT(a, 0);
  EXPECT_EQ(a, b);
  EXPECT_STREQ(with_default, explicit_null);
  EXPECT_EQ(nullptr, strstr(with_default, "radio_id"));
  EXPECT_EQ(nullptr, strstr(with_default, "mediamesh"));
}

TEST(MQTTPayloadBuilder, Radio2RxPacketCarriesRxRadioIdAndMediamesh) {
  JsonDocument doc;
  char main_buf[1024];
  char r2_buf[1024];
  int main_len = buildRepresentativePacket(doc, "rx", 0.125f, nullptr, 0, 0, "AABB",
                                           main_buf, sizeof(main_buf));
  int len = MQTTPayloadBuilder::buildPacketMessage(
      doc, "DEN Repeater", "0123456789ABCDEF", kTimestamp, "rx",
      "12:34:56", "18/07/2026", 42, 4, "D", 20, "AABB",
      10.26f, -87, 0.125f, "89ABCDEF01234567", nullptr, 0, 0, 64,
      r2_buf, sizeof(r2_buf), kMQTTRadio2Id);
  ASSERT_GT(len, 0);
  // Only appended keys: the main-radio message is an exact prefix.
  std::string expected(main_buf, main_len - 1);
  expected += ",\"rx_radio_id\":\"radio2\",\"mediamesh\":true}";
  EXPECT_EQ(expected, std::string(r2_buf));

  JsonDocument parsed;
  ASSERT_FALSE(deserializeJson(parsed, r2_buf));
  EXPECT_STREQ("radio2", parsed["rx_radio_id"].as<const char*>());
  EXPECT_TRUE(parsed["mediamesh"].is<bool>());
  EXPECT_TRUE(parsed["mediamesh"].as<bool>());
  EXPECT_FALSE(parsed["tx_radio_id"].is<JsonVariant>());
  EXPECT_STREQ("10.3", parsed["SNR"].as<const char*>());  // existing keys unchanged
}

TEST(MQTTPayloadBuilder, Radio2TxPacketCarriesTxRadioId) {
  JsonDocument doc;
  char buffer[1024];
  int len = MQTTPayloadBuilder::buildPacketMessage(
      doc, "DEN Repeater", "0123456789ABCDEF", kTimestamp, "tx",
      "12:34:56", "18/07/2026", 42, 4, "F", 20, "AABB",
      -999.0f, -999, NAN, "89ABCDEF01234567", nullptr, 0, 0, 64,
      buffer, sizeof(buffer), kMQTTRadio2Id);
  ASSERT_GT(len, 0);
  JsonDocument parsed;
  ASSERT_FALSE(deserializeJson(parsed, buffer));
  EXPECT_STREQ("radio2", parsed["tx_radio_id"].as<const char*>());
  EXPECT_FALSE(parsed["rx_radio_id"].is<JsonVariant>());
  EXPECT_TRUE(parsed["mediamesh"].as<bool>());
}

TEST(MQTTPayloadBuilder, Radio2RawMessageCarriesRadioId) {
  JsonDocument doc;
  char buffer[512];
  int len = MQTTPayloadBuilder::buildRawMessage(
      doc, "node", "id", kTimestamp, "AABB", buffer, sizeof(buffer), kMQTTRadio2Id, false);
  ASSERT_GT(len, 0);
  EXPECT_STREQ(
      "{\"origin\":\"node\",\"origin_id\":\"id\","
      "\"timestamp\":\"2026-07-18T12:34:56.123456+00:00\","
      "\"type\":\"RAW\",\"data\":\"AABB\",\"rx_radio_id\":\"radio2\",\"mediamesh\":true}",
      buffer);
  len = MQTTPayloadBuilder::buildRawMessage(
      doc, "node", "id", kTimestamp, "AABB", buffer, sizeof(buffer), kMQTTRadio2Id, true);
  ASSERT_GT(len, 0);
  JsonDocument parsed;
  ASSERT_FALSE(deserializeJson(parsed, buffer));
  EXPECT_STREQ("radio2", parsed["tx_radio_id"].as<const char*>());
}

TEST(MQTTPayloadBuilder, StatusListsRadio2WhenOn) {
  const MQTTRadio2Info radio2 = {"911.150000,250.0,7,5", "rxtx", false};
  JsonDocument doc;
  char buffer[1024];
  char minimal[1024];
  int minimal_len = buildMinimalStatus(doc, minimal, sizeof(minimal));
  int len = MQTTPayloadBuilder::buildStatusMessage(
      doc, "DEN Repeater", "0123456789ABCDEF", "Heltec V3", "v1.16.0",
      "915.000000,62.5,7,5", "MeshCore", "online", kTimestamp,
      buffer, sizeof(buffer), -1, -1, -1, -1, -999, -1, -1, -1, -1, -1, -1,
      nullptr, MQTTConnHealth(), &radio2);
  ASSERT_GT(len, 0);
  std::string expected(minimal, minimal_len - 1);
  expected += ",\"radios\":[{\"id\":\"radio2\",\"radio\":\"911.150000,250.0,7,5\","
              "\"mode\":\"rxtx\",\"mediamesh\":true,\"temporary\":false}]}";
  EXPECT_EQ(expected, std::string(buffer));

  JsonDocument parsed;
  ASSERT_FALSE(deserializeJson(parsed, buffer));
  EXPECT_STREQ("915.000000,62.5,7,5", parsed["radio"].as<const char*>());  // still the main radio
}

TEST(MQTTPayloadBuilder, StatusOmitsRadiosWhenRadio2OffOrAbsent) {
  const MQTTRadio2Info off = {nullptr, "off", false};
  JsonDocument doc;
  char buffer[1024];
  char minimal[1024];
  buildMinimalStatus(doc, minimal, sizeof(minimal));
  int len = MQTTPayloadBuilder::buildStatusMessage(
      doc, "DEN Repeater", "0123456789ABCDEF", "Heltec V3", "v1.16.0",
      "915.000000,62.5,7,5", "MeshCore", "online", kTimestamp,
      buffer, sizeof(buffer), -1, -1, -1, -1, -999, -1, -1, -1, -1, -1, -1,
      nullptr, MQTTConnHealth(), &off);
  ASSERT_GT(len, 0);
  EXPECT_STREQ(minimal, buffer);
}

TEST(MQTTPayloadBuilder, NeighborsMessageCarriesRadio2) {
  const MQTTRadio2Info self = {"910.525000,250.0,7,5", "rxtx", true};
  const MQTTRadio2Info on = {"911.150000,250.0,7,5", "rx", false};
  const MQTTRadio2Info off = {nullptr, "off", false};
  MQTTPayloadBuilder::NeighborsMessageEntry neighbors[] = {
      {"0011223344556677", 9.75f, 42, "DEN", "responded"},
      {"8899AABBCCDDEEFF", 12.5f, 300, "DEN", "responded"},
      {"1122334455667788", 1.0f, 900, "", "timeout"},
  };
  neighbors[0].radio2 = &on;
  neighbors[1].radio2 = &off;
  // neighbors[2]: no trailer, radio2 stays nullptr (unknown)

  JsonDocument scratch;
  char buffer[2048];
  int len = MQTTPayloadBuilder::buildNeighborsMessage(
      scratch, "DEN Repeater", "0123456789ABCDEF", kTimestamp, "DEN", "*",
      neighbors, 3, buffer, sizeof(buffer), 3, 3, false, &self);
  ASSERT_GT(len, 0);
  JsonDocument parsed;
  ASSERT_FALSE(deserializeJson(parsed, buffer));
  EXPECT_STREQ("rxtx", parsed["self"]["radio2"]["mode"].as<const char*>());
  EXPECT_STREQ("910.525000,250.0,7,5", parsed["self"]["radio2"]["radio"].as<const char*>());
  EXPECT_TRUE(parsed["self"]["radio2"]["temporary"].as<bool>());

  JsonArray arr = parsed["neighbors"].as<JsonArray>();
  ASSERT_EQ(3U, arr.size());
  EXPECT_STREQ("rx", arr[0]["radio2"]["mode"].as<const char*>());
  EXPECT_STREQ("911.150000,250.0,7,5", arr[0]["radio2"]["radio"].as<const char*>());
  EXPECT_FALSE(arr[0]["radio2"]["temporary"].as<bool>());
  EXPECT_STREQ("off", arr[1]["radio2"]["mode"].as<const char*>());
  EXPECT_FALSE(arr[1]["radio2"]["radio"].is<JsonVariant>());
  EXPECT_FALSE(arr[1]["radio2"]["temporary"].is<JsonVariant>());
  EXPECT_FALSE(arr[2]["radio2"].is<JsonVariant>());  // unknown: key left out
}

TEST(MQTTPayloadBuilder, NeighborsMessageWithoutRadio2IsUnchanged) {
  MQTTPayloadBuilder::NeighborsMessageEntry neighbors[] = {
      {"0011223344556677", 9.75f, 42, "DEN,APRS", "responded"},
  };
  JsonDocument scratch;
  char before[1024];
  char after[1024];
  int a = MQTTPayloadBuilder::buildNeighborsMessage(
      scratch, "DEN Repeater", "0123456789ABCDEF", kTimestamp, "DEN,APRS", "*",
      neighbors, 1, before, sizeof(before), 1, 1, false);
  int b = MQTTPayloadBuilder::buildNeighborsMessage(
      scratch, "DEN Repeater", "0123456789ABCDEF", kTimestamp, "DEN,APRS", "*",
      neighbors, 1, after, sizeof(after), 1, 1, false, nullptr);
  ASSERT_GT(a, 0);
  EXPECT_EQ(a, b);
  EXPECT_STREQ(before, after);
  EXPECT_EQ(nullptr, strstr(before, "radio2"));
}

TEST(MQTTPayloadBuilder, NeighborsMeasurementsIncludeRadio2) {
  const MQTTRadio2Info self = {"910.525000,250.0,7,5", "rxtx", false};
  const MQTTRadio2Info on = {"911.150000,250.0,7,5", "rxtx", true};
  MQTTPayloadBuilder::NeighborsMessageEntry entry = {
      "0011223344556677", 9.75f, 42, "DEN", "responded"};
  entry.radio2 = &on;
  JsonDocument scratch;
  char buffer[2048];
  int len = MQTTPayloadBuilder::buildNeighborsMessage(
      scratch, "DEN Repeater", "0123456789ABCDEF", kTimestamp, "DEN", "*",
      &entry, 1, buffer, sizeof(buffer), 1, 1, false, &self);
  ASSERT_GT(len, 0);
  size_t measured =
      MQTTPayloadBuilder::measureNeighborsMessageBase(
          "DEN Repeater", "0123456789ABCDEF", kTimestamp, "DEN", "*", 1, &self) +
      MQTTPayloadBuilder::measureNeighborsMessageEntry(entry);
  EXPECT_GE(measured, static_cast<size_t>(len));
}

}  // namespace

int main(int argc, char** argv) {
  ::testing::InitGoogleTest(&argc, argv);
  return RUN_ALL_TESTS();
}
