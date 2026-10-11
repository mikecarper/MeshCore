#pragma once

#include "Mesh.h"
#include <helpers/GpsPowerPolicy.h>
#include <stddef.h>
#include <stdio.h>
#include <stdint.h>

#ifndef GPS_POWERSAVING_ON_DURATION_SECS
#define GPS_POWERSAVING_ON_DURATION_SECS (10UL * 60UL)
#endif

#ifndef GPS_POWERSAVING_OFF_DURATION_SECS
#define GPS_POWERSAVING_OFF_DURATION_SECS (24UL * 60UL * 60UL)
#endif

class LocationProvider {
protected:
    bool _time_sync_needed = true;
    bool _time_sync_applied = false;
    bool _gps_powersaving_enabled = false;
    unsigned long _next_gps_off = 0;
    unsigned long _next_gps_on = 0;
    unsigned long _gps_on_duration_secs = GPS_POWERSAVING_ON_DURATION_SECS;
    unsigned long _gps_off_duration_secs = GPS_POWERSAVING_OFF_DURATION_SECS;
    uint16_t _gps_sync_interval_hours = 0; // 0 retains the board's legacy policy
    unsigned long _last_valid_time_sync = 0;
    uint32_t _last_time_sync_request_ms = 0;
    uint32_t _last_time_sync_applied_ms = 0;
    bool _time_sync_request_seen = false;
    bool _time_sync_applied_seen = false;
    bool _fresh_time_sync_required = false;
    uint32_t _time_sync_generation = 0;

    void markTimeSyncApplied() {
        _time_sync_applied = true;
        ++_time_sync_generation;
        _fresh_time_sync_required = false;
        _last_time_sync_applied_ms = static_cast<uint32_t>(millis());
        _time_sync_applied_seen = true;
    }
    void resetTimeSyncRequestState() {
        _last_time_sync_request_ms = 0;
        _last_time_sync_applied_ms = 0;
        _time_sync_request_seen = false;
        _time_sync_applied_seen = false;
    }

public:
    void setTimeSyncIntervalHours(uint16_t hours) {
        _gps_sync_interval_hours = hours > mesh::gps::MAX_SYNC_INTERVAL_HOURS
            ? mesh::gps::MAX_SYNC_INTERVAL_HOURS : hours;
        // A sleeping receiver must adopt a shorter/longer setting now, not
        // wait for its old (possibly week-long) deadline. No forced GPS wake.
        if (_next_gps_on != 0) setNextWake();
    }
    uint16_t getTimeSyncIntervalHours() const { return _gps_sync_interval_hours; }
    uint32_t periodicTimeSyncIntervalMillis() const {
        return _gps_sync_interval_hours != 0
            ? static_cast<uint32_t>(_gps_sync_interval_hours) * 3600000UL
            : 1800000UL; // existing always-on receiver policy: 30 minutes
    }
    // Position telemetry may power the receiver between scheduled clock
    // acquisitions. Do not turn that into an early clock-sync request.
    void syncTimeForPowerSavingCycle() {
        if (_gps_sync_interval_hours == 0) syncTime();
        else (void)requestTimeSync(static_cast<uint64_t>(_gps_sync_interval_hours) * 3600UL);
    }
    virtual void syncTime() { _time_sync_needed = true; }
    // A bounded tracker window needs new validated samples even when an older
    // clock sync is still waiting to be consumed by another component.
    virtual void beginFreshTimeSync() {
        _fresh_time_sync_required = true;
        syncTime();
    }
    void endFreshTimeSync() { _fresh_time_sync_required = false; }
    uint32_t getTimeSyncGeneration() const { return _time_sync_generation; }
    virtual bool waitingTimeSync() { return _time_sync_needed; }
    // Telemetry requests share an in-progress acquisition and rate-limit new
    // ones using monotonic time, not an RTC which GPS/manual sync may correct.
    // Direct syncTime() callers (startup, CLI, power-cycle policy) remain force
    // requests. The argument is seconds, separately from position gps_interval.
    bool requestTimeSync(uint64_t min_interval_secs) {
        if (waitingTimeSync()) return false;
        // Keep the interval below half the 32-bit millis range. Clamp before
        // multiplying so even unusually large build overrides cannot overflow.
        const uint32_t max_interval_ms = 0x7FFFFFFFUL;
        const uint32_t interval_ms = min_interval_secs > max_interval_ms / 1000UL
            ? max_interval_ms : static_cast<uint32_t>(min_interval_secs) * 1000UL;
        const uint32_t now = static_cast<uint32_t>(millis());
        if ((_time_sync_applied_seen
             && static_cast<uint32_t>(now - _last_time_sync_applied_ms) < interval_ms)
            || (_time_sync_request_seen
                && static_cast<uint32_t>(now - _last_time_sync_request_ms) < interval_ms)) {
            return false;
        }
        _last_time_sync_request_ms = now;
        _time_sync_request_seen = true;
        syncTime();
        return true;
    }
    // Edge-triggered notification for consumers that need to know a GPS time
    // was actually written, rather than merely seeing a valid location fix.
    bool consumeTimeSyncApplied() {
        bool applied = _time_sync_applied;
        _time_sync_applied = false;
        return applied;
    }
    virtual void stopTimeSync() { _time_sync_needed = false; }
    virtual void setGPSPowerSaving(bool enabled) {
        _gps_powersaving_enabled = enabled;
        _next_gps_off = 0;
        _next_gps_on = 0;
    }
    virtual bool getGPSPowerSaving() { return _gps_powersaving_enabled; }
    virtual void setNextGPSOff(unsigned long _millis) { _next_gps_off = _millis; }
    virtual unsigned long getNextGPSOff() { return _next_gps_off; }
    virtual void setNextGPSOn(unsigned long _millis) { _next_gps_on = _millis; }
    virtual unsigned long getNextGPSOn() { return _next_gps_on; }

