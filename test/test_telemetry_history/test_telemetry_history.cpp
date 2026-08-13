#include <gtest/gtest.h>

#include <cmath>
#include <cstdint>
#include <cstring>
#include <string>
#include <vector>

#include <helpers/TelemetryHistory.h>

namespace {

int base64Value(char value) {
  if (value >= 'A' && value <= 'Z') return value - 'A';
  if (value >= 'a' && value <= 'z') return value - 'a' + 26;
  if (value >= '0' && value <= '9') return value - '0' + 52;
  if (value == '+') return 62;
  if (value == '/') return 63;
  return -1;
}

std::vector<uint8_t> decodeReply(const char* reply) {
  EXPECT_EQ('>', reply[0]);
  EXPECT_EQ(' ', reply[1]);
  const char* encoded = reply + 2;
  std::vector<uint8_t> decoded;
  uint32_t accumulator = 0;
  unsigned bits = 0;
  for (const char* cursor = encoded; *cursor != 0 && *cursor != '='; cursor++) {
    const int value = base64Value(*cursor);
    EXPECT_GE(value, 0);
    if (value < 0) break;
    accumulator = (accumulator << 6) | (uint32_t)value;
    bits += 6;
    if (bits >= 8) {
      bits -= 8;
      decoded.push_back((uint8_t)(accumulator >> bits));
      accumulator &= bits == 0 ? 0U : ((1U << bits) - 1U);
    }
  }
  return decoded;
}

uint32_t uint32LE(const uint8_t* source) {
  return (uint32_t)source[0]
      | ((uint32_t)source[1] << 8)
      | ((uint32_t)source[2] << 16)
      | ((uint32_t)source[3] << 24);
}

uint16_t uint16LE(const uint8_t* source) {
  return (uint16_t)source[0] | ((uint16_t)source[1] << 8);
}

int32_t int32LE(const uint8_t* source) {
  return (int32_t)uint32LE(source);
}

uint32_t readBits(const uint8_t* source, size_t& bit_offset, uint8_t width) {
  uint32_t value = 0;
  for (uint8_t i = 0; i < width; i++) {
    value = (value << 1)
        | ((source[bit_offset / 8U] >> (7U - bit_offset % 8U)) & 1U);
    bit_offset++;
  }
  return value;
}

int signed14(uint32_t value) {
  return (value & 0x2000U) != 0 ? (int)value - 0x4000 : (int)value;
}

}  // namespace

TEST(TelemetryHistory, EncodesTemperatureSentinelsAndEndpoints) {
  using mesh::TelemetryHistory;
  uint8_t status = 0xFF;

  EXPECT_EQ(0, TelemetryHistory::encodeTemperature(0, false, status));
  EXPECT_EQ(TelemetryHistory::TEMPERATURE_NONE, status);
  EXPECT_EQ(0, TelemetryHistory::encodeTemperature(-51, true, status));
  EXPECT_EQ(TelemetryHistory::TEMPERATURE_LOW, status);
  EXPECT_EQ(0, TelemetryHistory::encodeTemperature(-50, true, status));
  EXPECT_EQ(TelemetryHistory::TEMPERATURE_VALUE, status);
  EXPECT_EQ(50, TelemetryHistory::encodeTemperature(0, true, status));
  EXPECT_EQ(TelemetryHistory::TEMPERATURE_VALUE, status);
  EXPECT_EQ(127, TelemetryHistory::encodeTemperature(77, true, status));
  EXPECT_EQ(TelemetryHistory::TEMPERATURE_VALUE, status);
  EXPECT_EQ(127, TelemetryHistory::encodeTemperature(78, true, status));
  EXPECT_EQ(TelemetryHistory::TEMPERATURE_HIGH, status);
}

TEST(TelemetryHistory, EncodesVoltageSentinelsAndHundredths) {
  using mesh::TelemetryHistory;

  EXPECT_EQ(0, TelemetryHistory::encodeVoltage(0));
  EXPECT_EQ(1, TelemetryHistory::encodeVoltage(1879));
  EXPECT_EQ(2, TelemetryHistory::encodeVoltage(1880));
  EXPECT_EQ(3, TelemetryHistory::encodeVoltage(1890));
  EXPECT_EQ(254, TelemetryHistory::encodeVoltage(4400));
  EXPECT_EQ(255, TelemetryHistory::encodeVoltage(4401));
}

