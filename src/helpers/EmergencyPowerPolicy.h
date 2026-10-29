#pragma once

#include <stddef.h>
#include <stdint.h>

namespace mesh { namespace emergencypower {

static constexpr uint32_t SampleIntervalMs = 30000;
static constexpr uint32_t HourMs = 3600000;
static constexpr uint32_t AwakeWindowMs = 900000;
static constexpr uint32_t ReplyHoldMs = 5000;
static constexpr uint32_t PlausibleEpoch = 1577836800; // 2020-01-01 UTC
static constexpr uint16_t MaximumAwakeMinutes = 10080;
static constexpr size_t RecordSize = 8;

struct Settings {
  bool manual = false;
};

inline uint16_t checksum(const uint8_t* data, size_t size) {
  uint16_t crc = 0xffff;
  while (size--) {
    crc ^= uint16_t(*data++) << 8;
    for (unsigned bit = 0; bit < 8; ++bit)
      crc = (crc & 0x8000) ? uint16_t((crc << 1) ^ 0x1021) : uint16_t(crc << 1);
  }
  return crc;
}

inline void encode(uint8_t* out, const Settings& settings) {
  out[0] = 'P'; out[1] = 'E'; out[2] = 'M'; out[3] = 1;
  out[4] = settings.manual ? 1 : 0; out[5] = 0;
  const uint16_t crc = checksum(out, 6);
  out[6] = uint8_t(crc); out[7] = uint8_t(crc >> 8);
}

inline bool decode(const uint8_t* in, size_t size, Settings& settings) {
  settings = Settings{};
  if (!in || size != RecordSize || in[0] != 'P' || in[1] != 'E'
      || in[2] != 'M' || in[3] != 1 || in[4] > 1 || in[5]
      || checksum(in, 6) != uint16_t(in[6] | uint16_t(in[7]) << 8)) return false;
  settings.manual = in[4] != 0;
  return true;
}

struct State {
  // Runtime only: keep aligned timers together without packing accesses.
  uint32_t sampledAt = 0;
  uint32_t phaseMs = 0;
  uint32_t lastTick = 0;
  uint32_t activationStarted = 0;
  uint32_t awakeStarted = 0;
  uint32_t awakeDuration = 0;
  uint16_t millivolts = 0;
  uint8_t percentage = 0;
  Settings settings;
  bool sampled = false;
  bool valid = false;
  bool lowBattery = false;
  bool activationHold = false;

  void begin(const Settings& loaded, uint32_t now) {
    *this = State{};
    settings = loaded;
    phaseMs = now % HourMs;
    lastTick = now;
    if (active()) holdReplies(now);
  }
  bool active() const { return settings.manual || lowBattery; }
  bool due(uint32_t now) const {
    return !sampled || uint32_t(now - sampledAt) >= SampleIntervalMs;
  }
  void holdReplies(uint32_t now) {
    activationStarted = now;
    activationHold = true;
  }
  void setManual(bool manual, uint32_t now) {
    if (settings.manual == manual) return;
    settings.manual = manual;
    if (active()) holdReplies(now);
  }
  void sample(uint32_t now, uint16_t mv, uint8_t pct, bool known) {
    const bool was_active = active();
    sampled = true; sampledAt = now; millivolts = mv; percentage = pct;
    valid = known && pct <= 100 && mv >= 2000 && mv <= 20000;
    // A failed reading cannot wake a node already protecting its battery.
    if (valid) lowBattery = lowBattery ? pct < 7 : pct <= 5;
    if (!was_active && active()) holdReplies(now);
  }
  bool stayAwake(uint16_t minutes, uint32_t now) {
    if (minutes > MaximumAwakeMinutes) return false;
    awakeStarted = now;
    awakeDuration = uint32_t(minutes) * 60000UL;
    return true;
  }
  uint32_t holdRemaining(uint32_t now) {
    uint32_t remaining = 0;
    if (activationHold) {
      const uint32_t elapsed = uint32_t(now - activationStarted);
      if (elapsed >= ReplyHoldMs) activationHold = false;
      else remaining = ReplyHoldMs - elapsed;
    }
    if (awakeDuration) {
      const uint32_t elapsed = uint32_t(now - awakeStarted);
      if (elapsed >= awakeDuration) awakeDuration = 0;
      else if (awakeDuration - elapsed > remaining) remaining = awakeDuration - elapsed;
    }
    return remaining;
  }
  static bool usesRtc(uint32_t epoch) { return epoch >= PlausibleEpoch; }
  uint32_t sleepMilliseconds(uint32_t now, uint32_t epoch) {
    // Accumulate the boot-relative phase across millis() wrap. RTC corrections
    // change only the hourly window, never the duration of an awake lease.
    phaseMs = (phaseMs + uint32_t(now - lastTick) % HourMs) % HourMs;
    lastTick = now;
    const uint32_t hold = holdRemaining(now);
    if (!active() || hold) return 0;
    const uint32_t phase = usesRtc(epoch) ? (epoch % 3600UL) * 1000UL : phaseMs;
    return phase < AwakeWindowMs ? 0 : HourMs - phase;
  }
};
static_assert(sizeof(State) <= 32, "Emergency timers must retain their compact RAM layout");
static_assert(alignof(State) >= alignof(uint32_t), "Emergency timers must remain aligned");

}} // namespace mesh::emergencypower
