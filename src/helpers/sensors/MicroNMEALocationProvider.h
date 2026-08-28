#pragma once

#include "LocationProvider.h"
#include <MicroNMEA.h>
#include <RTClib.h>
#include <helpers/RefCountedDigitalPin.h>
#include <helpers/sensors/GpsTimeValidation.h>

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
    static const unsigned long TIME_SYNC_INTERVAL = 1800000; // Re-sync every 30 minutes

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
    }

    void stop() override {
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

    void sendSentence(const char *sentence) override {
        nmea.sendSentence(*_gps_serial, sentence);
    }

    void loop() override {

        size_t processed = 0;
        while (_gps_serial->available() && processed < GPS_LOOP_MAX_BYTES) {
            char c = _gps_serial->read();
            #ifdef GPS_NMEA_DEBUG
            if (mesh::isUsbLoggingEnabled()) mesh::usbLoggingPort().print(c);
            #endif
            nmea.process(c);
            processed++;
        }

        if (!isValid()) time_valid = 0;

        if ((long)(millis() - next_check) > 0) {
            next_check = millis() + 1000;
            // Re-enable time sync periodically when GPS has valid fix
            if (!_time_sync_needed && _clock != NULL && (millis() - _last_time_sync) > TIME_SYNC_INTERVAL) {
                _time_sync_needed = true;
            }
            const long timestamp = getTimestamp();
            if (isValid() && satellitesCount() >= 5 && timestamp > 0) {
                time_valid++;
            } else {
                time_valid = 0;
            }
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
};