TEST(TelemetryHistory, FormatsRollingDayAndExplicitMissingBuckets) {
  using mesh::TelemetryHistory;
  TelemetryHistory history;
  const uint32_t first_bucket = 1000000U;
  const uint32_t first_epoch = first_bucket * TelemetryHistory::SAMPLE_INTERVAL_SECONDS;

  history.record(first_epoch, -51, true, 1880, 0, 0, false);
  history.record(first_epoch + 2U * TelemetryHistory::SAMPLE_INTERVAL_SECONDS,
                 78, true, 4400, 0, 0, false);

  char reply[160];
  ASSERT_TRUE(history.formatPageReply(TelemetryHistory::SERIES_TEMPERATURE,
                                      "", reply, sizeof(reply)));
  EXPECT_EQ(86U, strlen(reply));
  const std::vector<uint8_t> temperature_payload = decodeReply(reply);
  ASSERT_EQ(61U, temperature_payload.size());
  EXPECT_EQ(TelemetryHistory::TEMPERATURE_PAYLOAD_TYPE_V1,
            temperature_payload[0]);
  EXPECT_EQ((first_bucket + 2U - 47U) * TelemetryHistory::SAMPLE_INTERVAL_SECONDS,
            uint32LE(&temperature_payload[1]));
  EXPECT_EQ(30, temperature_payload[5]);
  EXPECT_EQ(48, temperature_payload[6]);

  size_t status_offset = 0;
  size_t bit_offset = 0;
  for (int slot = 0; slot < 48; slot++) {
    const uint8_t status = (uint8_t)readBits(&temperature_payload[7],
                                              status_offset, 2);
    const uint8_t temperature = (uint8_t)readBits(&temperature_payload[19],
                                                   bit_offset, 7);
    if (slot == 45) {
      EXPECT_EQ(TelemetryHistory::TEMPERATURE_LOW, status);
      EXPECT_EQ(0, temperature);
    } else if (slot == 47) {
      EXPECT_EQ(TelemetryHistory::TEMPERATURE_HIGH, status);
      EXPECT_EQ(127, temperature);
    } else {
      EXPECT_EQ(TelemetryHistory::TEMPERATURE_NONE, status);
      EXPECT_EQ(0, temperature);
    }
  }

  ASSERT_TRUE(history.formatPageReply(TelemetryHistory::SERIES_VOLTAGE,
                                      "1", reply, sizeof(reply)));
  EXPECT_EQ(78U, strlen(reply));
  const std::vector<uint8_t> voltage_payload = decodeReply(reply);
  ASSERT_EQ(55U, voltage_payload.size());
  EXPECT_EQ(TelemetryHistory::VOLTAGE_PAYLOAD_TYPE_V1, voltage_payload[0]);
  EXPECT_EQ((first_bucket + 2U - 47U) * TelemetryHistory::SAMPLE_INTERVAL_SECONDS,
            uint32LE(&voltage_payload[1]));
  for (int slot = 0; slot < 48; slot++) {
    if (slot == 45) {
      EXPECT_EQ(2, voltage_payload[7 + slot]);
    } else if (slot == 47) {
      EXPECT_EQ(254, voltage_payload[7 + slot]);
    } else {
      EXPECT_EQ(0, voltage_payload[7 + slot]);
    }
  }
}

TEST(TelemetryHistory, RetainsExactlySevenTemperatureVoltageDays) {
  using mesh::TelemetryHistory;
  TelemetryHistory history;
  const uint32_t first_bucket = 1000U;
  for (uint32_t sample = 0; sample <= TelemetryHistory::TV_RETENTION_SAMPLES; sample++) {
    history.record((first_bucket + sample) * TelemetryHistory::SAMPLE_INTERVAL_SECONDS,
                   0, true, (uint16_t)(1880U + (sample % 253U) * 10U),
                   0, 0, false);
  }

  char reply[160];
  ASSERT_TRUE(history.formatPageReply(TelemetryHistory::SERIES_TEMPERATURE,
                                      "7", reply, sizeof(reply)));
  const std::vector<uint8_t> payload = decodeReply(reply);
  ASSERT_EQ(61U, payload.size());
  EXPECT_EQ((first_bucket + 1U) * TelemetryHistory::SAMPLE_INTERVAL_SECONDS,
            uint32LE(&payload[1]));

  size_t status_offset = 0;
  EXPECT_EQ(TelemetryHistory::TEMPERATURE_VALUE,
            readBits(&payload[7], status_offset, 2));
  size_t bit_offset = 0;
  EXPECT_EQ(50U, readBits(&payload[19], bit_offset, 7));

  ASSERT_TRUE(history.formatPageReply(TelemetryHistory::SERIES_VOLTAGE,
                                      "7", reply, sizeof(reply)));
  const std::vector<uint8_t> voltage_payload = decodeReply(reply);
  ASSERT_EQ(55U, voltage_payload.size());
  EXPECT_EQ((first_bucket + 1U) * TelemetryHistory::SAMPLE_INTERVAL_SECONDS,
            uint32LE(&voltage_payload[1]));
  EXPECT_EQ(3U, voltage_payload[7]);
}

