#include "SensorManager.h"

#if ENV_INCLUDE_GPS
#ifndef GPS_TELEMETRY_CACHE_INTERVAL_SEC
#define GPS_TELEMETRY_CACHE_INTERVAL_SEC (2UL * 60UL * 60UL)
#endif
#ifndef GPS_TELEMETRY_MAX_ACQUIRE_SEC
#define GPS_TELEMETRY_MAX_ACQUIRE_SEC (15UL * 60UL)
#endif
#ifndef GPS_TELEMETRY_STABLE_SEC
#define GPS_TELEMETRY_STABLE_SEC 30UL
#endif
#ifndef GPS_TELEMETRY_HOLD_SEC
#define GPS_TELEMETRY_HOLD_SEC (2UL * 60UL * 60UL)
#endif
#ifndef GPS_TELEMETRY_MAX_STALE_SEC
#define GPS_TELEMETRY_MAX_STALE_SEC (12UL * 60UL * 60UL)
#endif
#ifndef GPS_TELEMETRY_STABLE_RADIUS_M
#define GPS_TELEMETRY_STABLE_RADIUS_M 100.0f
#endif

static bool millisDue(unsigned long now, unsigned long deadline) {
  return (long)(now - deadline) >= 0;
}

static float gpsDistanceSquaredMeters(float lat1, float lon1, float lat2, float lon2) {
  static const float METERS_PER_DEGREE = 111320.0f;
  float x = (lon2 - lon1) * METERS_PER_DEGREE;
  float y = (lat2 - lat1) * METERS_PER_DEGREE;
  return x * x + y * y;
}

bool SensorManager::gpsTelemetryHoldActive(unsigned long now) const {
  return gps_hold_until != 0 && !millisDue(now, gps_hold_until);
}

bool SensorManager::gpsTelemetryCacheFresh(unsigned long now) const {
  return gps_cache_valid &&
      (unsigned long)(now - gps_cache_updated_at) <= GPS_TELEMETRY_MAX_STALE_SEC * 1000UL;
}

void SensorManager::updateGpsTelemetryCache(float lat, float lon, float altitude, unsigned long now) {
  gps_cache_lat = lat;
  gps_cache_lon = lon;
  gps_cache_altitude = altitude;
  gps_cache_updated_at = now;
  gps_cache_valid = true;
}

void SensorManager::maybeStopGpsForTelemetry(unsigned long now) {
  if (telemetryGpsActive() && !gps_user_enabled && !gps_acquiring && !gpsTelemetryHoldActive(now)) {
    telemetryGpsStop();
    gps_next_cache_update_at = now + GPS_TELEMETRY_CACHE_INTERVAL_SEC * 1000UL;
  }
}

void SensorManager::beginGpsTelemetryAcquisition(unsigned long now) {
  if (!telemetryGpsDetected() || gps_acquiring) return;

  gps_acquiring = true;
  gps_acquire_has_fix = false;
  gps_acquire_started_at = now;
  gps_stable_started_at = now;
  gps_weighted_lat = 0;
  gps_weighted_lon = 0;
  gps_weighted_altitude = 0;
  gps_weight_sum = 0;
  gps_weight_count = 0;
  if (!telemetryGpsActive()) telemetryGpsStart();
}

void SensorManager::finishGpsTelemetryAcquisition(unsigned long now, bool use_weighted_average) {
  if (use_weighted_average && gps_weight_sum > 0) {
    updateGpsTelemetryCache(gps_weighted_lat / gps_weight_sum,
                            gps_weighted_lon / gps_weight_sum,
                            gps_weighted_altitude / gps_weight_sum,
                            now);
  }
  gps_acquiring = false;
  gps_next_cache_update_at = now + GPS_TELEMETRY_CACHE_INTERVAL_SEC * 1000UL;
  maybeStopGpsForTelemetry(now);
}

