#pragma once

#include "SensorManager.h"
#include <RTClib.h>

namespace mesh { namespace gps {

inline void formatCliStatus(SensorManager& sensors, bool power_saving, char* reply, size_t capacity) {
  if (!capacity) return;
  LocationProvider* gps = sensors.getLocationProvider();
  if (!gps) {
    snprintf(reply, capacity, "Can't find GPS");
    return;
  }

  const char* power = sensors.isGpsPowered() ? "powered" : "unpowered";
  const char* setting = sensors.getSettingByKey("gps");
  if (!setting || strcmp(setting, "1") != 0) {
    snprintf(reply, capacity, "off, %s", power);
    return;
  }

  const char* fix = gps->isValid() ? "fix" : "no fix";
  const int sats = gps->satellitesCount();
  if (!power_saving || !gps->getGPSPowerSaving()) {
    snprintf(reply, capacity, "on, %s, %s, %d sats", power, fix, sats);
    return;
  }

  const uint32_t off = gps->getNextGPSOff();
  const uint32_t deadline = off ? off : gps->getNextGPSOn();
  const int32_t remaining = deadline ? int32_t(deadline - uint32_t(millis())) : 0;
  const unsigned mins = remaining > 0 ? unsigned(remaining) / 60000 : 0;
  if (off) {
    snprintf(reply, capacity, "on, %s (powersaving, sleep in %uh %um), %s, %d sats",
             power, mins / 60, mins % 60, fix, sats);
  } else {
    snprintf(reply, capacity, "on, %s (powersaving, wake in %uh %um)", power, mins / 60, mins % 60);
  }
  const size_t used = strlen(reply);
  const uint32_t sync = gps->getLastValidTimeSync();
  if (sync) {
    const DateTime dt(sync);
    snprintf(reply + used, capacity - used, ", last sync: %02d:%02d - %d/%d/%d UTC",
             dt.hour(), dt.minute(), dt.day(), dt.month(), dt.year());
  } else {
    snprintf(reply + used, capacity - used, ", last sync: none");
  }
}

} }
