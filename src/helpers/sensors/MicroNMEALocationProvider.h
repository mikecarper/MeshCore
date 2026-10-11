#pragma once

#include "LocationProvider.h"
#include <MicroNMEA.h>
#include <RTClib.h>
#include <helpers/RefCountedDigitalPin.h>
#include <helpers/sensors/GpsTimeValidation.h>
#include <string.h>

#ifndef GPS_LOOP_MAX_BYTES
    #define GPS_LOOP_MAX_BYTES 96
#endif

#ifndef GPS_EN
    #ifdef PIN_GPS_EN
        #define GPS_EN PIN_GPS_EN
    #else
        #define GPS_EN (-1)
    #endif
#endif

#ifndef GPS_EN_ACTIVE
    #ifdef PIN_GPS_EN_ACTIVE
        #define GPS_EN_ACTIVE PIN_GPS_EN_ACTIVE
    #else
        #define GPS_EN_ACTIVE HIGH
    #endif
#endif

#ifndef GPS_RESET
    #ifdef PIN_GPS_RESET
        #define GPS_RESET PIN_GPS_RESET
    #else
        #define GPS_RESET (-1)
    #endif
#endif

#ifndef GPS_RESET_ACTIVE
    #ifdef PIN_GPS_RESET_ACTIVE
        #define GPS_RESET_ACTIVE PIN_GPS_RESET_ACTIVE
    #else
        #define GPS_RESET_ACTIVE LOW
    #endif
#endif

class MicroNMEALocationProvider : public LocationProvider {
    char _nmeaBuffer[100];
    MicroNMEA nmea;
    mesh::RTCClock* _clock;
    Stream* _gps_serial;
    RefCountedDigitalPin* _peripher_power;
    int8_t _claims = 0;
    int _pin_reset;
    int _pin_en;
    unsigned long next_check = 0;
    long time_valid = 0;
    unsigned long _last_time_sync = 0;

#ifdef GPS_L76K
    void configureL76K() {
        // Reapply the constellation mode at each provider start instead of
        // assuming it survives a receiver power cycle.
        // Mode 7 enables GPS + BeiDou + GLONASS.
        delay(250);
        MicroNMEA::sendSentence(*_gps_serial, "$PCAS04,7");
        delay(250);
    }
#endif
    // Observation-only diagnostics. These fields do not change GPS power,
    // timing, reset behaviour, or parser input.
    uint32_t _uart_bytes = 0;
    uint32_t _nmea_checksum_ok = 0;
    uint32_t _nmea_checksum_bad = 0;
    uint32_t _begin_calls = 0;
    uint32_t _stop_calls = 0;
    unsigned long _last_uart_ms = 0;
    unsigned long _last_nmea_ms = 0;
    unsigned long _last_fix_ms = 0;
    bool _uart_seen = false;
    bool _nmea_seen = false;
    bool _fix_seen = false;
    bool _fresh_clock_sample = false;

    static uint32_t ageMs(unsigned long timestamp) {
        return (uint32_t)(millis() - timestamp);
    }

    static void formatAge(char* out, size_t out_size, bool seen, unsigned long timestamp) {
        if (!seen) {
            snprintf(out, out_size, "never");
        } else {
            snprintf(out, out_size, "%lu", (unsigned long)ageMs(timestamp));
        }
    }

public :
    MicroNMEALocationProvider(Stream& ser, mesh::RTCClock* clock = NULL, int pin_reset = GPS_RESET, int pin_en = GPS_EN,RefCountedDigitalPin* peripher_power=NULL) :
    nmea(_nmeaBuffer, sizeof(_nmeaBuffer)), _clock(clock), _gps_serial(&ser), _peripher_power(peripher_power), _pin_reset(pin_reset), _pin_en(pin_en) {
        if (_pin_reset != -1) {
            pinMode(_pin_reset, OUTPUT);
            digitalWrite(_pin_reset, GPS_RESET_ACTIVE);
        }
        if (_pin_en != -1) {
            pinMode(_pin_en, OUTPUT);
            digitalWrite(_pin_en, !GPS_EN_ACTIVE);
        }
    }