bool SensorManager::queryGpsTelemetry(uint8_t requester_permissions, CayenneLPP& telemetry) {
  if (!(requester_permissions & TELEM_PERM_LOCATION) || !telemetryGpsDetected()) return false;

  unsigned long now = millis();
  gps_hold_until = now + GPS_TELEMETRY_HOLD_SEC * 1000UL;
  if (!telemetryGpsActive()) telemetryGpsStart();
  if (!gpsTelemetryCacheFresh(now) && !gps_acquiring) beginGpsTelemetryAcquisition(now);

  if (!gpsTelemetryCacheFresh(now)) return false;
  telemetry.addGPS(TELEM_CHANNEL_SELF, gps_cache_lat, gps_cache_lon, gps_cache_altitude);
  return true;
}

void SensorManager::processGpsTelemetryFix(float lat, float lon, float altitude, unsigned long now) {
  if (!gps_acquiring) {
    if (gps_user_enabled || gpsTelemetryHoldActive(now)) {
      updateGpsTelemetryCache(lat, lon, altitude, now);
    }
    return;
  }

  if (!gps_acquire_has_fix) {
    gps_acquire_has_fix = true;
    gps_stable_started_at = now;
    gps_stable_origin_lat = lat;
    gps_stable_origin_lon = lon;
  }

  static const float STABLE_RADIUS_M2 =
      GPS_TELEMETRY_STABLE_RADIUS_M * GPS_TELEMETRY_STABLE_RADIUS_M;
  if (gpsDistanceSquaredMeters(gps_stable_origin_lat, gps_stable_origin_lon, lat, lon) > STABLE_RADIUS_M2) {
    updateGpsTelemetryCache(lat, lon, altitude, now);
    finishGpsTelemetryAcquisition(now, false);
    return;
  }

  float weight = (float)++gps_weight_count;
  gps_weighted_lat += lat * weight;
  gps_weighted_lon += lon * weight;
  gps_weighted_altitude += altitude * weight;
  gps_weight_sum += weight;
  if (millisDue(now, gps_stable_started_at + GPS_TELEMETRY_STABLE_SEC * 1000UL)) {
    finishGpsTelemetryAcquisition(now, true);
  }
}

void SensorManager::loopGpsTelemetry(unsigned long now) {
  if (!gps_user_enabled && !gpsTelemetryHoldActive(now) && !gps_acquiring) {
    maybeStopGpsForTelemetry(now);
  }
  if (gps_location_access_available && telemetryGpsDetected() && !gps_user_enabled &&
      !gpsTelemetryHoldActive(now) && !gps_acquiring &&
      (gps_next_cache_update_at == 0 || millisDue(now, gps_next_cache_update_at))) {
    beginGpsTelemetryAcquisition(now);
  }
  if (gps_acquiring && millisDue(now, gps_acquire_started_at + GPS_TELEMETRY_MAX_ACQUIRE_SEC * 1000UL)) {
    finishGpsTelemetryAcquisition(now, gps_acquire_has_fix && gps_weight_sum > 0);
  }
}

void SensorManager::setGpsTelemetryUserEnabled(bool enabled) {
  gps_user_enabled = enabled;
  unsigned long now = millis();
  if (enabled) {
    if (telemetryGpsDetected() && !telemetryGpsActive()) telemetryGpsStart();
  } else {
    maybeStopGpsForTelemetry(now);
  }
}
#endif

void SensorManager::setTelemetryLocationAccessAvailable(bool available) {
#if ENV_INCLUDE_GPS
  unsigned long now = millis();
  if (gps_location_access_available == available) return;

  gps_location_access_available = available;
  if (available) {
    gps_next_cache_update_at = 0;
  } else if (gps_acquiring && !gps_user_enabled && !gpsTelemetryHoldActive(now)) {
    gps_acquiring = false;
    maybeStopGpsForTelemetry(now);
  }
#else
  (void)available;
#endif
}
