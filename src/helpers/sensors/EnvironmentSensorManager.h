#pragma once

#include <Mesh.h>
#include <helpers/SensorManager.h>
#include <helpers/sensors/LocationProvider.h>
#if defined(RAK_BOARD) && !defined(RAK_WISMESH_TAG)
#include "WisBlockSharedPowerPolicy.h"
#endif

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
  bool     gps_serial_transport = false;
  bool     gps_serial_transport_blocked = false;
#if defined(RAK_BOARD) && !defined(RAK_WISMESH_TAG)
  mesh::gps::WisBlockSharedPowerPolicy _wisblock_power;
  bool _wisblock_discovery_completed = false;
  void applyWisBlockRailPower();
  uint8_t detectWisBlockStorageHolds();
#endif
  bool finishSensorDiscovery() {
#if defined(RAK_BOARD) && !defined(RAK_WISMESH_TAG)
#if ENV_INCLUDE_GPS
    // A repeated probe must not override the loaded manual preference or an
    // established telemetry demand. Initial boot waits for preference load.
    if (_wisblock_discovery_completed && _wisblock_power.isConfigured() && gps_active
        && !isGpsTelemetryUserEnabled()
        && !gpsTelemetryReceiverRequired(millis())) stop_gps();
#endif
    _wisblock_power.setGpsActive(gps_active);
    _wisblock_discovery_completed = true;
    _wisblock_power.setDiscoveryActive(false);
    applyWisBlockRailPower();
#endif
    return true;
  }

  #if ENV_INCLUDE_GPS
  #if (defined(ESP32_PLATFORM) || defined(NRF52_PLATFORM)) \
    && !defined(RAK_WISBLOCK_GPS) \
    && !(defined(RAK_BOARD) && !defined(RAK_WISMESH_TAG))
  bool gps_discovery_pending = false;
  bool gps_discovery_preference_known = false;
  uint32_t gps_discovery_started_at = 0;
  void finishBasicGpsDiscovery(bool found);
  void serviceBasicGpsDiscovery();
  #endif
  LocationProvider* _location;
  LocationProvider* _configured_location;
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
  bool gpsIsAwake(uint8_t ioPin, bool shared_power_rail);
  #endif
  #endif

public:
  #if ENV_INCLUDE_GPS
  EnvironmentSensorManager(LocationProvider &location):
      _location(&location), _configured_location(&location) {};
  LocationProvider* getLocationProvider() { return _location; }
  bool isGPSDetected() const override { return gps_detected; }
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
  bool supportsSharedPeripheralMode() const override;
  void configureSharedPeripheralMode(bool shared) override;
  bool canRemoveSharedStoragePowerForDeepSleep() const override;
  bool isGpsPowered() const override;
  bool gpsUsesSerialUart(uint8_t uart) const override;
  bool gpsSerialTransportMayConflict(uint8_t uart) const override;
  bool gpsSerialTransportCanYield(uint8_t uart) const override;
  bool setGpsSerialTransportBlocked(uint8_t uart, bool blocked) override;
  void shutdownGpsTransportForDeepSleep() override;
};