    void claim() {
        _claims++;
        if (_peripher_power) _peripher_power->claim();
    }

    void release() {
        if (_claims == 0) return; // avoid negative _claims
        _claims--;
        if (_peripher_power) _peripher_power->release();
    }

    void begin() override {
        _begin_calls++;
        claim();
        if (_pin_en != -1) {
            digitalWrite(_pin_en, GPS_EN_ACTIVE);
        }
        if (_pin_reset != -1) {
            digitalWrite(_pin_reset, !GPS_RESET_ACTIVE);
        }
    }

    void reset() override {
        if (_pin_reset != -1) {
            digitalWrite(_pin_reset, GPS_RESET_ACTIVE);
            delay(10);
            digitalWrite(_pin_reset, !GPS_RESET_ACTIVE);
        }
#ifdef GPS_L76K
        // EnvironmentSensorManager calls reset() immediately after begin().
        // Configure only after that reset so the setting is not discarded.
        configureL76K();
#endif
    }

    void stop() override {
        _stop_calls++;
        if (_pin_en != -1) {
            digitalWrite(_pin_en, !GPS_EN_ACTIVE);
        }
        if (_pin_reset != -1) {
            digitalWrite(_pin_reset, GPS_RESET_ACTIVE);
        }
        release();
    }

    bool isEnabled() override {
        // directly read the enable pin if present as gps can be
        // activated/deactivated outside of here ...
        if (_pin_en != -1) {
            return digitalRead(_pin_en) == GPS_EN_ACTIVE;
        } else {
            return true; // no enable so must be active
        }
    }

    void setPinEn(int pin_en) override {
        _pin_en = pin_en;
    }

    int getPinEn() override {
        return _pin_en;
    }

    void syncTime() override { nmea.clear(); LocationProvider::syncTime(); }
    void beginFreshTimeSync() override {
        time_valid = 0;
        _fresh_clock_sample = false;
        next_check = millis();
        LocationProvider::beginFreshTimeSync();
    }
    mesh::RTCClock* getRTCClock() override { return _clock; }
    long getLatitude() override { return nmea.getLatitude(); }
    long getLongitude() override { return nmea.getLongitude(); }
    long getAltitude() override { 
        long alt = 0;
        nmea.getAltitude(alt);
        return alt;
    }
    long satellitesCount() override { return nmea.getNumSatellites(); }
    bool isValid() override { return nmea.isValid(); }
    bool clearPositionFix() override {
        nmea.clear();
        // Discard only bytes already queued before the acquisition begins.
        // Snapshot the count so a noisy live UART cannot make this unbounded.
        int buffered = _gps_serial->available();
        if (buffered < 0 || buffered > 1024) return false;
        while (buffered-- > 0) {
            if (_gps_serial->read() < 0) return false;
        }
        return true;
    }

    long getTimestamp() override {
        const uint16_t year = nmea.getYear();
        const uint8_t month = nmea.getMonth();
        const uint8_t day = nmea.getDay();
        const uint8_t hour = nmea.getHour();
        const uint8_t minute = nmea.getMinute();
        const uint8_t second = nmea.getSecond();
        if (!mesh::gps::isValidNmeaDateTime(year, month, day, hour, minute,
                                            second)) {
            return 0;
        }
        DateTime dt(year, month, day, hour, minute, second);
        const uint32_t timestamp = dt.unixtime();
        return timestamp <= 0x7FFFFFFFUL ? static_cast<long>(timestamp) : 0;
    } 

    void drain() override { while (_gps_serial->available()) _gps_serial->read(); }

    const char* getSentence() const { return nmea.getSentence(); }

    void sendSentence(const char *sentence) override {
        nmea.sendSentence(*_gps_serial, sentence);
    }

    bool waitFor(const char* prefix, uint32_t timeout_ms) override {
        size_t plen = strlen(prefix);
        uint32_t timeout = millis() + timeout_ms;
        while ((int32_t)(millis() - timeout) < 0) {
            if (_gps_serial->available()) {
                char c = _gps_serial->read();
                #ifdef GPS_NMEA_DEBUG
                if (mesh::isUsbDebugLoggingEnabled()) mesh::usbLoggingPort().print(c);
                #endif
                // MicroNMEA also returns true for a complete sentence with a
                // bad checksum. A damaged ACK must not confirm GPS sleep.
                if (nmea.process(c) && MicroNMEA::testChecksum(nmea.getSentence())
                    && strncmp(nmea.getSentence(), prefix, plen) == 0) return true;
            } else {
                yield();
            }
        }
        return false;
    }

