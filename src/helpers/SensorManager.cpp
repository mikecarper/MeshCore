#include "SensorManager.h"
#include <math.h>

#if ENV_INCLUDE_GPS
static const uint32_t GPS_TRACKER_MAX_ACQUIRE_MS = 120000UL;
#endif

bool SensorManager::getCachedGpsPosition(double& latitude, double& longitude) const {
  latitude = longitude = 0;
#if ENV_INCLUDE_GPS
  if (!gpsTelemetryCacheFresh(millis())
      || !isfinite(gps_cache_lat) || !isfinite(gps_cache_lon)
      || gps_cache_lat < -90.0f || gps_cache_lat > 90.0f
      || gps_cache_lon < -180.0f || gps_cache_lon > 180.0f
      || (gps_cache_lat == 0 && gps_cache_lon == 0)) return false;
  latitude = gps_cache_lat;
  longitude = gps_cache_lon;
  return true;
#else
  return false;
#endif
}

void SensorManager::setTrackerGpsModeEnabled(bool enabled) {
#if ENV_INCLUDE_GPS
  if (gps_tracker_enabled == enabled) return;
  // Leave an interrupted window through the same hardware stop/start hooks so
  // the normal provider power cycle is rearmed when a saved GPS-on preference
  // resumes. In particular, GPS-off must not leave the receiver running.
  if (!enabled && gps_transport_available && telemetryGpsDetected() && telemetryGpsActive()) {
    telemetryGpsStop();
  }
  gps_tracker_enabled = enabled;
  gps_tracker_fix_ready = false;
  const unsigned long now = millis();
  cancelGpsTelemetryDemand(now);
  LocationProvider* location = getLocationProvider();
  if (location != nullptr) {
    location->endFreshTimeSync();
    // The provider's normal clock/power cycle must not wake GPS while the
    // tracker is idle. The normal preference is restored when mode ends.
    location->setGPSPowerSaving(!enabled && powersaving_enabled && gps_user_enabled);
  }
  if (enabled) {
    maybeStopGpsForTelemetry(now);
  } else if (gps_transport_available && telemetryGpsDetected() && gps_user_enabled) {
    if (!telemetryGpsActive()) telemetryGpsStart();
  } else if (gps_location_access_available) {
    gps_next_cache_update_at = 0;
  }
#else
  (void)enabled;
#endif
}

bool SensorManager::beginTrackerGpsAcquisition() {
#if ENV_INCLUDE_GPS
  if (!gps_tracker_enabled || !gps_transport_available || !telemetryGpsDetected()) return false;
  if (gps_acquiring) return true; // A repeated request must not extend its bound.
  gps_tracker_fix_ready = false;
  gps_hold_until = 0;
  LocationProvider* location = getLocationProvider();
  if (location == nullptr || !location->clearPositionFix()) return false;
  gps_tracker_sync_generation = location->getTimeSyncGeneration();
  location->beginFreshTimeSync();
  if (location->getGPSPowerSaving()) location->setGPSPowerSaving(false);
  beginGpsTelemetryAcquisition(millis());
  return gps_acquiring;
#else
  return false;
#endif
}

bool SensorManager::isTrackerGpsAcquisitionPending() const {
#if ENV_INCLUDE_GPS
  return gps_tracker_enabled && gps_acquiring;
#else
  return false;
#endif
}

bool SensorManager::takeTrackerGpsPosition(double& latitude, double& longitude) {
  latitude = longitude = 0;
#if ENV_INCLUDE_GPS
  const unsigned long now = millis();
  if (gps_tracker_enabled && gps_acquiring
      && static_cast<uint32_t>(now - gps_acquire_started_at) >= GPS_TRACKER_MAX_ACQUIRE_MS)
    finishGpsTelemetryAcquisition(now, false);
  if (!gps_tracker_enabled || gps_acquiring || !gps_tracker_fix_ready) return false;
  gps_tracker_fix_ready = false;
  if (static_cast<uint32_t>(now - gps_stable_started_at) >= GPS_TRACKER_MAX_ACQUIRE_MS) return false;
  latitude = gps_cache_lat;
  longitude = gps_cache_lon;
  return true;
#else
  return false;
#endif
}

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
      static_cast<uint32_t>(now - gps_cache_updated_at) <= GPS_TELEMETRY_MAX_STALE_SEC * 1000UL;
}

void SensorManager::updateGpsTelemetryCache(float lat, float lon, float altitude, unsigned long now) {
  gps_cache_lat = lat;
  gps_cache_lon = lon;
  gps_cache_altitude = altitude;
  gps_cache_updated_at = now;
  gps_cache_valid = true;
}