TEST(TelemetryHistory, FormatsMaximumRawVoltageSnapshot) {
  using mesh::TelemetryHistory;
  TelemetryHistory history;
  const uint32_t first_bucket = 500000U;
  const uint32_t total_samples = 200U;
  for (uint32_t sample = 0; sample < total_samples; sample++) {
    history.record((first_bucket + sample)
                       * TelemetryHistory::SAMPLE_INTERVAL_SECONDS,
                   20, true,
                   (uint16_t)(1880U + (sample % 253U) * 10U),
                   0, 0, false);
  }

  const uint8_t source_id[TelemetryHistory::BINARY_SOURCE_ID_SIZE] = {
      0x01, 0x23, 0x45, 0x67, 0x89, 0xAB, 0xCD, 0xEF};
  uint8_t payload[TelemetryHistory::BINARY_PAYLOAD_SIZE];
  ASSERT_EQ(sizeof(payload), history.formatVoltageBinarySnapshot(
                                 source_id, payload, sizeof(payload)));
  EXPECT_EQ(0, memcmp(payload, "TVB1", 4));
  EXPECT_EQ(0, memcmp(&payload[4], source_id, sizeof(source_id)));

  const uint32_t oldest_sample =
      total_samples - TelemetryHistory::BINARY_MAX_SAMPLES;
  EXPECT_EQ((first_bucket + oldest_sample)
                * TelemetryHistory::SAMPLE_INTERVAL_SECONDS,
            uint32LE(&payload[12]));
  EXPECT_EQ(30, uint16LE(&payload[16]));
  EXPECT_EQ(TelemetryHistory::BINARY_MAX_SAMPLES, payload[18]);
  for (uint16_t slot = 0;
       slot < TelemetryHistory::BINARY_MAX_SAMPLES; slot++) {
    const uint32_t sample = oldest_sample + slot;
    EXPECT_EQ(TelemetryHistory::encodeVoltage(
                  (uint16_t)(1880U + (sample % 253U) * 10U)),
              payload[TelemetryHistory::BINARY_HEADER_SIZE + slot]);
  }
}

TEST(TelemetryHistory, FormatsMaximumRawTemperatureSnapshot) {
  using mesh::TelemetryHistory;
  TelemetryHistory history;
  const uint32_t first_bucket = 700000U;
  const uint32_t total_samples = TelemetryHistory::BINARY_MAX_SAMPLES;
  for (uint32_t sample = 0; sample < total_samples; sample++) {
    const int16_t temperature = sample == 0 ? -51
        : sample == 1 ? 78 : (int16_t)(-50 + sample % 128U);
    history.record((first_bucket + sample)
                       * TelemetryHistory::SAMPLE_INTERVAL_SECONDS,
                   temperature, sample != 2, 3700, 0, 0, false);
  }

  const uint8_t source_id[TelemetryHistory::BINARY_SOURCE_ID_SIZE] = {
      0x10, 0x32, 0x54, 0x76, 0x98, 0xBA, 0xDC, 0xFE};
  uint8_t payload[TelemetryHistory::BINARY_PAYLOAD_SIZE];
  ASSERT_EQ(sizeof(payload), history.formatTemperatureBinarySnapshot(
                                 source_id, payload, sizeof(payload)));
  EXPECT_EQ(0, memcmp(payload, "TTB1", 4));
  EXPECT_EQ(0, memcmp(&payload[4], source_id, sizeof(source_id)));
  EXPECT_EQ(first_bucket * TelemetryHistory::SAMPLE_INTERVAL_SECONDS,
            uint32LE(&payload[12]));
  EXPECT_EQ(30, uint16LE(&payload[16]));
  EXPECT_EQ(TelemetryHistory::BINARY_MAX_SAMPLES, payload[18]);
  EXPECT_EQ(1, payload[TelemetryHistory::BINARY_HEADER_SIZE]);
  EXPECT_EQ(2, payload[TelemetryHistory::BINARY_HEADER_SIZE + 1U]);
  EXPECT_EQ(0, payload[TelemetryHistory::BINARY_HEADER_SIZE + 2U]);
  for (uint16_t slot = 3; slot < TelemetryHistory::BINARY_MAX_SAMPLES; slot++) {
    EXPECT_EQ((uint8_t)(slot % 128U + 3U),
              payload[TelemetryHistory::BINARY_HEADER_SIZE + slot]);
  }
}

