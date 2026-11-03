#pragma once
#include <stdint.h>

namespace mesh { namespace tracker {

// The opt-in Tag tracker samples battery/environment every five minutes.
// Other sensor roles and builds retain their configured normal cadence.
inline uint32_t sensorTelemetryInterval(bool tracker_enabled, uint32_t normal_seconds) {
#if defined(MESH_SENSOR_MOTION) && MESH_SENSOR_MOTION
  if (tracker_enabled) return 300;
#else
  (void)tracker_enabled;
#endif
  return normal_seconds;
}

}} // namespace mesh::tracker
