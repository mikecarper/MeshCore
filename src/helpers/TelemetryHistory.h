#pragma once

#include <math.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

namespace mesh {

// A bounded, boot-local telemetry history for repeater diagnostics.
// Samples are aligned to 30-minute UTC buckets. Temperature and voltage keep
// seven days; GPS keeps three days by default and can grow to 30 days. Missing
// buckets are inserted explicitly so a temperature or voltage page always
// describes the same 48 half-hour positions. GPS pages contain 24 half-hour
// positions.
class TelemetryHistory {
  struct GpsSample {
    int32_t latitude_e7;
    int32_t longitude_e7;
  };
  static_assert(sizeof(GpsSample) == 8, "GPS sample must remain eight bytes");

public:
  static constexpr uint32_t SAMPLE_INTERVAL_SECONDS = 30UL * 60UL;
  static constexpr uint8_t SAMPLES_PER_DAY = 48;
  static constexpr uint16_t TV_RETENTION_SAMPLES = 7U * SAMPLES_PER_DAY;
  static constexpr uint8_t GPS_DEFAULT_RETENTION_DAYS = 3;
  static constexpr uint8_t GPS_MAX_RETENTION_DAYS = 30;
  static constexpr uint16_t GPS_RETENTION_SAMPLES =
      GPS_DEFAULT_RETENTION_DAYS * SAMPLES_PER_DAY;
  static constexpr uint8_t GPS_SAMPLES_PER_PAGE = 24;
  static constexpr uint8_t TV_PAGE_COUNT = 7;
  static constexpr uint8_t GPS_DEFAULT_PAGE_COUNT =
      GPS_DEFAULT_RETENTION_DAYS * 2U;
  static constexpr size_t GPS_HEAP_BYTES_PER_DAY =
      SAMPLES_PER_DAY * sizeof(GpsSample);

  static constexpr uint8_t TEMPERATURE_PAYLOAD_TYPE_V1 = 0x11;
  static constexpr uint8_t VOLTAGE_PAYLOAD_TYPE_V1 = 0x12;
  static constexpr uint8_t GPS_PAYLOAD_TYPE_V1 = 0x13;

  // A raw custom temperature ("TTB1") or voltage ("TVB1") snapshot fills one
  // 184-byte mesh payload: 19 metadata bytes plus 165 chronological samples.
  static constexpr size_t BINARY_HEADER_SIZE = 19;
  static constexpr uint8_t BINARY_SOURCE_ID_SIZE = 8;
  static constexpr uint8_t BINARY_MAX_SAMPLES = 165;
  static constexpr size_t BINARY_PAYLOAD_SIZE =
      BINARY_HEADER_SIZE + BINARY_MAX_SAMPLES;

  enum Series : uint8_t {
    SERIES_TEMPERATURE = 0,
    SERIES_VOLTAGE = 1,
    SERIES_GPS = 2,
  };

  enum TemperatureStatus : uint8_t {
    TEMPERATURE_NONE = 0,
    TEMPERATURE_VALUE = 1,
    TEMPERATURE_LOW = 2,
    TEMPERATURE_HIGH = 3,
  };

  TelemetryHistory()
      : _gps_samples(_gps_default_samples),
        _gps_capacity(GPS_RETENTION_SAMPLES),
        _gps_allocated_capacity(0) {
    clear();
  }

  ~TelemetryHistory() {
    if (_gps_allocated_capacity != 0) free(_gps_samples);
  }

  TelemetryHistory(const TelemetryHistory&) = delete;
  TelemetryHistory& operator=(const TelemetryHistory&) = delete;

  void clear() {
    memset(_temperature, 0, sizeof(_temperature));
    memset(_temperature_status, 0, sizeof(_temperature_status));
    memset(_voltage, 0, sizeof(_voltage));
    memset(_gps_samples, 0, gpsPhysicalCapacity() * sizeof(GpsSample));
    _tv_next = 0;
    _tv_count = 0;
    _gps_next = 0;
    _gps_count = 0;
    _last_bucket = 0;
    _has_bucket = false;
  }

