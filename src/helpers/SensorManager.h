#pragma once

#include <CayenneLPP.h>
#include "GpsPowerPolicy.h"
#include "sensors/LocationProvider.h"
#include <string.h>
#include <Wire.h>

#define TELEM_PERM_BASE         0x01   // 'base' permission includes battery
#define TELEM_PERM_LOCATION     0x02
#define TELEM_PERM_ENVIRONMENT  0x04   // permission to access environment sensors

#define TELEM_CHANNEL_SELF   1   // LPP data channel for 'self' device

class SensorManager {
#if ENV_INCLUDE_GPS
  bool gps_cache_valid = false;
  bool gps_location_access_available = false;
  bool gps_transport_available = true;
  bool gps_user_enabled = false;
  bool gps_acquiring = false;
  bool gps_acquire_has_fix = false;
  float gps_cache_lat = 0;
  float gps_cache_lon = 0;
  float gps_cache_altitude = 0;
  float gps_stable_origin_lat = 0;
  float gps_stable_origin_lon = 0;
  float gps_weighted_lat = 0;
  float gps_weighted_lon = 0;
  float gps_weighted_altitude = 0;
  float gps_weight_sum = 0;
  uint16_t gps_weight_count = 0;
  unsigned long gps_cache_updated_at = 0;
  unsigned long gps_next_cache_update_at = 0;
  unsigned long gps_hold_until = 0;
  unsigned long gps_acquire_started_at = 0;
  unsigned long gps_stable_started_at = 0;
  uint32_t gps_update_interval_sec = 0;

  bool gpsTelemetryHoldActive(unsigned long now) const;
  bool gpsTelemetryCacheFresh(unsigned long now) const;
  void beginGpsTelemetryAcquisition(unsigned long now);
  void finishGpsTelemetryAcquisition(unsigned long now, bool use_weighted_average);
  void updateGpsTelemetryCache(float lat, float lon, float altitude, unsigned long now);
  void maybeStopGpsForTelemetry(unsigned long now);

protected:
  virtual bool telemetryGpsDetected() const { return false; }
  virtual bool telemetryGpsActive() const { return false; }
  virtual void telemetryGpsStart() { }
  virtual void telemetryGpsStop() { }
  bool queryGpsTelemetry(uint8_t requester_permissions, CayenneLPP& telemetry);
  void processGpsTelemetryFix(float lat, float lon, float altitude, unsigned long now);
  void loopGpsTelemetry(unsigned long now);
  void setGpsTelemetryUserEnabled(bool enabled);
  void setGpsTelemetryTransportAvailable(bool available);
  void resetGpsTelemetryTransportState();
  bool isGpsTelemetryUserEnabled() const { return gps_user_enabled; }
  bool isGpsTelemetryTransportAvailable() const {
    return gps_transport_available;
  }
  bool gpsTelemetryReceiverRequired(unsigned long now) const {
    return gps_acquiring || gpsTelemetryHoldActive(now);
  }
  bool setGpsUpdateIntervalValue(const char* value) {
    return mesh::gps::parseUpdateInterval(value, gps_update_interval_sec);
  }
  uint32_t getGpsUpdateIntervalMillis() const {
    return mesh::gps::updateIntervalMillis(gps_update_interval_sec);
  }
#endif

public:
  struct VoltageSensorReading {
    uint8_t channel;
    float voltage;
    bool valid;
  };

  double node_lat, node_lon;  // modify these, if you want to affect Advert location
  double node_altitude;       // altitude in meters
  bool powersaving_enabled;   // powersaving mode

  SensorManager() { node_lat = 0; node_lon = 0; node_altitude = 0; powersaving_enabled = false; }
  virtual bool i2c_probe(TwoWire& wire, uint8_t addr) { return false; }
  virtual bool begin() { return false; }
  virtual bool querySensors(uint8_t requester_permissions, CayenneLPP& telemetry) { return false; }
  virtual uint8_t getVoltageSensorChannels(uint8_t channels[],
                                           uint8_t capacity) const {
    (void)channels;
    (void)capacity;
    return 0;
  }
  virtual uint8_t queryVoltageSensors(VoltageSensorReading readings[],
                                      uint8_t capacity) {
    (void)readings;
    (void)capacity;
    return 0;
  }
  virtual void loop() { }
  virtual void setTelemetryLocationAccessAvailable(bool available);
  virtual int getNumSettings() const { return 0; }
  virtual const char* getSettingName(int i) const { return NULL; }
  virtual const char* getSettingValue(int i) const { return NULL; }
  virtual bool setSettingValue(const char* name, const char* value) {
#if ENV_INCLUDE_GPS
    if (strcmp(name, "gps_interval") == 0) {
      return setGpsUpdateIntervalValue(value);
    }
#else
    (void)name;
    (void)value;
#endif
    return false;
  }
  virtual LocationProvider* getLocationProvider() { return NULL; }
  virtual void setPowerSavingEnabled(bool enabled) { powersaving_enabled = enabled; }
  // Runtime bridges may temporarily own the UART used by a GPS receiver. A
  // sensor manager that actually detected a GPS on that UART overrides these
  // hooks; I2C GPS providers and boards without GPS remain unaffected.
  virtual bool gpsUsesSerialUart(uint8_t uart) const {
    (void)uart;
    return false;
  }
  // A universal image may need to reserve a UART even when a receiver did not
  // speak during its bounded boot probe. The default has no such uncertainty;
  // hardware-specific managers can fail closed for a known shared connector.
  virtual bool gpsSerialTransportMayConflict(uint8_t uart) const {
    return gpsUsesSerialUart(uart);
  }
  // True only when the detected receiver can be made electrically silent on
  // the selected UART. Stopping the MCU UART alone is not sufficient when a
  // still-powered GPS continues driving its TX pin.
  virtual bool gpsSerialTransportCanYield(uint8_t uart) const {
    (void)uart;
    return false;
  }
  virtual bool setGpsSerialTransportBlocked(uint8_t uart, bool blocked) {
    (void)uart;
    (void)blocked;
    return false;
  }

  // Helper functions to manage setting by keys (useful in many places ...)
  const char* getSettingByKey(const char* key) {
    int num = getNumSettings();
    for (int i = 0; i < num; i++) {
      if (strcmp(getSettingName(i), key) == 0) {
        return getSettingValue(i);
      }
    }
    return NULL;
  }
};
