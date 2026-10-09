#pragma once

#include <math.h>
#include <stdint.h>

namespace mesh {

// Private fleet matching uses a configured fixed position first. Only the
// default/unset pair falls back to an existing GPS cache; never wake GPS here.
template<class Prefs, class Sensors>
bool readFleetLocation(const Prefs& prefs, const Sensors& sensors,
                       int32_t& latitude_e6, int32_t& longitude_e6) {
  latitude_e6 = longitude_e6 = 0;
  double latitude = prefs.node_lat;
  double longitude = prefs.node_lon;
  if (latitude == 0 && longitude == 0
      && !sensors.getCachedGpsPosition(latitude, longitude)) return false;
  if (!isfinite(latitude) || !isfinite(longitude)
      || latitude < -90.0 || latitude > 90.0
      || longitude < -180.0 || longitude > 180.0) return false;
  const int32_t lat = static_cast<int32_t>(lround(latitude * 1000000.0));
  const int32_t lon = static_cast<int32_t>(lround(longitude * 1000000.0));
  if (lat == 0 && lon == 0) return false;
  latitude_e6 = lat;
  longitude_e6 = lon;
  return true;
}

} // namespace mesh