  bool sampleDue(uint32_t epoch_seconds) const {
    return !_has_bucket || epoch_seconds / SAMPLE_INTERVAL_SECONDS != _last_bucket;
  }

  void record(uint32_t epoch_seconds, int16_t temperature_c, bool temperature_valid,
              uint16_t battery_mv,
              int32_t gps_lat_e7, int32_t gps_lon_e7, bool gps_valid) {
    const uint32_t bucket = epoch_seconds / SAMPLE_INTERVAL_SECONDS;
    uint8_t temperature_status;
    const uint8_t temperature = encodeTemperature(temperature_c, temperature_valid,
                                                   temperature_status);
    const uint8_t voltage = encodeVoltage(battery_mv);
    gps_valid = gps_valid
        && gps_lat_e7 >= -900000000 && gps_lat_e7 <= 900000000
        && gps_lon_e7 >= -1800000000 && gps_lon_e7 <= 1800000000
        && (gps_lat_e7 != 0 || gps_lon_e7 != 0);

    if (!_has_bucket) {
      append(temperature, temperature_status, voltage,
             gps_lat_e7, gps_lon_e7, gps_valid);
      _last_bucket = bucket;
      _has_bucket = true;
      return;
    }

    if (bucket == _last_bucket) return;

    const uint32_t maximum_retention = _gps_capacity > TV_RETENTION_SAMPLES
        ? _gps_capacity : TV_RETENTION_SAMPLES;
    if (bucket < _last_bucket || bucket - _last_bucket > maximum_retention) {
      clear();
      append(temperature, temperature_status, voltage,
             gps_lat_e7, gps_lon_e7, gps_valid);
      _last_bucket = bucket;
      _has_bucket = true;
      return;
    }

    const uint32_t skipped = bucket - _last_bucket - 1U;
    for (uint32_t i = 0; i < skipped; i++) {
      append(0, TEMPERATURE_NONE, 0, 0, 0, false);
    }
    append(temperature, temperature_status, voltage,
           gps_lat_e7, gps_lon_e7, gps_valid);
    _last_bucket = bucket;
  }

  // The seven-bit value is an exact whole-degree offset: 0=-50 C, 127=+77 C.
  // A separate two-bit status carries none/value/low/high because seven bits
  // alone cannot represent 128 temperatures plus three sentinel states.
  static uint8_t encodeTemperature(int16_t temperature_c, bool valid,
                                   uint8_t& status) {
    if (!valid) {
      status = TEMPERATURE_NONE;
      return 0;
    }
    if (temperature_c < -50) {
      status = TEMPERATURE_LOW;
      return 0;
    }
    if (temperature_c > 77) {
      status = TEMPERATURE_HIGH;
      return 127;
    }
    status = TEMPERATURE_VALUE;
    return (uint8_t)(temperature_c + 50);
  }

  // Voltage uses all 256 eight-bit codes:
  //   0       no reading
  //   1       below 1.88 V
  //   2..254  1.88 V through 4.40 V in 0.01 V steps
  //   255     above 4.40 V
  static uint8_t encodeVoltage(uint16_t battery_mv) {
    if (battery_mv == 0) return 0;
    if (battery_mv < 1880) return 1;
    if (battery_mv > 4400) return 255;
    return (uint8_t)(2U + (battery_mv - 1880U + 5U) / 10U);
  }

  uint8_t gpsRetentionDays() const {
    return (uint8_t)(_gps_capacity / SAMPLES_PER_DAY);
  }

  uint8_t gpsPageCount() const {
    return (uint8_t)(_gps_capacity / GPS_SAMPLES_PER_PAGE);
  }

  uint16_t voltageSampleCount() const {
    return _tv_count;
  }

  size_t formatTemperatureBinarySnapshot(
      const uint8_t source_id[BINARY_SOURCE_ID_SIZE],
      uint8_t* dest, size_t dest_size) const {
    return formatBinarySnapshot(false, source_id, dest, dest_size);
  }

