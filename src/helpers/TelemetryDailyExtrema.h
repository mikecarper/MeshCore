#pragma once

#include <stddef.h>
#include <stdint.h>
#include <string.h>

namespace mesh {

// Versioned one-series daily extrema reply, carried as padded Base64 text.
// Wire v1 is defined here and in the telemetry documentation. Header bytes:
//  0: 0x17; 1: version 1; 2: Series; 3: reserved (0);
//  4: sample interval in minutes; 5..8: newest sample's start epoch, uint32 LE.
// Seven rolling 24-hour windows follow, newest first. Bits are MSB-first and
// continuous between rows (there is no per-row padding). Each row contains:
//  min index (7), max index (7), then min older/current/newer and max
//  older/current/newer sample codes, at the series' original bit width.
// A row index is the distance in slots backwards from that window's newest
// sample; 127 marks an absent extreme. Missing samples use the source series'
// sentinel. Trailing bits in the final byte are zero. Incomplete windows have
// absent extremes/neighbor codes rather than fabricated history. A neighbor
// may belong to the adjacent window, but must actually exist in retained data.
class TelemetryDailyExtrema {
 public:
  enum Series : uint8_t {
    TEMPERATURE = 1,
    BATTERY_VOLTAGE = 2,
  };

  static constexpr uint8_t MAGIC = 0x17;
  static constexpr uint8_t VERSION = 1;
  static constexpr uint8_t DAYS = 7;
  static constexpr uint8_t MISSING_INDEX = 127;
  static constexpr size_t HEADER_BYTES = 9;
  static constexpr size_t MAX_BINARY_BYTES = 64;
  static constexpr size_t MAX_BASE64_BYTES = 88;
  static constexpr uint8_t TEMP_VOLT_MAGIC = 0x18;
  static constexpr size_t TEMP_VOLT_BINARY_BYTES = 112;
  static constexpr size_t TEMP_VOLT_BASE64_BYTES = 152;

  // read(offset) returns the original coded sample, where offset zero is the
  // newest *stored* sample. It must return the series' missing code for an
  // offset outside retained history. `latest_bucket` is that sample's epoch
  // divided by interval_seconds, rather than the sampler's current bucket.
  // With include_prompt=false a correlated CLI reply can use its own prefix.
  // The output is never truncated: insufficient capacity returns false and
  // leaves a nonempty destination containing just the terminating NUL.
  template <typename Reader>
  static bool format(Series series, uint8_t channel, uint32_t latest_bucket,
                     uint16_t interval_seconds, Reader read, char* reply,
                     size_t reply_size, bool include_prompt = true) {
    if (reply == NULL || reply_size == 0) return false;
    reply[0] = '\0';
    if ((series != TEMPERATURE && series != BATTERY_VOLTAGE)
        || interval_seconds != 1800U || channel != 0
        || latest_bucket > UINT32_MAX / interval_seconds) return false;

    const uint8_t bits = 8;
    const uint16_t slots = 48;
    const size_t data_bits = (size_t)DAYS * (14U + 6U * bits);
    const size_t binary_size = HEADER_BYTES + (data_bits + 7U) / 8U;
    const size_t encoded_size = ((binary_size + 2U) / 3U) * 4U;
    const size_t prefix_size = include_prompt ? 2U : 0U;
    if (binary_size > MAX_BINARY_BYTES || encoded_size > MAX_BASE64_BYTES
        || reply_size < prefix_size + encoded_size + 1U) return false;

    uint8_t binary[MAX_BINARY_BYTES] = {};
    binary[0] = MAGIC;
    binary[1] = VERSION;
    binary[2] = (uint8_t)series;
    binary[3] = channel;
    binary[4] = (uint8_t)(interval_seconds / 60U);
    const uint32_t latest_epoch = latest_bucket * interval_seconds;
    for (uint8_t i = 0; i < 4; ++i)
      binary[5 + i] = (uint8_t)(latest_epoch >> (i * 8U));

    BitWriter out(binary + HEADER_BYTES, binary_size - HEADER_BYTES);
    for (uint8_t day = 0; day < DAYS; ++day) {
      const uint16_t day_start = (uint16_t)(day * slots);
      if (!writeDay(out, series, latest_bucket, day_start, slots, bits,
                    7, MISSING_INDEX, read)) return false;
    }
    encodeBase64(binary, binary_size, reply, include_prompt);
    return true;
  }

  // Combined version 1: header [0x18, 1, interval_minutes=30, epoch_le32],
  // followed by seven windows, each with temperature then battery-voltage
  // rows. Each row has min and max 6-bit slot indexes (63 means missing),
  // followed by the same six eight-bit neighbor codes as the per-series form.
  // Row bits are continuous, MSB-first, and there is no per-row padding.
  // Both readers refer to the same half-hour bucket sequence.
  template <typename TemperatureReader, typename VoltageReader>
  static bool formatTemperatureVoltage(uint32_t latest_bucket,
      TemperatureReader temperature, VoltageReader voltage, char* reply,
      size_t reply_size, bool include_prompt = true) {
    if (reply == NULL || reply_size == 0) return false;
    reply[0] = '\0';
    const size_t prefix_size = include_prompt ? 2U : 0U;
    if (latest_bucket > UINT32_MAX / 1800U
        || reply_size < prefix_size + TEMP_VOLT_BASE64_BYTES + 1U)
      return false;
    uint8_t binary[TEMP_VOLT_BINARY_BYTES] = {};
    binary[0] = TEMP_VOLT_MAGIC;
    binary[1] = VERSION;
    binary[2] = 30;
    const uint32_t latest_epoch = latest_bucket * 1800U;
    for (uint8_t i = 0; i < 4; ++i)
      binary[3 + i] = (uint8_t)(latest_epoch >> (i * 8U));
    BitWriter out(binary + 7, sizeof(binary) - 7U);
    for (uint8_t day = 0; day < DAYS; ++day) {
      const uint16_t day_start = (uint16_t)(day * 48U);
      if (!writeDay(out, TEMPERATURE, latest_bucket, day_start, 48, 8,
                    6, 63, temperature)
          || !writeDay(out, BATTERY_VOLTAGE, latest_bucket, day_start, 48, 8,
                       6, 63, voltage)) return false;
    }
    encodeBase64(binary, sizeof(binary), reply, include_prompt);
    return true;
  }

