#pragma once

#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include "TrackerGeofence.h"

namespace mesh { namespace tracker { namespace geofence {

// Parse decimal coordinates directly into E7, without float rounding.
// Seven fractional digits are sufficient for GPS coordinates on the wire.
inline bool parseCoordinate(const char*& text, int32_t limit, int32_t& out) {
  while (*text == ' ') ++text;
  bool negative = false;
  if (*text == '-' || *text == '+') { negative = *text == '-'; ++text; }
  if (*text < '0' || *text > '9') return false;
  uint32_t whole = 0;
  while (*text >= '0' && *text <= '9') {
    whole = whole * 10u + uint32_t(*text++ - '0');
    if (whole > 180u) return false;
  }
  uint32_t frac = 0, scale = 1000000;
  if (*text == '.') {
    ++text;
    if (*text < '0' || *text > '9') return false;
    do {
      if (!scale) return false;
      frac += uint32_t(*text++ - '0') * scale;
      scale /= 10;
    } while (*text >= '0' && *text <= '9');
  }
  const uint32_t magnitude = whole * 10000000u + frac;
  if (magnitude > static_cast<uint32_t>(limit)) return false;
  out = negative ? -static_cast<int32_t>(magnitude) : static_cast<int32_t>(magnitude);
  while (*text == ' ') ++text;
  return true;
}

// CLI order is south,west,north,east; west > east crosses the dateline.
inline bool parseBox(const char* text, Box& out) {
  if (!text) return false;
  Box box;
  if (!parseCoordinate(text, kLatitudeLimit, box.south) || *text++ != ','
      || !parseCoordinate(text, kLongitudeLimit, box.west) || *text++ != ','
      || !parseCoordinate(text, kLatitudeLimit, box.north) || *text++ != ','
      || !parseCoordinate(text, kLongitudeLimit, box.east) || *text != '\0') return false;
  box.configured = true;
  if (!isValid(box)) return false;
  out = box;
  return true;
}

inline void formatCoordinate(int32_t value, char* out, size_t capacity) {
  const int64_t signed_value = value;
  const uint32_t magnitude = static_cast<uint32_t>(signed_value < 0 ? -signed_value : signed_value);
  snprintf(out, capacity, "%s%lu.%07lu", signed_value < 0 ? "-" : "",
      static_cast<unsigned long>(magnitude / 10000000u),
      static_cast<unsigned long>(magnitude % 10000000u));
}

inline const char* resultName(Result value) {
  return value == Result::Inside ? "inside" : value == Result::Outside ? "outside" : "unknown";
}

}}} // namespace mesh::tracker::geofence