  size_t formatVoltageBinarySnapshot(
      const uint8_t source_id[BINARY_SOURCE_ID_SIZE],
      uint8_t* dest, size_t dest_size) const {
    return formatBinarySnapshot(true, source_id, dest, dest_size);
  }

private:
  // Formats a RAW_CUSTOM payload without Base64. The source ID is normally
  // the first eight bytes of the repeater public key. Samples are oldest
  // first, and the payload contains as many as fit, capped at 165.
  size_t formatBinarySnapshot(
      bool voltage_series, const uint8_t source_id[BINARY_SOURCE_ID_SIZE],
      uint8_t* dest, size_t dest_size) const {
    if (source_id == NULL || dest == NULL
        || dest_size <= BINARY_HEADER_SIZE || !_has_bucket) {
      return 0;
    }

    size_t sample_count = dest_size - BINARY_HEADER_SIZE;
    if (sample_count > BINARY_MAX_SAMPLES) {
      sample_count = BINARY_MAX_SAMPLES;
    }
    if (sample_count > _tv_count) sample_count = _tv_count;
    if (sample_count == 0) return 0;

    memcpy(dest, voltage_series ? "TVB1" : "TTB1", 4);
    memcpy(&dest[4], source_id, BINARY_SOURCE_ID_SIZE);
    const uint16_t oldest_offset = (uint16_t)(sample_count - 1U);
    putUint32LE(&dest[12], startEpochForOffset(oldest_offset));
    putUint16LE(&dest[16], (uint16_t)(SAMPLE_INTERVAL_SECONDS / 60U));
    dest[18] = (uint8_t)sample_count;

    for (uint16_t slot = 0; slot < sample_count; slot++) {
      uint8_t temperature = 0;
      uint8_t temperature_status = TEMPERATURE_NONE;
      uint8_t voltage = 0;
      tvAtOffset((uint16_t)(oldest_offset - slot), temperature,
                 temperature_status, voltage);
      uint8_t value = voltage;
      if (!voltage_series) {
        if (temperature_status == TEMPERATURE_NONE) value = 0;
        else if (temperature_status == TEMPERATURE_LOW) value = 1;
        else if (temperature_status == TEMPERATURE_HIGH) value = 2;
        else value = (uint8_t)(temperature + 3U);
      }
      dest[BINARY_HEADER_SIZE + slot] = value;
    }
    return BINARY_HEADER_SIZE + sample_count;
  }

public:

  // Changes the logical GPS retention and preserves the newest samples. The
  // heap budget is the maximum additional allocation allowed for this call.
  // Requests above the budget are reduced one day at a time. The returned
  // value is the number of days actually available.
  uint8_t resizeGpsDays(uint8_t requested_days,
                        size_t max_additional_heap_bytes) {
    if (requested_days < 1U) requested_days = 1U;
    if (requested_days > GPS_MAX_RETENTION_DAYS) {
      requested_days = GPS_MAX_RETENTION_DAYS;
    }

    normalizeGpsRing();
    const uint8_t current_days = gpsRetentionDays();
    if (requested_days == current_days) return current_days;

    if (requested_days <= GPS_DEFAULT_RETENTION_DAYS) {
      resizeGpsToDefaultStorage(requested_days);
      return gpsRetentionDays();
    }

    if (_gps_allocated_capacity != 0
        && requested_days * SAMPLES_PER_DAY <= _gps_allocated_capacity) {
      resizeGpsLogicalCapacity((uint16_t)(requested_days * SAMPLES_PER_DAY));
      shrinkGpsAllocation();
      return gpsRetentionDays();
    }

    if (_gps_allocated_capacity == 0
        && _gps_capacity < GPS_RETENTION_SAMPLES) {
      resizeGpsLogicalCapacity(GPS_RETENTION_SAMPLES);
    }

    const uint16_t old_allocated_capacity = _gps_allocated_capacity;
    const size_t old_heap_bytes =
        (size_t)old_allocated_capacity * sizeof(GpsSample);
    for (uint8_t days = requested_days;
         days > gpsRetentionDays(); days--) {
      const uint16_t candidate_capacity = (uint16_t)(days * SAMPLES_PER_DAY);
      const size_t candidate_bytes =
          (size_t)candidate_capacity * sizeof(GpsSample);
      const size_t additional_bytes = candidate_bytes > old_heap_bytes
          ? candidate_bytes - old_heap_bytes : 0;
      if (additional_bytes > max_additional_heap_bytes) continue;
      if (expandGpsStorage(candidate_capacity)) return days;
    }
    return gpsRetentionDays();
  }