 private:
  static void encodeBase64(const uint8_t* binary, size_t binary_size,
                           char* reply, bool include_prompt) {
    size_t p = 0;
    if (include_prompt) { reply[p++] = '>'; reply[p++] = ' '; }
    static const char alphabet[] =
        "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
    for (size_t i = 0; i < binary_size; i += 3U) {
      const uint32_t a = binary[i];
      const uint32_t b = i + 1U < binary_size ? binary[i + 1U] : 0U;
      const uint32_t c = i + 2U < binary_size ? binary[i + 2U] : 0U;
      const uint32_t value = (a << 16U) | (b << 8U) | c;
      reply[p++] = alphabet[(value >> 18U) & 63U];
      reply[p++] = alphabet[(value >> 12U) & 63U];
      reply[p++] = i + 1U < binary_size ? alphabet[(value >> 6U) & 63U] : '=';
      reply[p++] = i + 2U < binary_size ? alphabet[value & 63U] : '=';
    }
    reply[p] = '\0';
  }

  class BitWriter {
    uint8_t* dest_;
    size_t capacity_, used_;
   public:
    BitWriter(uint8_t* dest, size_t bytes)
        : dest_(dest), capacity_(bytes * 8U), used_(0) {}
    bool write(uint16_t value, uint8_t width) {
      if (used_ + width > capacity_) return false;
      for (uint8_t n = 0; n < width; ++n) {
        if (value & (uint16_t)(1U << (width - n - 1U)))
          dest_[used_ / 8U] |= (uint8_t)(1U << (7U - (used_ % 8U)));
        ++used_;
      }
      return true;
    }
  };

  template <typename Reader>
  static bool writeDay(BitWriter& out, Series series, uint32_t latest_bucket,
                       uint16_t day_start, uint16_t slots, uint8_t bits,
                       uint8_t index_width, uint8_t missing_index,
                       Reader& read) {
    uint8_t minimum = MISSING_INDEX, maximum = MISSING_INDEX;
    int32_t min_rank = 0, max_rank = 0;
    // Iterate newest to oldest; strict comparisons preserve newest ties.
    for (uint16_t i = 0; i < slots; ++i) {
      const uint16_t offset = (uint16_t)(day_start + i);
      const uint16_t value = sample(series, latest_bucket, offset, read);
      int32_t rank;
      if (!valueRank(series, value, rank)) continue;
      if (minimum == MISSING_INDEX || rank < min_rank) {
        minimum = (uint8_t)i;
        min_rank = rank;
      }
      if (maximum == MISSING_INDEX || rank > max_rank) {
        maximum = (uint8_t)i;
        max_rank = rank;
      }
    }
    return out.write(minimum == MISSING_INDEX ? missing_index : minimum, index_width)
        && out.write(maximum == MISSING_INDEX ? missing_index : maximum, index_width)
        && writeNeighborhood(out, series, latest_bucket, day_start,
                             minimum, bits, read)
        && writeNeighborhood(out, series, latest_bucket, day_start,
                             maximum, bits, read);
  }

  static bool valueRank(Series series, uint16_t value, int32_t& rank) {
    if (value == 0) return false;
    switch (series) {
      case TEMPERATURE:
        // Stored compact-temperature sentinels are 1=below range and
        // 2=above range; values 3..130 represent -50..+77 C.
        if (value == 1) rank = -1;
        else if (value == 2) rank = 128;
        else if (value <= 130) rank = (int32_t)value - 3;
        else return false;
        return true;
      case BATTERY_VOLTAGE:
        if (value > 255U) return false;
        rank = value; return true;
      default: return false;
    }
  }

  template <typename Reader>
  static uint16_t sample(Series series, uint32_t latest_bucket,
                         uint16_t offset, Reader& read) {
    if (offset > latest_bucket) return 0;
    const uint16_t value = read(offset);
    int32_t ignored;
    return valueRank(series, value, ignored) ? value : 0;
  }

  template <typename Reader>
  static bool writeNeighborhood(BitWriter& out, Series series,
                                uint32_t latest_bucket, uint16_t day_start,
                                uint8_t index, uint8_t bits, Reader& read) {
    const uint16_t absent = 0;
    if (index == MISSING_INDEX)
      return out.write(absent, bits) && out.write(absent, bits)
          && out.write(absent, bits);
    const uint16_t center = (uint16_t)(day_start + index);
    const uint16_t older = sample(series, latest_bucket,
                                  (uint16_t)(center + 1U), read);
    const uint16_t current = sample(series, latest_bucket, center, read);
    const uint16_t newer = center == 0 ? absent
        : sample(series, latest_bucket, (uint16_t)(center - 1U), read);
    return out.write(older, bits) && out.write(current, bits)
        && out.write(newer, bits);
  }
};

} // namespace mesh