void SensorManager::maybeStopGpsForTelemetry(unsigned long now) {
  if (gps_transport_available && telemetryGpsActive() && (gps_tracker_enabled || !gps_user_enabled)
      && !gps_acquiring && !gpsTelemetryHoldActive(now)) {
    telemetryGpsStop();
    gps_next_cache_update_at = now + GPS_TELEMETRY_CACHE_INTERVAL_SEC * 1000UL;
  }
}

void SensorManager::cancelGpsTelemetryDemand(unsigned long now) {
  // An explicit user-off request must win over work started by an earlier
  // telemetry query.  Otherwise the acquisition/hold state can keep a GPS
  // powered for up to two hours after the user turned it off.
  gps_acquiring = false;
  gps_acquire_has_fix = false;
  gps_hold_until = 0;
  gps_acquire_started_at = 0;
  gps_stable_started_at = 0;
  gps_weighted_lat = 0;
  gps_weighted_lon = 0;
  gps_weighted_altitude = 0;
  gps_weight_sum = 0;
  gps_weight_count = 0;
  // Do not let the background cache refresh immediately undo the command.
  gps_next_cache_update_at = now + GPS_TELEMETRY_CACHE_INTERVAL_SEC * 1000UL;
}

void SensorManager::beginGpsTelemetryAcquisition(unsigned long now) {
  if (!gps_transport_available || !telemetryGpsDetected() || gps_acquiring) return;

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
  if (gps_tracker_enabled) {
    // Keep a fresh position acquired before a clock-sync timeout. Its one-shot
    // result expires relative to completion, not the first position sample.
    gps_stable_started_at = now;
    LocationProvider* location = getLocationProvider();
    if (location != nullptr) location->endFreshTimeSync();
  }
  gps_next_cache_update_at = now + GPS_TELEMETRY_CACHE_INTERVAL_SEC * 1000UL;
  maybeStopGpsForTelemetry(now);
}

bool SensorManager::queryGpsTelemetry(uint8_t requester_permissions, CayenneLPP& telemetry) {
  if (!(requester_permissions & TELEM_PERM_LOCATION)
      || !telemetryGpsDetected()) return false;

  unsigned long now = millis();
  if (gps_tracker_enabled) {
    // Ordinary telemetry can observe the cache, but may not create a two-hour
    // hold or acquire GPS outside the tracker's scheduled window.
    if (!gpsTelemetryCacheFresh(now)) return false;
    telemetry.addGPS(TELEM_CHANNEL_SELF, gps_cache_lat, gps_cache_lon, gps_cache_altitude);
    return true;
  }
  if (!gps_transport_available) {
    // A bridge may temporarily own the GPS UART. Authorized callers can still
    // receive the last good fix while it is inside the normal freshness bound,
    // but the query must not create a hold or try to reclaim the UART.
    if (!gpsTelemetryCacheFresh(now)) return false;
    telemetry.addGPS(TELEM_CHANNEL_SELF, gps_cache_lat, gps_cache_lon,
                     gps_cache_altitude);
    return true;
  }
  if (gps_stop_after_fix && !gps_user_enabled) {
    // Repeaters acquire only when the cached position is stale. The request
    // returns without GPS while acquisition is in progress; the next request
    // can use the cached fix without waking the receiver again.
    if (!gpsTelemetryCacheFresh(now) && !gps_acquiring) {
      beginGpsTelemetryAcquisition(now);
    }
    if (!gpsTelemetryCacheFresh(now)) return false;
    telemetry.addGPS(TELEM_CHANNEL_SELF, gps_cache_lat, gps_cache_lon,
                     gps_cache_altitude);
    return true;
  }
  gps_hold_until = now + GPS_TELEMETRY_HOLD_SEC * 1000UL;
  if (!telemetryGpsActive()) telemetryGpsStart();
  if (!gpsTelemetryCacheFresh(now) && !gps_acquiring) beginGpsTelemetryAcquisition(now);

  if (!gpsTelemetryCacheFresh(now)) return false;
  telemetry.addGPS(TELEM_CHANNEL_SELF, gps_cache_lat, gps_cache_lon, gps_cache_altitude);
  return true;
}