  // Formats a complete CLI reply in at most 139 bytes, including NUL:
  //   get telemetry.temp [page]  (page 1..7)
  //   get telemetry.volt [page]  (page 1..7)
  //   get telemetry.gps [page]   (page 1..configured days * 2)
  // Page 1 is always the newest page. Temperature and voltage pages hold one
  // day; GPS pages hold half a day.
  // The reply is "> " followed by standard padded Base64.
  bool formatPageReply(Series series, const char* args,
                       char* reply, size_t reply_size) const {
    if (reply == NULL || reply_size == 0) return false;
    reply[0] = 0;
    if (!_has_bucket) {
      copyReply(reply, reply_size, "Err - telemetry history is empty");
      return false;
    }

    const char* cursor = skipSpaces(args == NULL ? "" : args);
    char token[12];
    unsigned page = 1;

    if (readToken(cursor, token, sizeof(token))) {
      if (!parseUnsigned(token, page)) return formatUsage(series, reply, reply_size);
    }

    cursor = skipSpaces(cursor);
    if (*cursor != 0) return formatUsage(series, reply, reply_size);

#if !defined(MESH_ENABLE_TELEMETRY_GPS_HISTORY) \
    || MESH_ENABLE_TELEMETRY_GPS_HISTORY
    if (series == SERIES_GPS) {
      if (page < 1U || page > gpsPageCount()) {
        snprintf(reply, reply_size, "Err - telemetry.gps page must be 1-%u",
                 (unsigned)gpsPageCount());
        return false;
      }
      return formatGpsPage((uint8_t)page, reply, reply_size);
    }
#endif

    if (page < 1U || page > TV_PAGE_COUNT) {
      copyReply(reply, reply_size, series == SERIES_TEMPERATURE
          ? "Err - telemetry.temp page must be 1-7"
          : "Err - telemetry.volt page must be 1-7");
      return false;
    }
    return series == SERIES_TEMPERATURE
        ? formatTemperaturePage((uint8_t)page, reply, reply_size)
        : formatVoltagePage((uint8_t)page, reply, reply_size);
  }

private:
  class BitWriter {
    uint8_t* _dest;
    size_t _capacity;
    size_t _bits;

  public:
    BitWriter(uint8_t* dest, size_t capacity)
        : _dest(dest), _capacity(capacity), _bits(0) {
      memset(dest, 0, capacity);
    }

    bool write(uint32_t value, uint8_t width) {
      if (_bits + width > _capacity * 8U) return false;
      for (int bit = width - 1; bit >= 0; bit--) {
        if (value & (1UL << bit)) {
          _dest[_bits / 8U] |= (uint8_t)(1U << (7U - (_bits % 8U)));
        }
        _bits++;
      }
      return true;
    }
  };

  uint8_t _temperature[TV_RETENTION_SAMPLES];
  uint8_t _temperature_status[(TV_RETENTION_SAMPLES * 2U + 7U) / 8U];
  uint8_t _voltage[TV_RETENTION_SAMPLES];
  GpsSample _gps_default_samples[GPS_RETENTION_SAMPLES];
  GpsSample* _gps_samples;
  uint16_t _gps_capacity;
  uint16_t _gps_allocated_capacity;
  uint16_t _tv_next;
  uint16_t _tv_count;
  uint16_t _gps_next;
  uint16_t _gps_count;
  uint32_t _last_bucket;
  bool _has_bucket;

  static const char* skipSpaces(const char* value) {
    while (*value == ' ') value++;
    return value;
  }

  static bool readToken(const char*& cursor, char* token, size_t token_size) {
    cursor = skipSpaces(cursor);
    if (*cursor == 0) return false;

    size_t len = 0;
    while (*cursor != 0 && *cursor != ' ') {
      if (len + 1U >= token_size) {
        while (*cursor != 0 && *cursor != ' ') cursor++;
        token[0] = 0;
        return true;
      }
      token[len++] = *cursor++;
    }
    token[len] = 0;
    return true;
  }