TEST(TelemetryHistory, RawVoltageSnapshotRequiresHistoryAndDataSpace) {
  using mesh::TelemetryHistory;
  TelemetryHistory history;
  const uint8_t source_id[TelemetryHistory::BINARY_SOURCE_ID_SIZE] = {};
  uint8_t payload[TelemetryHistory::BINARY_PAYLOAD_SIZE];

  EXPECT_EQ(0U, history.formatVoltageBinarySnapshot(
                    source_id, payload, sizeof(payload)));
  history.record(1800000000U, 20, true, 3700, 0, 0, false);
  EXPECT_EQ(0U, history.formatVoltageBinarySnapshot(
                    source_id, payload,
                    TelemetryHistory::BINARY_HEADER_SIZE));
  EXPECT_EQ(TelemetryHistory::BINARY_HEADER_SIZE + 1U,
            history.formatVoltageBinarySnapshot(
                source_id, payload,
                TelemetryHistory::BINARY_HEADER_SIZE + 1U));
  EXPECT_EQ(1, payload[18]);
}

TEST(TelemetryHistory, EmitsZeroGpsOriginAndDeltasWithoutFixes) {
  using mesh::TelemetryHistory;
  TelemetryHistory history;
  const uint32_t epoch = 1000000U * TelemetryHistory::SAMPLE_INTERVAL_SECONDS;
  history.record(epoch, 20, true, 3700, 0, 0, false);

  char reply[160];
  ASSERT_TRUE(history.formatPageReply(TelemetryHistory::SERIES_GPS,
                                      "1", reply, sizeof(reply)));
  EXPECT_EQ(138U, strlen(reply));
  const std::vector<uint8_t> payload = decodeReply(reply);
  ASSERT_EQ(101U, payload.size());
  EXPECT_EQ(TelemetryHistory::GPS_PAYLOAD_TYPE_V1, payload[0]);
  EXPECT_EQ(0, int32LE(&payload[7]));
  EXPECT_EQ(0, int32LE(&payload[11]));
  EXPECT_EQ(0xFF, payload[15]);
  for (size_t i = 17; i < payload.size(); i++) EXPECT_EQ(0, payload[i]);
}

TEST(TelemetryHistory, EncodesGpsAsTenMeterFourteenBitDeltas) {
  using mesh::TelemetryHistory;
  TelemetryHistory history;
  const uint32_t first_bucket = 1000000U;
  const uint32_t interval = TelemetryHistory::SAMPLE_INTERVAL_SECONDS;
  const int32_t latitude = 470000000;
  const int32_t longitude = -1220000000;

  history.record(first_bucket * interval, 20, true, 3700,
                 latitude, longitude, true);
  history.record((first_bucket + 1U) * interval, 20, true, 3700,
                 latitude + 8983, longitude, true);  // about 100 m north
  history.record((first_bucket + 2U) * interval, 20, true, 3700,
                 latitude + 8983, longitude + 13160, true);  // about 100 m east

  char reply[160];
  ASSERT_TRUE(history.formatPageReply(TelemetryHistory::SERIES_GPS,
                                      "1", reply, sizeof(reply)));
  const std::vector<uint8_t> payload = decodeReply(reply);
  ASSERT_EQ(101U, payload.size());
  EXPECT_EQ(latitude, int32LE(&payload[7]));
  EXPECT_EQ(longitude, int32LE(&payload[11]));
  EXPECT_EQ(21, payload[15]);
  EXPECT_EQ(0, payload[16]);

  size_t bit_offset = 0;
  for (int slot = 0; slot < 24; slot++) {
    const int north = signed14(readBits(&payload[17], bit_offset, 14));
    const int east = signed14(readBits(&payload[17], bit_offset, 14));
    if (slot == 22) {
      EXPECT_NEAR(10, north, 1);
      EXPECT_NEAR(0, east, 1);
    } else if (slot == 23) {
      EXPECT_NEAR(0, north, 1);
      EXPECT_NEAR(10, east, 1);
    } else {
      EXPECT_EQ(0, north);
      EXPECT_EQ(0, east);
    }
  }
}

