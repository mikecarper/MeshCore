#pragma once

#include <math.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

namespace mesh {

// Boot-local history for external voltage monitors. Each configured LPP
// channel owns four days of half-hour samples. Codes are packed at 15 bits:
// zero means missing and 1..32767 represent 0.02..655.34 V.
class ExternalVoltageHistory {
public:
  struct Reading {
    uint8_t channel;
    float voltage;
    bool valid;
  };

  static constexpr uint32_t SAMPLE_INTERVAL_SECONDS = 30UL * 60UL;
  static constexpr uint8_t SAMPLES_PER_DAY = 48;
  static constexpr uint8_t RETENTION_DAYS = 4;
  static constexpr uint16_t RETENTION_SAMPLES =
      RETENTION_DAYS * SAMPLES_PER_DAY;
  static constexpr uint8_t SAMPLE_BITS = 15;
  static constexpr uint16_t MAX_CODE = 0x7FFF;
  static constexpr uint32_t STEP_MILLIVOLTS = 20;
  static constexpr uint32_t MAX_MILLIVOLTS =
      (uint32_t)MAX_CODE * STEP_MILLIVOLTS;
  static constexpr uint8_t MAX_CHANNELS = 16;
  static constexpr size_t BYTES_PER_CHANNEL =
      (RETENTION_SAMPLES * SAMPLE_BITS + 7U) / 8U;

  static constexpr uint8_t PAGE_PAYLOAD_TYPE_V1 = 0x14;
  static constexpr uint8_t PAGE_COUNT = RETENTION_DAYS;
  static constexpr size_t PAGE_HEADER_SIZE = 8;
  static constexpr size_t PAGE_DATA_SIZE =
      (SAMPLES_PER_DAY * SAMPLE_BITS + 7U) / 8U;

  static constexpr size_t BINARY_SOURCE_ID_SIZE = 8;
  static constexpr size_t BINARY_HEADER_SIZE = 20;
  static constexpr uint8_t BINARY_SAMPLES_PER_CHUNK = 64;
  static constexpr size_t BINARY_DATA_SIZE =
      (BINARY_SAMPLES_PER_CHUNK * SAMPLE_BITS + 7U) / 8U;
  static constexpr size_t BINARY_PAYLOAD_SIZE =
      BINARY_HEADER_SIZE + BINARY_DATA_SIZE;

  ExternalVoltageHistory()
      : _samples(NULL), _channel_count(0), _next(0), _count(0),
        _last_bucket(0), _has_bucket(false) {
    memset(_channels, 0, sizeof(_channels));
  }

  ~ExternalVoltageHistory() {
    free(_samples);
  }

  ExternalVoltageHistory(const ExternalVoltageHistory&) = delete;
  ExternalVoltageHistory& operator=(const ExternalVoltageHistory&) = delete;

  bool configure(const uint8_t channels[], uint8_t count) {
    uint8_t unique[MAX_CHANNELS];
    uint8_t unique_count = 0;
    if (channels != NULL) {
      for (uint8_t i = 0; i < count && unique_count < MAX_CHANNELS; i++) {
        if (channels[i] == 0) continue;
        bool duplicate = false;
        for (uint8_t j = 0; j < unique_count; j++) {
          if (unique[j] == channels[i]) {
            duplicate = true;
            break;
          }
        }
        if (!duplicate) unique[unique_count++] = channels[i];
      }
    }

    if (unique_count == 0) {
      free(_samples);
      _samples = NULL;
      _channel_count = 0;
      memset(_channels, 0, sizeof(_channels));
      clear();
      return true;
    }

    const size_t bytes = (size_t)unique_count * BYTES_PER_CHANNEL;
    uint8_t* replacement = static_cast<uint8_t*>(malloc(bytes));
    if (replacement == NULL) return false;
    memset(replacement, 0, bytes);

    free(_samples);
    _samples = replacement;
    _channel_count = unique_count;
    memset(_channels, 0, sizeof(_channels));
    memcpy(_channels, unique, (size_t)unique_count * sizeof(unique[0]));
    _next = 0;
    _count = 0;
    _last_bucket = 0;
    _has_bucket = false;
    return true;
  }

  void clear() {
    if (_samples != NULL) {
      memset(_samples, 0, (size_t)_channel_count * BYTES_PER_CHANNEL);
    }
    _next = 0;
    _count = 0;
    _last_bucket = 0;
    _has_bucket = false;
  }