  static bool parseUnsigned(const char* token, unsigned& value) {
    if (token == NULL || *token == 0) return false;
    unsigned parsed = 0;
    while (*token != 0) {
      if (*token < '0' || *token > '9') return false;
      if (parsed > 1000U) return false;
      parsed = parsed * 10U + (unsigned)(*token++ - '0');
    }
    value = parsed;
    return true;
  }

  static void copyReply(char* reply, size_t reply_size, const char* text) {
    if (reply_size == 0) return;
    snprintf(reply, reply_size, "%s", text);
  }

  static bool formatUsage(Series series, char* reply, size_t reply_size) {
    const char* command = series == SERIES_TEMPERATURE ? "telemetry.temp"
        : series == SERIES_VOLTAGE ? "telemetry.volt" : "telemetry.gps";
    snprintf(reply, reply_size, "Err - use: get %s [page]", command);
    return false;
  }

  uint16_t gpsPhysicalCapacity() const {
    return _gps_allocated_capacity != 0
        ? _gps_allocated_capacity : GPS_RETENTION_SAMPLES;
  }

  static void reverseGpsSamples(GpsSample* samples,
                                uint16_t begin, uint16_t end) {
    while (begin < end && begin < --end) {
      const GpsSample saved = samples[begin];
      samples[begin++] = samples[end];
      samples[end] = saved;
    }
  }

  void normalizeGpsRing() {
    if (_gps_count == 0) {
      _gps_next = 0;
      return;
    }

    const uint16_t oldest = (uint16_t)(
        (_gps_next + _gps_capacity - _gps_count) % _gps_capacity);
    if (oldest != 0) {
      reverseGpsSamples(_gps_samples, 0, oldest);
      reverseGpsSamples(_gps_samples, oldest, _gps_capacity);
      reverseGpsSamples(_gps_samples, 0, _gps_capacity);
    }
    _gps_next = _gps_count == _gps_capacity ? 0 : _gps_count;
  }

  void resizeGpsLogicalCapacity(uint16_t new_capacity) {
    const uint16_t keep_count = _gps_count < new_capacity
        ? _gps_count : new_capacity;
    if (keep_count < _gps_count) {
      memmove(_gps_samples, &_gps_samples[_gps_count - keep_count],
              (size_t)keep_count * sizeof(GpsSample));
    }
    _gps_capacity = new_capacity;
    _gps_count = keep_count;
    _gps_next = keep_count == new_capacity ? 0 : keep_count;
  }

  void resizeGpsToDefaultStorage(uint8_t days) {
    const uint16_t new_capacity = (uint16_t)(days * SAMPLES_PER_DAY);
    if (_gps_allocated_capacity == 0) {
      resizeGpsLogicalCapacity(new_capacity);
      return;
    }

    const uint16_t keep_count = _gps_count < new_capacity
        ? _gps_count : new_capacity;
    memset(_gps_default_samples, 0, sizeof(_gps_default_samples));
    memcpy(_gps_default_samples, &_gps_samples[_gps_count - keep_count],
           (size_t)keep_count * sizeof(GpsSample));
    free(_gps_samples);
    _gps_samples = _gps_default_samples;
    _gps_allocated_capacity = 0;
    _gps_capacity = new_capacity;
    _gps_count = keep_count;
    _gps_next = keep_count == new_capacity ? 0 : keep_count;
  }

  void shrinkGpsAllocation() {
    if (_gps_allocated_capacity == 0
        || _gps_allocated_capacity == _gps_capacity) return;
    GpsSample* resized = static_cast<GpsSample*>(
        realloc(_gps_samples, (size_t)_gps_capacity * sizeof(GpsSample)));
    if (resized != NULL) {
      _gps_samples = resized;
      _gps_allocated_capacity = _gps_capacity;
    }
  }