    virtual void setPowerSavingProfile(unsigned long wake_duration_secs, unsigned long sleep_duration_secs) {
        _gps_on_duration_secs = wake_duration_secs;
        _gps_off_duration_secs = sleep_duration_secs;
    }

    // Compatibility names used by the PowerSaving branch. The existing GPS
    // names remain canonical so older targets and stored behavior stay intact.
    virtual void enablePowerSaving(bool enabled) { setGPSPowerSaving(enabled); }
    virtual bool isPowerSavingEnabled() { return getGPSPowerSaving(); }
    virtual void setNextWake() {
        const uint32_t now = static_cast<uint32_t>(millis());
        uint32_t wait_ms = _gps_off_duration_secs * 1000UL;
        if (_gps_sync_interval_hours != 0) {
            wait_ms = periodicTimeSyncIntervalMillis();
            // Anchor to clock acquisition, not an unrelated location query's
            // power-off. Repeated position queries must not postpone sync.
            uint32_t age = UINT32_MAX;
            if (_time_sync_applied_seen) age = now - _last_time_sync_applied_ms;
            if (_time_sync_request_seen) {
                const uint32_t request_age = now - _last_time_sync_request_ms;
                if (request_age < age) age = request_age;
            }
            if (age < wait_ms) wait_ms -= age;
            // A failed/timed-out acquisition gets a full interval backoff,
            // rather than immediately powering back on in a retry loop.
        }
        const uint32_t deadline = now + wait_ms;
        setNextGPSOn(deadline == 0 ? 1 : deadline); // 0 means no deadline
    }
    virtual unsigned long getNextWake() { return getNextGPSOn(); }
    virtual void setNextSleep() {
        setNextGPSOff(millis() + _gps_on_duration_secs * 1000UL);
    }
    virtual unsigned long getNextSleep() { return getNextGPSOff(); }
    virtual unsigned long getLastValidTimeSync() { return _last_valid_time_sync; }
    virtual mesh::RTCClock* getRTCClock() { return NULL; }
    virtual long getLatitude() = 0;
    virtual long getLongitude() = 0;
    virtual long getAltitude() = 0;
    virtual long satellitesCount() = 0;
    virtual bool isValid() = 0;
    // Invalidate a previous position without resetting receiver ephemeris or
    // clock policy. Call before starting a bounded fresh acquisition. Unknown
    // providers fail closed rather than presenting retained data as new.
    virtual bool clearPositionFix() { return false; }
    virtual long getTimestamp() = 0;
    virtual void sendSentence(const char * sentence);
    virtual bool waitFor(const char* prefix, uint32_t timeout_ms) { return false; }
    virtual void drain() { }
    virtual void reset() = 0;
    virtual void begin() = 0;
    virtual void stop() = 0;
    virtual void loop() = 0;
    virtual bool isEnabled() = 0;
    virtual void setPinEn(int pin_en) { (void)pin_en; }
    virtual int getPinEn() { return -1; }

    // Format compact diagnostics in the caller-provided buffer. Providers that
    // have more information (for example UART counters) can override this.
    virtual void formatDiagnostics(char* out, size_t out_size) {
        if (out_size == 0) return;
        snprintf(out, out_size, "en:%u sat:%ld fix:%u",
            isEnabled() ? 1U : 0U,
            satellitesCount(),
            isValid() ? 1U : 0U);
    }
};
