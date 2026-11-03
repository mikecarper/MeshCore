#pragma once

#include <stdint.h>

namespace mesh { namespace tracker { namespace geofence {

// Coordinates use signed degrees * 10,000,000. west > east describes a box
// that crosses the antimeridian. Equal endpoints denote an inclusive line.
struct Box {
  bool configured = false;
  int32_t south = 0;
  int32_t north = 0;
  int32_t west = 0;
  int32_t east = 0;
};

enum class Result : uint8_t { Unknown, Inside, Outside };

static constexpr int32_t kLatitudeLimit = 900000000;
static constexpr int32_t kLongitudeLimit = 1800000000;

inline bool validLatitude(int32_t latitude) {
  return latitude >= -kLatitudeLimit && latitude <= kLatitudeLimit;
}

inline bool validLongitude(int32_t longitude) {
  return longitude >= -kLongitudeLimit && longitude <= kLongitudeLimit;
}

inline bool isValid(const Box& box) {
  return box.configured && validLatitude(box.south) && validLatitude(box.north)
      && box.south <= box.north && validLongitude(box.west)
      && validLongitude(box.east);
}

inline bool containsLongitude(const Box& box, int32_t longitude) {
  return box.west <= box.east
      ? longitude >= box.west && longitude <= box.east
      : longitude >= box.west || longitude <= box.east;
}

// A caller must only pass fresh=true for a new, validated GPS fix. Unknown
// must remain distinct from Inside, including after a timeout or reset.
inline Result classify(const Box& box, bool fresh, int32_t latitude,
                       int32_t longitude) {
  if (!fresh || !isValid(box) || !validLatitude(latitude)
      || !validLongitude(longitude)) return Result::Unknown;
  const bool within_latitude = latitude >= box.south && latitude <= box.north;
  bool within_longitude = containsLongitude(box, longitude);
  // Both signed representations denote the same meridian.
  if (!within_longitude && longitude == -kLongitudeLimit)
    within_longitude = containsLongitude(box, kLongitudeLimit);
  else if (!within_longitude && longitude == kLongitudeLimit)
    within_longitude = containsLongitude(box, -kLongitudeLimit);
  return within_latitude && within_longitude ? Result::Inside : Result::Outside;
}

}}} // namespace mesh::tracker::geofence