  bool expandGpsStorage(uint16_t new_capacity) {
    GpsSample* resized;
    if (_gps_allocated_capacity == 0) {
      resized = static_cast<GpsSample*>(
          malloc((size_t)new_capacity * sizeof(GpsSample)));
      if (resized == NULL) return false;
      memset(resized, 0, (size_t)new_capacity * sizeof(GpsSample));
      memcpy(resized, _gps_samples, (size_t)_gps_count * sizeof(GpsSample));
    } else {
      resized = static_cast<GpsSample*>(
          realloc(_gps_samples, (size_t)new_capacity * sizeof(GpsSample)));
      if (resized == NULL) return false;
    }

    _gps_samples = resized;
    _gps_capacity = new_capacity;
    _gps_allocated_capacity = new_capacity;
    _gps_next = _gps_count == new_capacity ? 0 : _gps_count;
    return true;
  }

  void setTemperatureStatus(uint16_t index, uint8_t status) {
    const uint8_t shift = (uint8_t)((index & 3U) * 2U);
    const uint8_t mask = (uint8_t)(0x03U << shift);
    _temperature_status[index / 4U] = (uint8_t)(
        (_temperature_status[index / 4U] & (uint8_t)~mask)
        | ((status & 0x03U) << shift));
  }

  uint8_t temperatureStatus(uint16_t index) const {
    const uint8_t shift = (uint8_t)((index & 3U) * 2U);
    return (uint8_t)((_temperature_status[index / 4U] >> shift) & 0x03U);
  }

  void append(uint8_t temperature, uint8_t temperature_status, uint8_t voltage,
              int32_t gps_lat_e7,
              int32_t gps_lon_e7, bool gps_valid) {
    _temperature[_tv_next] = temperature;
    setTemperatureStatus(_tv_next, temperature_status);
    _voltage[_tv_next] = voltage;
    _tv_next = (uint16_t)((_tv_next + 1U) % TV_RETENTION_SAMPLES);
    if (_tv_count < TV_RETENTION_SAMPLES) _tv_count++;

    _gps_samples[_gps_next].latitude_e7 = gps_valid ? gps_lat_e7 : 0;
    _gps_samples[_gps_next].longitude_e7 = gps_valid ? gps_lon_e7 : 0;
    _gps_next = (uint16_t)((_gps_next + 1U) % _gps_capacity);
    if (_gps_count < _gps_capacity) _gps_count++;
  }

  bool tvAtOffset(uint16_t offset, uint8_t& temperature,
                  uint8_t& temperature_status, uint8_t& voltage) const {
    if (offset >= _tv_count) {
      temperature = 0;
      temperature_status = TEMPERATURE_NONE;
      voltage = 0;
      return false;
    }
    const uint16_t index = (uint16_t)((_tv_next + TV_RETENTION_SAMPLES - 1U - offset)
                                      % TV_RETENTION_SAMPLES);
    temperature = _temperature[index];
    temperature_status = temperatureStatus(index);
    voltage = _voltage[index];
    return true;
  }

  bool gpsAtOffset(uint16_t offset, int32_t& latitude_e7, int32_t& longitude_e7) const {
    if (offset >= _gps_count) {
      latitude_e7 = 0;
      longitude_e7 = 0;
      return false;
    }
    const uint16_t index = (uint16_t)((_gps_next + _gps_capacity - 1U - offset)
                                      % _gps_capacity);
    latitude_e7 = _gps_samples[index].latitude_e7;
    longitude_e7 = _gps_samples[index].longitude_e7;
    return latitude_e7 != 0 || longitude_e7 != 0;
  }

  static void putUint32LE(uint8_t* dest, uint32_t value) {
    dest[0] = (uint8_t)value;
    dest[1] = (uint8_t)(value >> 8);
    dest[2] = (uint8_t)(value >> 16);
    dest[3] = (uint8_t)(value >> 24);
  }

  static void putUint16LE(uint8_t* dest, uint16_t value) {
    dest[0] = (uint8_t)value;
    dest[1] = (uint8_t)(value >> 8);
  }

  static void putInt32LE(uint8_t* dest, int32_t value) {
    putUint32LE(dest, (uint32_t)value);
  }

