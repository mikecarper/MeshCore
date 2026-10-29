#pragma once

#include "EmergencyPowerPolicy.h"
#include "SolarDaylight.h"

namespace mesh { namespace powerschedule {

static constexpr size_t RecordSize = 14;
static constexpr uint32_t DaySeconds = 86400;

struct Settings {
  // Natural 16-bit allocation units; only explicit encode/decode is persisted.
  uint16_t startMinute : 11;
  bool daylight : 1;
  bool hours : 1;
  uint16_t endMinute : 11;
  int16_t offsetMinutes : 11;
  Settings() : startMinute(0), daylight(false), hours(false), endMinute(0), offsetMinutes(0) {}
};
static_assert(sizeof(Settings) <= 6, "Operating settings must retain their compact layout");

inline bool valid(const Settings& settings) {
  return settings.startMinute < 1440 && settings.endMinute <= 1440
      && (!settings.hours || settings.startMinute != settings.endMinute)
      && settings.offsetMinutes >= -720 && settings.offsetMinutes <= 840;
}

inline bool equal(const Settings& a, const Settings& b) {
  return a.daylight == b.daylight && a.hours == b.hours
      && a.startMinute == b.startMinute && a.endMinute == b.endMinute
      && a.offsetMinutes == b.offsetMinutes;
}

inline void encode(uint8_t* out, const Settings& settings) {
  out[0] = 'P'; out[1] = 'S'; out[2] = 'C'; out[3] = 1;
  out[4] = (settings.daylight ? 1 : 0) | (settings.hours ? 2 : 0);
  out[5] = 0;
  const uint16_t values[] = {settings.startMinute, settings.endMinute,
                             uint16_t(settings.offsetMinutes + 720)};
  for (unsigned i = 0; i < 3; ++i) {
    out[6 + i * 2] = uint8_t(values[i]);
    out[7 + i * 2] = uint8_t(values[i] >> 8);
  }
  const uint16_t crc = emergencypower::checksum(out, 12);
  out[12] = uint8_t(crc); out[13] = uint8_t(crc >> 8);
}

inline bool decode(const uint8_t* in, size_t size, Settings& settings) {
  settings = Settings{};
  if (!in || size != RecordSize || in[0] != 'P' || in[1] != 'S'
      || in[2] != 'C' || in[3] != 1 || (in[4] & ~3) || in[5]
      || emergencypower::checksum(in, 12) != uint16_t(in[12] | uint16_t(in[13]) << 8))
    return false;
  Settings loaded;
  loaded.daylight = (in[4] & 1) != 0;
  loaded.hours = (in[4] & 2) != 0;
  const uint16_t start = uint16_t(in[6] | uint16_t(in[7]) << 8);
  const uint16_t end = uint16_t(in[8] | uint16_t(in[9]) << 8);
  const uint16_t offset = uint16_t(in[10] | uint16_t(in[11]) << 8);
  if (start >= 1440 || end > 1440 || offset > 1560) return false;
  loaded.startMinute = start;
  loaded.endMinute = end;
  loaded.offsetMinutes = int16_t(offset) - 720;
  if (!valid(loaded)) return false;
  settings = loaded;
  return true;
}

struct State {
  uint32_t evaluatedUtc = UINT32_MAX;
  uint32_t nextChangeUtc = 0;
  Settings settings;
  solar::State daylightState = solar::State::Unknown;
  bool hasLocation = false;

  void begin(const Settings& loaded) {
    *this = State{};
    settings = loaded;
  }
  void invalidate() {
    evaluatedUtc = UINT32_MAX;
    nextChangeUtc = 0;
    daylightState = solar::State::Unknown;
  }
  uint32_t sleepMilliseconds(uint32_t epoch, bool located,
                             int32_t latitude_e6, int32_t longitude_e6) {
    if (!emergencypower::State::usesRtc(epoch)) {
      invalidate();
      return 0; // Never strand a node whose clock has not been set.
    }
    uint32_t budget = 0;
    if (settings.hours) {
      int32_t local = int32_t(epoch % DaySeconds) + int32_t(settings.offsetMinutes) * 60;
      if (local < 0) local += DaySeconds;
      else if (local >= int32_t(DaySeconds)) local -= DaySeconds;
      const int32_t start = int32_t(settings.startMinute) * 60;
      const int32_t end = int32_t(settings.endMinute) * 60;
      const bool inside = start < end ? local >= start && local < end
                                      : local >= start || local < end;
      if (!inside) budget = uint32_t((start - local + DaySeconds) % DaySeconds) * 1000UL;
    }
    if (settings.daylight) {
      // Bound calculations to once per UTC second. Location availability is
      // immediate; a changed known position is observed within one second.
      // The explicit sentinel check also handles clock recovery at UINT32_MAX.
      if (evaluatedUtc == UINT32_MAX || evaluatedUtc != epoch || located != hasLocation) {
        const solar::Window window = located ? solar::evaluate(epoch, latitude_e6, longitude_e6)
            : solar::Window{solar::State::Unknown, 0};
        evaluatedUtc = epoch;
        hasLocation = located;
        daylightState = window.state;
        nextChangeUtc = window.nextChangeUtc;
      }
      if (daylightState == solar::State::Night) {
        uint32_t seconds = nextChangeUtc > epoch ? nextChangeUtc - epoch : 60;
        if (seconds > DaySeconds) seconds = DaySeconds;
        const uint32_t night = seconds * 1000UL;
        if (night > budget) budget = night;
      }
    } else {
      daylightState = solar::State::Unknown;
    }
    return budget;
  }
};

static_assert(sizeof(State) <= 16, "Operating schedule must keep its bounded RAM footprint");

}} // namespace mesh::powerschedule
