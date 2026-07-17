#pragma once

#include "Mesh.h"


class LocationProvider {
protected:
    bool _time_sync_needed = true;
    bool _time_sync_applied = false;
    bool _gps_powersaving_enabled = false;
    unsigned long _next_gps_off = 0;
    unsigned long _next_gps_on = 0;
    unsigned long _last_valid_time_sync = 0;

    void markTimeSyncApplied() { _time_sync_applied = true; }

public:
    virtual void syncTime() { _time_sync_needed = true; }
    virtual bool waitingTimeSync() { return _time_sync_needed; }
    // Edge-triggered notification for consumers that need to know a GPS time
    // was actually written, rather than merely seeing a valid location fix.
    bool consumeTimeSyncApplied() {
        bool applied = _time_sync_applied;
        _time_sync_applied = false;
        return applied;
    }
    virtual void stopTimeSync() { _time_sync_needed = false; }
    virtual void setGPSPowerSaving(bool enabled) { _gps_powersaving_enabled = enabled; _next_gps_off = 0; _next_gps_on = 0; }
    virtual bool getGPSPowerSaving() { return _gps_powersaving_enabled; }
    virtual void setNextGPSOff(unsigned long _millis) { _next_gps_off = _millis; }
    virtual unsigned long getNextGPSOff() { return _next_gps_off; }
    virtual void setNextGPSOn(unsigned long _millis) { _next_gps_on = _millis; }
    virtual unsigned long getNextGPSOn() { return _next_gps_on; }
    virtual unsigned long getLastValidTimeSync() { return _last_valid_time_sync; }
    virtual long getLatitude() = 0;
    virtual long getLongitude() = 0;
    virtual long getAltitude() = 0;
    virtual long satellitesCount() = 0;
    virtual bool isValid() = 0;
    virtual long getTimestamp() = 0;
    virtual void sendSentence(const char * sentence);
    virtual void reset() = 0;
    virtual void begin() = 0;
    virtual void stop() = 0;
    virtual void loop() = 0;
    virtual bool isEnabled() = 0;
};