  static size_t base64Encode(const uint8_t* source, size_t source_len,
                             char* dest, size_t dest_size) {
    static const char alphabet[] =
        "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
    const size_t required = 4U * ((source_len + 2U) / 3U);
    if (dest_size <= required) return 0;

    size_t in = 0;
    size_t out = 0;
    while (in < source_len) {
      const size_t remaining = source_len - in;
      const uint32_t a = source[in++];
      const uint32_t b = remaining > 1U ? source[in++] : 0U;
      const uint32_t c = remaining > 2U ? source[in++] : 0U;
      const uint32_t value = (a << 16) | (b << 8) | c;

      dest[out++] = alphabet[(value >> 18) & 0x3FU];
      dest[out++] = alphabet[(value >> 12) & 0x3FU];
      dest[out++] = remaining > 1U ? alphabet[(value >> 6) & 0x3FU] : '=';
      dest[out++] = remaining > 2U ? alphabet[value & 0x3FU] : '=';
    }
    dest[out] = 0;
    return out;
  }

  static bool formatBase64Reply(const uint8_t* payload, size_t payload_len,
                                char* reply, size_t reply_size) {
    if (reply_size < 3U) return false;
    reply[0] = '>';
    reply[1] = ' ';
    if (base64Encode(payload, payload_len, &reply[2], reply_size - 2U) == 0) {
      copyReply(reply, reply_size, "Err - telemetry reply buffer too small");
      return false;
    }
    return true;
  }

  uint32_t startEpochForOffset(uint16_t oldest_offset) const {
    const uint32_t start_bucket = _last_bucket >= oldest_offset
        ? _last_bucket - oldest_offset : 0;
    return start_bucket * SAMPLE_INTERVAL_SECONDS;
  }

  bool formatTemperaturePage(uint8_t page, char* reply, size_t reply_size) const {
    static constexpr size_t HEADER_SIZE = 7;
    static constexpr size_t STATUS_SIZE = (SAMPLES_PER_DAY * 2U) / 8U;
    static constexpr size_t DATA_SIZE = (SAMPLES_PER_DAY * 7U) / 8U;
    uint8_t payload[HEADER_SIZE + STATUS_SIZE + DATA_SIZE];
    memset(payload, 0, sizeof(payload));

    const uint16_t oldest_offset = (uint16_t)(page * SAMPLES_PER_DAY - 1U);
    payload[0] = TEMPERATURE_PAYLOAD_TYPE_V1;
    putUint32LE(&payload[1], startEpochForOffset(oldest_offset));
    payload[5] = 30;
    payload[6] = SAMPLES_PER_DAY;

    BitWriter status_writer(&payload[HEADER_SIZE], STATUS_SIZE);
    BitWriter writer(&payload[HEADER_SIZE + STATUS_SIZE], DATA_SIZE);
    for (uint8_t slot = 0; slot < SAMPLES_PER_DAY; slot++) {
      uint8_t temperature = 0;
      uint8_t temperature_status = TEMPERATURE_NONE;
      uint8_t voltage = 0;
      tvAtOffset((uint16_t)(oldest_offset - slot), temperature,
                 temperature_status, voltage);
      status_writer.write(temperature_status, 2);
      writer.write(temperature, 7);
    }
    return formatBase64Reply(payload, sizeof(payload), reply, reply_size);
  }

  bool formatVoltagePage(uint8_t page, char* reply, size_t reply_size) const {
    static constexpr size_t HEADER_SIZE = 7;
    uint8_t payload[HEADER_SIZE + SAMPLES_PER_DAY];
    memset(payload, 0, sizeof(payload));

    const uint16_t oldest_offset = (uint16_t)(page * SAMPLES_PER_DAY - 1U);
    payload[0] = VOLTAGE_PAYLOAD_TYPE_V1;
    putUint32LE(&payload[1], startEpochForOffset(oldest_offset));
    payload[5] = 30;
    payload[6] = SAMPLES_PER_DAY;

    for (uint8_t slot = 0; slot < SAMPLES_PER_DAY; slot++) {
      uint8_t temperature = 0;
      uint8_t temperature_status = TEMPERATURE_NONE;
      uint8_t voltage = 0;
      tvAtOffset((uint16_t)(oldest_offset - slot), temperature,
                 temperature_status, voltage);
      payload[HEADER_SIZE + slot] = voltage;
    }
    return formatBase64Reply(payload, sizeof(payload), reply, reply_size);
  }