  uint8_t channelCount() const { return _channel_count; }
  uint8_t channelAt(uint8_t index) const {
    return index < _channel_count ? _channels[index] : 0;
  }
  uint8_t populatedChannelCount() const {
    uint8_t count = 0;
    for (uint8_t i = 0; i < _channel_count; i++) {
      if (channelIndexHasValue(i)) count++;
    }
    return count;
  }
  uint8_t populatedChannelAt(uint8_t index) const {
    for (uint8_t i = 0; i < _channel_count; i++) {
      if (!channelIndexHasValue(i)) continue;
      if (index == 0) return _channels[i];
      index--;
    }
    return 0;
  }
  bool channelHasValue(uint8_t channel) const {
    const int channel_index = findChannel(channel);
    return channel_index >= 0
        && channelIndexHasValue((uint8_t)channel_index);
  }
  uint16_t sampleCount() const { return _count; }
  size_t storageBytes() const {
    return (size_t)_channel_count * BYTES_PER_CHANNEL;
  }

  bool sampleDue(uint32_t epoch_seconds) const {
    return !_has_bucket
        || epoch_seconds / SAMPLE_INTERVAL_SECONDS != _last_bucket;
  }

  static uint16_t encodeVoltage(float voltage, bool valid = true) {
    if (!valid || !isfinite(voltage) || voltage <= 0.0f) return 0;
    const double scaled = (double)voltage * 1000.0
        / (double)STEP_MILLIVOLTS;
    if (scaled >= MAX_CODE) return MAX_CODE;
    long code = lround(scaled);
    if (code < 1) code = 1;
    return (uint16_t)code;
  }

  static uint32_t decodeMillivolts(uint16_t code) {
    return code == 0 ? 0 : (uint32_t)code * STEP_MILLIVOLTS;
  }

  void record(uint32_t epoch_seconds, const Reading readings[],
              uint8_t reading_count) {
    if (_samples == NULL || _channel_count == 0) return;
    const uint32_t bucket = epoch_seconds / SAMPLE_INTERVAL_SECONDS;
    if (!_has_bucket) {
      append(readings, reading_count);
      _last_bucket = bucket;
      _has_bucket = true;
      return;
    }
    if (bucket == _last_bucket) return;
    if (bucket < _last_bucket
        || bucket - _last_bucket > RETENTION_SAMPLES) {
      clear();
      append(readings, reading_count);
      _last_bucket = bucket;
      _has_bucket = true;
      return;
    }

    const uint32_t skipped = bucket - _last_bucket - 1U;
    for (uint32_t i = 0; i < skipped; i++) append(NULL, 0);
    append(readings, reading_count);
    _last_bucket = bucket;
  }

  uint8_t binaryChunkCount() const {
    return (uint8_t)((_count + BINARY_SAMPLES_PER_CHUNK - 1U)
                     / BINARY_SAMPLES_PER_CHUNK);
  }

  size_t formatBinarySnapshot(
      const uint8_t source_id[BINARY_SOURCE_ID_SIZE], uint8_t channel,
      uint8_t chunk_index, uint8_t* dest, size_t dest_size) const {
    const int channel_index = findChannel(channel);
    const uint8_t chunk_count = binaryChunkCount();
    if (source_id == NULL || dest == NULL || channel_index < 0
        || !channelIndexHasValue((uint8_t)channel_index)
        || chunk_index >= chunk_count) {
      return 0;
    }

    const uint16_t chronological_start =
        (uint16_t)(chunk_index * BINARY_SAMPLES_PER_CHUNK);
    uint16_t sample_count = (uint16_t)(_count - chronological_start);
    if (sample_count > BINARY_SAMPLES_PER_CHUNK) {
      sample_count = BINARY_SAMPLES_PER_CHUNK;
    }
    const size_t data_size =
        (sample_count * SAMPLE_BITS + 7U) / 8U;
    const size_t payload_size = BINARY_HEADER_SIZE + data_size;
    if (dest_size < payload_size) return 0;
    memset(dest, 0, payload_size);

    memcpy(dest, "IVB1", 4);
    memcpy(&dest[4], source_id, BINARY_SOURCE_ID_SIZE);
    const uint16_t oldest_offset =
        (uint16_t)(_count - 1U - chronological_start);
    putUint32LE(&dest[12], startEpochForOffset(oldest_offset));
    putUint16LE(&dest[16],
                (uint16_t)(SAMPLE_INTERVAL_SECONDS / 60U));
    dest[18] = channel;
    dest[19] = (uint8_t)sample_count;

    BitWriter writer(&dest[BINARY_HEADER_SIZE], data_size);
    for (uint16_t slot = 0; slot < sample_count; slot++) {
      writer.write(codeAtOffset(
          (uint8_t)channel_index, (uint16_t)(oldest_offset - slot)));
    }
    return payload_size;
  }

