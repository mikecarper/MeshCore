#pragma once

#include "Mesh.h"


class LocationProvider {
protected:
    bool _time_sync_needed = true;
    bool _time_sync_applied = false;

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