TEST(TelemetryHistory, ExpandsGpsRetentionWithinHeapBudget) {
  using mesh::TelemetryHistory;
  TelemetryHistory history;

  EXPECT_EQ(3, history.gpsRetentionDays());
  EXPECT_EQ(6, history.gpsPageCount());
  EXPECT_EQ(5, history.resizeGpsDays(
                   30, 5U * TelemetryHistory::GPS_HEAP_BYTES_PER_DAY));
  EXPECT_EQ(10, history.gpsPageCount());
  EXPECT_EQ(4, history.resizeGpsDays(4, 0));
  EXPECT_EQ(8, history.gpsPageCount());
  EXPECT_EQ(3, history.resizeGpsDays(3, 0));
  EXPECT_EQ(6, history.gpsPageCount());

  TelemetryHistory maximum;
  EXPECT_EQ(30, maximum.resizeGpsDays(
                    30, 30U * TelemetryHistory::GPS_HEAP_BYTES_PER_DAY));
  EXPECT_EQ(60, maximum.gpsPageCount());
}

TEST(TelemetryHistory, PreservesNewestGpsSamplesAcrossResizes) {
  using mesh::TelemetryHistory;
  TelemetryHistory history;
  const uint32_t first_bucket = 1000000U;
  const uint32_t interval = TelemetryHistory::SAMPLE_INTERVAL_SECONDS;
  const int32_t latitude = 470000000;
  const int32_t longitude = -1220000000;

  for (uint32_t sample = 0; sample <= TelemetryHistory::GPS_RETENTION_SAMPLES;
       sample++) {
    history.record((first_bucket + sample) * interval, 20, true, 3700,
                   latitude + (int32_t)sample * 1000,
                   longitude, true);
  }
  ASSERT_EQ(7, history.resizeGpsDays(
                   7, 7U * TelemetryHistory::GPS_HEAP_BYTES_PER_DAY));
  for (uint32_t sample = TelemetryHistory::GPS_RETENTION_SAMPLES + 1U;
       sample <= 7U * TelemetryHistory::SAMPLES_PER_DAY; sample++) {
    history.record((first_bucket + sample) * interval, 20, true, 3700,
                   latitude + (int32_t)sample * 1000,
                   longitude, true);
  }

  char reply[160];
  ASSERT_TRUE(history.formatPageReply(TelemetryHistory::SERIES_GPS,
                                      "14", reply, sizeof(reply)));
  std::vector<uint8_t> payload = decodeReply(reply);
  ASSERT_EQ(101U, payload.size());
  EXPECT_EQ((first_bucket + 1U) * interval, uint32LE(&payload[1]));
  EXPECT_EQ(latitude + 1000, int32LE(&payload[7]));
  EXPECT_FALSE(history.formatPageReply(TelemetryHistory::SERIES_GPS,
                                       "15", reply, sizeof(reply)));
  EXPECT_STREQ("Err - telemetry.gps page must be 1-14", reply);

  ASSERT_EQ(2, history.resizeGpsDays(2, 0));
  ASSERT_TRUE(history.formatPageReply(TelemetryHistory::SERIES_GPS,
                                      "4", reply, sizeof(reply)));
  payload = decodeReply(reply);
  ASSERT_EQ(101U, payload.size());
  EXPECT_EQ((first_bucket + 241U) * interval, uint32LE(&payload[1]));
  EXPECT_EQ(latitude + 241000, int32LE(&payload[7]));
}

TEST(TelemetryHistory, ValidatesRetentionAndPagingArguments) {
  mesh::TelemetryHistory history;
  history.record(1800000000U, 20, true, 3700, 0, 0, false);
  char reply[160];

  EXPECT_TRUE(history.formatPageReply(mesh::TelemetryHistory::SERIES_TEMPERATURE,
                                      "7", reply, sizeof(reply)));
  EXPECT_FALSE(history.formatPageReply(mesh::TelemetryHistory::SERIES_TEMPERATURE,
                                       "8", reply, sizeof(reply)));
  EXPECT_STREQ("Err - telemetry.temp page must be 1-7", reply);
  EXPECT_FALSE(history.formatPageReply(mesh::TelemetryHistory::SERIES_VOLTAGE,
                                       "0", reply, sizeof(reply)));
  EXPECT_STREQ("Err - telemetry.volt page must be 1-7", reply);
  EXPECT_TRUE(history.formatPageReply(mesh::TelemetryHistory::SERIES_GPS,
                                      "6", reply, sizeof(reply)));
  EXPECT_FALSE(history.formatPageReply(mesh::TelemetryHistory::SERIES_GPS,
                                       "7", reply, sizeof(reply)));
  EXPECT_STREQ("Err - telemetry.gps page must be 1-6", reply);
  EXPECT_FALSE(history.formatPageReply(mesh::TelemetryHistory::SERIES_GPS,
                                       "1 extra", reply, sizeof(reply)));
  EXPECT_NE(nullptr, strstr(reply, "get telemetry.gps"));
}

int main(int argc, char** argv) {
  ::testing::InitGoogleTest(&argc, argv);
  return RUN_ALL_TESTS();
}