  bool formatPageReply(const char* args, char* reply,
                       size_t reply_size) const {
    if (reply == NULL || reply_size == 0) return false;
    reply[0] = 0;
    if (_channel_count == 0) {
      copyReply(reply, reply_size, "Err - no I2C voltage sensors");
      return false;
    }
    if (!_has_bucket) {
      copyReply(reply, reply_size, "Err - I2C voltage history is empty");
      return false;
    }
    if (populatedChannelCount() == 0) {
      copyReply(reply, reply_size,
                "Err - no connected I2C voltage channels");
      return false;
    }

    const char* cursor = skipSpaces(args == NULL ? "" : args);
    if (*cursor == 0) return formatStatusReply(reply, reply_size);

    char token[12];
    unsigned channel = 0;
    unsigned page = 1;
    if (!readToken(cursor, token, sizeof(token))
        || !parseUnsigned(token, channel) || channel > 255U) {
      return formatUsage(reply, reply_size);
    }
    if (readToken(cursor, token, sizeof(token))) {
      if (!parseUnsigned(token, page)) {
        return formatUsage(reply, reply_size);
      }
    }
    cursor = skipSpaces(cursor);
    if (*cursor != 0) return formatUsage(reply, reply_size);
    const int channel_index = findChannel((uint8_t)channel);
    if (channel_index < 0
        || !channelIndexHasValue((uint8_t)channel_index)) {
      snprintf(reply, reply_size,
               "Err - I2C voltage channel %u has no readings", channel);
      return false;
    }
    if (page < 1U || page > PAGE_COUNT) {
      copyReply(reply, reply_size,
                "Err - telemetry.volt.i2c page must be 1-4");
      return false;
    }
    return formatPage((uint8_t)channel_index, (uint8_t)page,
                      reply, reply_size);
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

    bool write(uint16_t value) {
      if (_bits + SAMPLE_BITS > _capacity * 8U) return false;
      for (int bit = SAMPLE_BITS - 1; bit >= 0; bit--) {
        if ((value & ((uint16_t)1U << bit)) != 0) {
          _dest[_bits / 8U] |=
              (uint8_t)(1U << (7U - (_bits % 8U)));
        }
        _bits++;
      }
      return true;
    }
  };

  uint8_t* _samples;
  uint8_t _channels[MAX_CHANNELS];
  uint8_t _channel_count;
  uint16_t _next;
  uint16_t _count;
  uint32_t _last_bucket;
  bool _has_bucket;

  int findChannel(uint8_t channel) const {
    for (uint8_t i = 0; i < _channel_count; i++) {
      if (_channels[i] == channel) return i;
    }
    return -1;
  }

  bool channelIndexHasValue(uint8_t channel_index) const {
    for (uint16_t offset = 0; offset < _count; offset++) {
      if (codeAtOffset(channel_index, offset) != 0) return true;
    }
    return false;
  }

  size_t bitOffset(uint8_t channel_index, uint16_t sample_index) const {
    return ((size_t)channel_index * RETENTION_SAMPLES + sample_index)
        * SAMPLE_BITS;
  }

  void setCode(uint8_t channel_index, uint16_t sample_index,
               uint16_t value) {
    const size_t first_bit = bitOffset(channel_index, sample_index);
    for (uint8_t bit = 0; bit < SAMPLE_BITS; bit++) {
      const size_t absolute = first_bit + bit;
      const uint8_t mask = (uint8_t)(1U << (7U - absolute % 8U));
      if ((value & ((uint16_t)1U << (SAMPLE_BITS - 1U - bit))) != 0) {
        _samples[absolute / 8U] |= mask;
      } else {
        _samples[absolute / 8U] &= (uint8_t)~mask;
      }
    }
  }

  uint16_t codeAt(uint8_t channel_index, uint16_t sample_index) const {
    const size_t first_bit = bitOffset(channel_index, sample_index);
    uint16_t value = 0;
    for (uint8_t bit = 0; bit < SAMPLE_BITS; bit++) {
      const size_t absolute = first_bit + bit;
      value = (uint16_t)((value << 1U)
          | ((_samples[absolute / 8U] >> (7U - absolute % 8U)) & 1U));
    }
    return value;
  }

