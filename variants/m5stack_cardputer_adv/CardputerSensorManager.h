#pragma once

#include <helpers/SensorManager.h>
#include <helpers/sensors/LocationProvider.h>
#if CARDPUTER_ADV_UI
using CardputerSensorBase = SensorManager;
#else
#include <helpers/sensors/EnvironmentSensorManager.h>
using CardputerSensorBase = EnvironmentSensorManager;
#endif

class CardputerSensorManager : public CardputerSensorBase {
private:
  bool gps_active = false;
  bool gps_serial_open = false;
  uint32_t next_gps_update = 0;
  uint32_t standby_renew_at = 0;
#if ENV_INCLUDE_GPS
  LocationProvider *_location;
  void armGpsPowerSavingCycle();

protected:
  bool telemetryGpsDetected() const override { return gps_serial_open; }
  bool telemetryGpsActive() const override { return gps_active; }
  void telemetryGpsStart() override { start_gps(); }
  void telemetryGpsStop() override { stop_gps(); }
#endif

public:
#if ENV_INCLUDE_GPS
#if CARDPUTER_ADV_UI
  CardputerSensorManager(LocationProvider &location) : _location(&location) {}
#else
  CardputerSensorManager(LocationProvider &location) : CardputerSensorBase(location), _location(&location) {}
#endif
#else
  CardputerSensorManager() = default;
#endif
  bool begin() override;
  bool querySensors(uint8_t requester_permissions, CayenneLPP &telemetry) override;
  void loop() override;
  int getNumSettings() const override;
  const char *getSettingName(int i) const override;
  const char *getSettingValue(int i) const override;
  bool setSettingValue(const char *name, const char *value) override;
  void setPowerSavingEnabled(bool enabled) override;
  bool isGPSDetected() const override {
#if ENV_INCLUDE_GPS
    return gps_serial_open;
#else
    return false;
#endif
  }
  // The cap has command-controlled standby, not a GPIO power switch. A
  // pinless LocationProvider::isEnabled() cannot report its logical state.
  bool isGpsPowered() const override { return gps_active; }
  bool gpsUsesSerialUart(uint8_t uart) const override { return uart == 1 && gps_serial_open; }
  // The cap cannot prove electrical silence without an enable pin. Do not
  // let the environmental base or a serial bridge release this fixed UART.
  bool gpsSerialTransportMayConflict(uint8_t uart) const override { return gpsUsesSerialUart(uart); }
  bool gpsSerialTransportCanYield(uint8_t) const override { return false; }
  bool setGpsSerialTransportBlocked(uint8_t, bool) override { return false; }
  void shutdownGpsTransportForDeepSleep() override;
  LocationProvider *getLocationProvider() override {
#if ENV_INCLUDE_GPS
    return _location;
#else
    return nullptr;
#endif
  }
#if ENV_INCLUDE_GPS
  void start_gps();
  void stop_gps();
#endif
  void gps_standby(bool active);
};