    void loop() override {

        size_t processed = 0;
        while (_gps_serial->available() && processed < GPS_LOOP_MAX_BYTES) {
            char c = _gps_serial->read();
            _uart_bytes++;
            _last_uart_ms = millis();
            _uart_seen = true;
            #ifdef GPS_NMEA_DEBUG
            if (mesh::isUsbDebugLoggingEnabled()) mesh::usbLoggingPort().print(c);
            #endif
            bool parsed = nmea.process(c);
            processed++;

            // MicroNMEA leaves the completed sentence in its buffer after the
            // first line terminator. CRLF therefore counts once: CR observes the
            // sentence, then LF clears the now-empty buffer.
            if ((c == '\0' || c == '\r' || c == '\n') && nmea.getSentence()[0] != '\0') {
                if (MicroNMEA::testChecksum(nmea.getSentence())) {
                    _nmea_checksum_ok++;
                    _last_nmea_ms = millis();
                    _nmea_seen = true;

                    // Refresh fix age only when a newly parsed GGA/RMC sentence
                    // carries a valid fix. Other valid NMEA sentences must not
                    // make an old position look fresh.
                    const char* message_id = nmea.getMessageID();
                    if (parsed && isValid() &&
                        (strcmp(message_id, "GGA") == 0 || strcmp(message_id, "RMC") == 0)) {
                        _last_fix_ms = millis();
                        _fix_seen = true;
                        _fresh_clock_sample = true;
                    }
                } else {
                    _nmea_checksum_bad++;
                }
            }
        }

        if (!isValid()) time_valid = 0;

        if ((long)(millis() - next_check) > 0) {
            next_check = millis() + 1000;
            // Re-enable time sync periodically when GPS has valid fix
            if (!_time_sync_needed && _clock != NULL
                && static_cast<uint32_t>(millis() - _last_time_sync)
                    >= periodicTimeSyncIntervalMillis()) {
                _time_sync_needed = true;
            }
            const long timestamp = getTimestamp();
            if (isValid() && satellitesCount() >= 5 && timestamp > 0
                && (!_fresh_time_sync_required || _fresh_clock_sample)) {
                time_valid++;
            } else {
                time_valid = 0;
            }
            _fresh_clock_sample = false;
            if (_time_sync_needed && time_valid > 2) {
                if (_clock != NULL) {
                    _clock->setCurrentTime(timestamp);
                    markTimeSyncApplied();
                    _time_sync_needed = false;
                    _last_time_sync = millis();
                    _last_valid_time_sync = _clock->getCurrentTime();
                }
            }
        }
    }

    void formatDiagnostics(char* out, size_t out_size) override {
        if (out_size == 0) return;

        char uart_age[11];
        char nmea_age[11];
        char fix_age[11];
        formatAge(uart_age, sizeof(uart_age), _uart_seen, _last_uart_ms);
        formatAge(nmea_age, sizeof(nmea_age), _nmea_seen, _last_nmea_ms);
        formatAge(fix_age, sizeof(fix_age), _fix_seen, _last_fix_ms);

        // Compact enough for CommonCLI's 160-byte reply buffer, including the
        // "req:" prefix added by CommonCLI.
        snprintf(out, out_size,
            "en:%u ub:%lu ua:%s ok:%lu bad:%lu na:%s sat:%ld fix:%u fa:%s bc:%lu sc:%lu",
            isEnabled() ? 1U : 0U,
            (unsigned long)_uart_bytes,
            uart_age,
            (unsigned long)_nmea_checksum_ok,
            (unsigned long)_nmea_checksum_bad,
            nmea_age,
            satellitesCount(),
            isValid() ? 1U : 0U,
            fix_age,
            (unsigned long)_begin_calls,
            (unsigned long)_stop_calls);
    }
};
