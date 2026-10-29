#pragma once

#include "BatteryForwardingConfig.h"
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

namespace mesh { namespace batteryforward {

// Percentage reporting also serves the existing low-battery alerts when the
// optional forwarding controls are omitted from a constrained image.
inline uint8_t percent(uint16_t mv, uint16_t empty, uint16_t full) {
  if (full <= empty || mv >= full) return 100;
  if (mv <= empty) return 0;
  return uint8_t(uint32_t(mv - empty) * 100U / (full - empty));
}

#if MESH_ENABLE_BATTERY_FORWARDING
static constexpr uint8_t Repeat = 1;
static constexpr uint8_t Flood = 2;
static constexpr uint32_t SampleIntervalMs = 30000;
static constexpr size_t RecordSize = 16;

struct Settings {
  uint8_t enabled = 0;
  uint8_t repeatBelow = 15;
  uint8_t floodBelow = 25;
  // Zero keeps the board's compiled endpoints. nRF52 uses its voltage policy.
  uint16_t emptyMv = 0;
  uint16_t fullMv = 0;
};

inline bool validEndpoints(uint16_t empty, uint16_t full) {
  return empty >= 2000 && full <= 20000 && full >= empty + 100U;
}

inline bool validSettings(const Settings& s) {
  return (s.enabled & ~(Repeat | Flood)) == 0
      && s.repeatBelow >= 1 && s.repeatBelow <= 100
      && s.floodBelow >= 1 && s.floodBelow <= 100
      && ((s.emptyMv == 0 && s.fullMv == 0) || validEndpoints(s.emptyMv, s.fullMv));
}

inline uint16_t checksum(const uint8_t* data, size_t size) {
  uint16_t crc = 0xffff;
  while (size--) {
    crc ^= uint16_t(*data++) << 8;
    for (unsigned bit = 0; bit < 8; ++bit)
      crc = (crc & 0x8000) ? uint16_t((crc << 1) ^ 0x1021) : uint16_t(crc << 1);
  }
  return crc;
}

inline void encode(uint8_t* out, const Settings& s) {
  memset(out, 0, RecordSize);
  out[0] = 'B'; out[1] = 'F'; out[2] = 'R'; out[3] = 2;
  out[4] = s.enabled; out[5] = s.repeatBelow; out[6] = s.floodBelow;
  out[8] = uint8_t(s.emptyMv); out[9] = uint8_t(s.emptyMv >> 8);
  out[10] = uint8_t(s.fullMv); out[11] = uint8_t(s.fullMv >> 8);
  const uint16_t crc = checksum(out, 14);
  out[14] = uint8_t(crc); out[15] = uint8_t(crc >> 8);
}

inline bool decode(const uint8_t* in, size_t size, Settings& s) {
  s = Settings{};
  if (!in || size != RecordSize || in[0] != 'B' || in[1] != 'F'
      || in[2] != 'R' || (in[3] != 1 && in[3] != 2) || in[7] || in[12] || in[13]
      || checksum(in, 14) != uint16_t(in[14] | (uint16_t(in[15]) << 8))) return false;
  Settings candidate;
  candidate.enabled = in[4]; candidate.repeatBelow = in[5]; candidate.floodBelow = in[6];
  candidate.emptyMv = uint16_t(in[8] | (uint16_t(in[9]) << 8));
  candidate.fullMv = uint16_t(in[10] | (uint16_t(in[11]) << 8));
  if (!validSettings(candidate)) return false;
  // Upgrade the old default pair once; v2 preserves an explicit 10/20 choice.
  if (in[3] == 1 && candidate.repeatBelow == 10 && candidate.floodBelow == 20) {
    candidate.repeatBelow = 15;
    candidate.floodBelow = 25;
  }
  s = candidate;
  return true;
}

struct State {
  uint32_t sampledAt = 0;
  uint16_t millivolts = 0;
  uint8_t percentage = 0;
  bool sampled = false;
  bool valid = false;
  bool repeatBlocked = false;
  bool floodBlocked = false;