  static int clampGpsDelta(long value, bool& clipped) {
    if (value < -8192L) {
      clipped = true;
      return -8192;
    }
    if (value > 8191L) {
      clipped = true;
      return 8191;
    }
    return (int)value;
  }

  bool formatGpsPage(uint8_t page, char* reply, size_t reply_size) const {
    static constexpr size_t HEADER_SIZE = 17;
    static constexpr size_t DATA_SIZE = (GPS_SAMPLES_PER_PAGE * 28U) / 8U;
    static constexpr double METERS_PER_DEGREE = 111320.0;
    static constexpr double DEGREES_TO_RADIANS = 0.017453292519943295;
    uint8_t payload[HEADER_SIZE + DATA_SIZE];
    memset(payload, 0, sizeof(payload));

    const uint16_t newest_offset = (uint16_t)((page - 1U) * GPS_SAMPLES_PER_PAGE);
    const uint16_t oldest_offset = (uint16_t)(newest_offset
                                               + GPS_SAMPLES_PER_PAGE - 1U);

    payload[0] = GPS_PAYLOAD_TYPE_V1;
    putUint32LE(&payload[1], startEpochForOffset(oldest_offset));
    payload[5] = 30;
    payload[6] = GPS_SAMPLES_PER_PAGE;
    payload[15] = 0xFF;  // origin slot; 0xFF means no GPS fix on this page

    int32_t origin_lat_e7 = 0;
    int32_t origin_lon_e7 = 0;
    for (uint8_t slot = 0; slot < GPS_SAMPLES_PER_PAGE; slot++) {
      int32_t latitude_e7;
      int32_t longitude_e7;
      if (gpsAtOffset((uint16_t)(oldest_offset - slot), latitude_e7, longitude_e7)) {
        origin_lat_e7 = latitude_e7;
        origin_lon_e7 = longitude_e7;
        payload[15] = slot;
        break;
      }
    }
    putInt32LE(&payload[7], origin_lat_e7);
    putInt32LE(&payload[11], origin_lon_e7);

    double reference_lat = (double)origin_lat_e7 / 10000000.0;
    double reference_lon = (double)origin_lon_e7 / 10000000.0;
    bool clipped = false;
    BitWriter writer(&payload[HEADER_SIZE], DATA_SIZE);
    for (uint8_t slot = 0; slot < GPS_SAMPLES_PER_PAGE; slot++) {
      int north_units = 0;
      int east_units = 0;
      int32_t latitude_e7;
      int32_t longitude_e7;
      const bool valid = payload[15] != 0xFF && slot >= payload[15]
          && gpsAtOffset((uint16_t)(oldest_offset - slot), latitude_e7, longitude_e7);

      if (valid && slot != payload[15]) {
        const double latitude = (double)latitude_e7 / 10000000.0;
        const double longitude = (double)longitude_e7 / 10000000.0;
        const double mean_latitude = (latitude + reference_lat) * 0.5;
        const double north_m = (latitude - reference_lat) * METERS_PER_DEGREE;
        const double east_m = (longitude - reference_lon) * METERS_PER_DEGREE
            * cos(mean_latitude * DEGREES_TO_RADIANS);
        north_units = clampGpsDelta(lround(north_m / 10.0), clipped);
        east_units = clampGpsDelta(lround(east_m / 10.0), clipped);

        reference_lat += (double)north_units * 10.0 / METERS_PER_DEGREE;
        const double longitude_scale = METERS_PER_DEGREE
            * cos(reference_lat * DEGREES_TO_RADIANS);
        if (fabs(longitude_scale) > 0.001) {
          reference_lon += (double)east_units * 10.0 / longitude_scale;
        }
      }

      writer.write((uint16_t)north_units & 0x3FFFU, 14);
      writer.write((uint16_t)east_units & 0x3FFFU, 14);
    }
    if (clipped) payload[16] |= 0x01;
    return formatBase64Reply(payload, sizeof(payload), reply, reply_size);
  }
};

}  // namespace mesh