  uint16_t codeAtOffset(uint8_t channel_index, uint16_t offset) const {
    if (offset >= _count) return 0;
    const uint16_t index = (uint16_t)(
        (_next + RETENTION_SAMPLES - 1U - offset) % RETENTION_SAMPLES);
    return codeAt(channel_index, index);
  }

  void append(const Reading readings[], uint8_t reading_count) {
    if (readings == NULL) reading_count = 0;
    for (uint8_t channel_index = 0;
         channel_index < _channel_count; channel_index++) {
      uint16_t code = 0;
      for (uint8_t reading = 0; reading < reading_count; reading++) {
        if (readings[reading].channel == _channels[channel_index]) {
          code = encodeVoltage(readings[reading].voltage,
                               readings[reading].valid);
          break;
        }
      }
      setCode(channel_index, _next, code);
    }
    _next = (uint16_t)((_next + 1U) % RETENTION_SAMPLES);
    if (_count < RETENTION_SAMPLES) _count++;
  }

  uint32_t startEpochForOffset(uint16_t oldest_offset) const {
    const uint32_t start_bucket = _last_bucket >= oldest_offset
        ? _last_bucket - oldest_offset : 0;
    return start_bucket * SAMPLE_INTERVAL_SECONDS;
  }

  bool formatPage(uint8_t channel_index, uint8_t page,
                  char* reply, size_t reply_size) const {
    uint8_t payload[PAGE_HEADER_SIZE + PAGE_DATA_SIZE];
    memset(payload, 0, sizeof(payload));
    const uint16_t oldest_offset =
        (uint16_t)(page * SAMPLES_PER_DAY - 1U);
    payload[0] = PAGE_PAYLOAD_TYPE_V1;
    putUint32LE(&payload[1], startEpochForOffset(oldest_offset));
    payload[5] = (uint8_t)(SAMPLE_INTERVAL_SECONDS / 60U);
    payload[6] = SAMPLES_PER_DAY;
    payload[7] = _channels[channel_index];
    BitWriter writer(&payload[PAGE_HEADER_SIZE], PAGE_DATA_SIZE);
    for (uint8_t slot = 0; slot < SAMPLES_PER_DAY; slot++) {
      writer.write(codeAtOffset(
          channel_index, (uint16_t)(oldest_offset - slot)));
    }
    return formatBase64Reply(payload, sizeof(payload), reply, reply_size);
  }

  bool formatStatusReply(char* reply, size_t reply_size) const {
    size_t used = (size_t)snprintf(reply, reply_size, "> channels=");
    bool first = true;
    for (uint8_t i = 0; i < _channel_count && used < reply_size; i++) {
      if (!channelIndexHasValue(i)) continue;
      const int written = snprintf(reply + used, reply_size - used,
                                   "%s%u", first ? "" : ",",
                                   (unsigned)_channels[i]);
      if (written < 0) return false;
      used += (size_t)written;
      first = false;
    }
    if (used < reply_size) {
      snprintf(reply + used, reply_size - used,
               " samples=%u retention=4d step=0.02V max=655.34V",
               (unsigned)_count);
    }
    return true;
  }

  static const char* skipSpaces(const char* value) {
    while (*value == ' ') value++;
    return value;
  }

  static bool readToken(const char*& cursor, char* token,
                        size_t token_size) {
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
      if (*token < '0' || *token > '9' || parsed > 1000U) return false;
      parsed = parsed * 10U + (unsigned)(*token++ - '0');
    }
    value = parsed;
    return true;
  }

  static void copyReply(char* reply, size_t reply_size, const char* text) {
    if (reply_size == 0) return;
    snprintf(reply, reply_size, "%s", text);
  }

  static bool formatUsage(char* reply, size_t reply_size) {
    copyReply(reply, reply_size,
              "Err - use: get telemetry.volt.i2c <channel> [page]");
    return false;
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

  static size_t base64Encode(const uint8_t* source, size_t source_len,
                             char* dest, size_t dest_size) {
    static const char alphabet[] =
        "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
    const size_t output_len = ((source_len + 2U) / 3U) * 4U;
    if (dest_size <= output_len) return 0;
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
    if (base64Encode(payload, payload_len, &reply[2],
                     reply_size - 2U) == 0) {
      copyReply(reply, reply_size,
                "Err - telemetry reply buffer too small");
      return false;
    }
    return true;
  }
};

}  // namespace mesh