  bool due(uint32_t now) const {
    return !sampled || uint32_t(now - sampledAt) >= SampleIntervalMs;
  }
  static bool blocked(bool was, bool enabled, bool known, uint8_t pct, uint8_t below) {
    if (!enabled || !known) return false;
    const unsigned resume = unsigned(below) + 2U > 100U ? 100U : unsigned(below) + 2U;
    return was ? pct < resume : pct < below;
  }
  void update(uint32_t now, uint16_t mv, uint8_t pct, bool known, const Settings& s) {
    sampled = true; sampledAt = now; millivolts = mv; percentage = pct;
    valid = known && pct <= 100 && mv >= 2000 && mv <= 20000;
    repeatBlocked = blocked(repeatBlocked, s.enabled & Repeat, valid, pct, s.repeatBelow);
    floodBlocked = blocked(floodBlocked, s.enabled & Flood, valid, pct, s.floodBelow);
  }
  bool allows(bool flood) const { return !repeatBlocked && (!flood || !floodBlocked); }
};

inline bool parseNumber(const char* text, unsigned min, unsigned max, uint16_t& value) {
  if (!text || !*text) return false;
  unsigned number = 0;
  for (const char* p = text; *p; ++p) {
    if (*p < '0' || *p > '9') return false;
    number = number * 10U + unsigned(*p - '0');
    if (number > max) return false;
  }
  if (number < min) return false;
  value = uint16_t(number);
  return true;
}

// The save callback receives a candidate, so a failed save cannot change the
// active policy. Sampling remains a separate hardware boundary in CommonCLI.
template <typename Save>
bool handleCommand(Settings& s, const State& state, uint16_t empty, uint16_t full,
                   bool externalVoltagePolicy, const char* command,
                   char* reply, size_t capacity, Save save) {
  if (!command || !reply || !capacity) return false;
  const bool get = strncmp(command, "get ", 4) == 0;
  const bool set = strncmp(command, "set ", 4) == 0;
  if (!get && !set) return false;
  const char* key = command + 4;
  enum Kind { None, RepeatLimit, FloodLimit, Status, Empty, Full } kind = None;
  size_t length = 0;
  const char* names[] = {"repeat.battery", "flood.battery", "battery.forward", "battery.empty", "battery.full"};
  for (unsigned i = 0; i < 5; ++i) {
    const size_t n = strlen(names[i]);
    if (strncmp(key, names[i], n) == 0 && (!key[n] || key[n] == ' ' || key[n] == '\t')) {
      kind = Kind(i + 1); length = n; break;
    }
  }
  if (kind == None || (externalVoltagePolicy && (kind == Empty || kind == Full))) return false;
  const char* value = key + length;
  while (*value == ' ' || *value == '\t') ++value;
  if (get) {
    if (*value) snprintf(reply, capacity, "Error: usage get %.*s", int(length), key);
    else if (kind == RepeatLimit || kind == FloodLimit) {
      const bool repeat = kind == RepeatLimit;
      snprintf(reply, capacity, "> %s below=%u%%", s.enabled & (repeat ? Repeat : Flood) ? "on" : "off",
               unsigned(repeat ? s.repeatBelow : s.floodBelow));
    } else if (kind == Empty || kind == Full) {
      snprintf(reply, capacity, "> %u", unsigned(kind == Empty ? empty : full));
    } else {
      char battery[12] = "unknown";
      if (state.valid) snprintf(battery, sizeof(battery), "%u%%", unsigned(state.percentage));
      snprintf(reply, capacity, "> battery=%s mv=%u repeat=%s flood=%s", battery,
               unsigned(state.millivolts), state.allows(false) ? "allowed" : "blocked",
               state.allows(true) ? "allowed" : "blocked");
    }
    return true;
  }
  if (kind == Status) {
    snprintf(reply, capacity, "Error: usage get battery.forward");
    return true;
  }
  Settings candidate = s;
  uint16_t number = 0;
  if (kind == RepeatLimit || kind == FloodLimit) {
    const uint8_t bit = kind == RepeatLimit ? Repeat : Flood;
    if (strcmp(value, "off") == 0) candidate.enabled &= ~bit;
    else if (strcmp(value, "on") == 0) candidate.enabled |= bit;
    else if (parseNumber(value, 1, 100, number)) {
      candidate.enabled |= bit;
      (kind == RepeatLimit ? candidate.repeatBelow : candidate.floodBelow) = uint8_t(number);
    } else {
      snprintf(reply, capacity, "Error: use set %.*s off|on|1-100", int(length), key);
      return true;
    }
  } else {
    if (!parseNumber(value, 2000, 20000, number)) {
      snprintf(reply, capacity, "Error: use set %.*s 2000-20000 (mV)", int(length), key);
      return true;
    }
    candidate.emptyMv = kind == Empty ? number : empty;
    candidate.fullMv = kind == Full ? number : full;
    if (!validEndpoints(candidate.emptyMv, candidate.fullMv)) {
      snprintf(reply, capacity, "Error: battery.full must exceed battery.empty by at least 100 mV");
      return true;
    }
  }
  if (!save(candidate)) snprintf(reply, capacity, "Error: battery settings save unconfirmed; active policy unchanged");
  else { s = candidate; snprintf(reply, capacity, "OK - saved"); }
  return true;
}

#endif  // MESH_ENABLE_BATTERY_FORWARDING

}} // namespace mesh::batteryforward
