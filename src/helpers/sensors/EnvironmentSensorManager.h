#pragma once

#include <Mesh.h>
#include <helpers/SensorManager.h>
#include <helpers/sensors/LocationProvider.h>

class EnvironmentSensorManager : public SensorManager {
protected:
  static const int MAX_ACTIVE_SENSORS = 16;

  // Query function pointer + sub-channel index (for multi-channel sensors like INA3221).
  // Sub-channel is 0 for all single-output sensors.
  struct ActiveSensor {
    void    (*query)(uint8_t channel, uint8_t sub_channel, CayenneLPP& telemetry);
    bool    (*query_voltage)(uint8_t sub_channel, float& voltage);
    uint8_t   sub_channel;
  };

  ActiveSensor _active_sensors[MAX_ACTIVE_SENSORS];
  int          _active_sensor_count = 0;
  uint8_t      next_available_channel = TELEM_CHANNEL_SELF + 1;

  bool     gps_detected = false;
  bool     gps_active = false;

  #if ENV_INCLUDE_GPS
  LocationProvider* _location;
  void armGpsPowerSavingCycle();
  void start_gps();
  void stop_gps();
  bool telemetryGpsDetected() const override { return gps_detected; }
  bool telemetryGpsActive() const override { return gps_active; }
  void telemetryGpsStart() override { start_gps(); }
  void telemetryGpsStop() override { stop_gps(); }
  void initBasicGPS();
  #ifdef RAK_BOARD
  void rakGPSInit();
  bool gpsIsAwake(uint8_t ioPin);
  #endif
  #endif

public:
  #if ENV_INCLUDE_GPS
  EnvironmentSensorManager(LocationProvider &location): _location(&location){};
  LocationProvider* getLocationProvider() { return _location; }
  #else
  EnvironmentSensorManager(){};
  #endif
  bool i2c_probe(TwoWire& wire, uint8_t addr) override;
  bool begin() override;
  bool querySensors(uint8_t requester_permissions, CayenneLPP& telemetry) override;
  uint8_t getVoltageSensorChannels(uint8_t channels[],
                                   uint8_t capacity) const override;
  uint8_t queryVoltageSensors(VoltageSensorReading readings[],
                              uint8_t capacity) override;
  #if ENV_INCLUDE_GPS || defined(ENV_INCLUDE_BME680_BSEC)
  void loop() override;
  #endif
  int getNumSettings() const override;
  const char* getSettingName(int i) const override;
  const char* getSettingValue(int i) const override;
  bool setSettingValue(const char* name, const char* value) override;
  void setPowerSavingEnabled(bool enabled) override;
};