void SensorManager::processGpsTelemetryFix(float lat, float lon, float altitude, unsigned long now) {
  if (!gps_transport_available) return;
  if (gps_tracker_enabled) {
    if (!gps_acquiring) return;
    if (static_cast<uint32_t>(now - gps_acquire_started_at) >= GPS_TRACKER_MAX_ACQUIRE_MS) {
      finishGpsTelemetryAcquisition(now, false);
      return;
    }
    if (!isfinite(lat) || !isfinite(lon) || lat < -90.0f || lat > 90.0f
        || lon < -180.0f || lon > 180.0f || (lat == 0 && lon == 0)) return;
    updateGpsTelemetryCache(lat, lon, altitude, now);
    gps_tracker_fix_ready = true;
    LocationProvider* location = getLocationProvider();
    if (location != nullptr && location->getTimeSyncGeneration() != gps_tracker_sync_generation)
      finishGpsTelemetryAcquisition(now, false);
    return;
  }
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
    if (gps_stop_after_fix && !gps_user_enabled) {
      updateGpsTelemetryCache(lat, lon, altitude, now);
      finishGpsTelemetryAcquisition(now, false);
      return;
    }
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
  // Expire the validity bit even while a bridge owns the UART. A stale fix
  // must not become fresh again when the 32-bit millis counter cycles.
  if (gps_cache_valid && !gpsTelemetryCacheFresh(now)) gps_cache_valid = false;
  if (gps_tracker_enabled) {
    LocationProvider* location = getLocationProvider();
    if (location != nullptr && location->getGPSPowerSaving()) location->setGPSPowerSaving(false);
    if (!gps_transport_available) return;
    if (gps_acquiring
        && static_cast<uint32_t>(now - gps_acquire_started_at) >= GPS_TRACKER_MAX_ACQUIRE_MS) {
      finishGpsTelemetryAcquisition(now, false);
    }
    if (gps_acquiring && telemetryGpsActive() && location != nullptr && location->isValid()) {
      // Tracker freshness is independent of the saved telemetry gps_interval.
      // Derived managers have already polled this provider in their previous
      // loop; clearPositionFix() made any retained pre-window sample invalid.
      processGpsTelemetryFix(static_cast<float>(location->getLatitude()) / 1000000.0f,
                             static_cast<float>(location->getLongitude()) / 1000000.0f,
                             static_cast<float>(location->getAltitude()) / 1000.0f, now);
    }
    if (gps_acquiring && gps_tracker_fix_ready && location != nullptr
        && location->getTimeSyncGeneration() != gps_tracker_sync_generation)
      finishGpsTelemetryAcquisition(now, false);
    maybeStopGpsForTelemetry(now);
    return;
  }
  if (!gps_transport_available) return;
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
  if (gps_tracker_enabled) {
    LocationProvider* location = getLocationProvider();
    if (location != nullptr && location->getGPSPowerSaving()) location->setGPSPowerSaving(false);
    maybeStopGpsForTelemetry(now);
    return;
  }
  if (enabled) {
    if (gps_transport_available && telemetryGpsDetected()
        && !telemetryGpsActive()) telemetryGpsStart();
  } else {
    cancelGpsTelemetryDemand(now);
    maybeStopGpsForTelemetry(now);
  }
}

void SensorManager::setGpsTelemetryTransportAvailable(bool available) {
  if (gps_transport_available == available) return;

  gps_transport_available = available;
  if (!available) {
    LocationProvider* location = getLocationProvider();
    if (location != nullptr) location->endFreshTimeSync();
    // The UART is no longer ours. Cancel both the short acquisition and the
    // two-hour remote-query hold so neither can silently reclaim it from a
    // bridge. Preserve the user's preference and last good cache.
    gps_acquiring = false;
    gps_tracker_fix_ready = false;
    gps_acquire_has_fix = false;
    gps_hold_until = 0;
    gps_acquire_started_at = 0;
    gps_stable_started_at = 0;
    gps_weighted_lat = 0;
    gps_weighted_lon = 0;
    gps_weighted_altitude = 0;
    gps_weight_sum = 0;
    gps_weight_count = 0;
    return;
  }

  gps_next_cache_update_at = 0;
  if (!gps_tracker_enabled && gps_user_enabled && telemetryGpsDetected() && !telemetryGpsActive()) {
    telemetryGpsStart();
  }
}

void SensorManager::resetGpsTelemetryTransportState() {
  // Hardware discovery may be rerun after a bridge-owned UART was blocked.
  // Reset transient ownership without invoking a provider callback before the
  // new probe has established which provider, if any, is present.
  gps_transport_available = true;
  gps_acquiring = false;
  gps_tracker_fix_ready = false;
  gps_acquire_has_fix = false;
  gps_hold_until = 0;
  gps_acquire_started_at = 0;
  gps_stable_started_at = 0;
  gps_weighted_lat = 0;
  gps_weighted_lon = 0;
  gps_weighted_altitude = 0;
  gps_weight_sum = 0;
  gps_weight_count = 0;
  gps_next_cache_update_at = 0;
}
#endif

void SensorManager::setTelemetryLocationAccessAvailable(bool available) {
#if ENV_INCLUDE_GPS
  unsigned long now = millis();
  if (gps_location_access_available == available) return;

  gps_location_access_available = available;
  if (available) {
    gps_next_cache_update_at = 0;
  } else if (!gps_tracker_enabled && gps_acquiring && !gps_user_enabled && !gpsTelemetryHoldActive(now)) {
    gps_acquiring = false;
    maybeStopGpsForTelemetry(now);
  }
#else
  (void)available;
#endif
}
