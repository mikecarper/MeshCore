#pragma once

#include <helpers/radiolib/UniversalLoRaRadio.h>
#include <helpers/AutoDiscoverRTCClock.h>
#include <helpers/SensorManager.h>
#include "../meshadventurer/MeshadventurerBoard.h"
#include <helpers/sensors/LocationProvider.h>
class MASensorManager : public SensorManager {
  bool gps_active = false;
  LocationProvider * _location;

  void armGpsPowerSavingCycle();
  void start_gps();
  void stop_gps();
  bool telemetryGpsDetected() const override { return true; }
  bool telemetryGpsActive() const override { return gps_active; }
  void telemetryGpsStart() override { start_gps(); }
  void telemetryGpsStop() override { stop_gps(); }
public:
  MASensorManager(LocationProvider &location): _location(&location) { }
  bool begin() override;
  bool querySensors(uint8_t requester_permissions, CayenneLPP& telemetry) override;
  void loop() override;
  int getNumSettings() const override;
  const char* getSettingName(int i) const override;
  const char* getSettingValue(int i) const override;
  bool setSettingValue(const char* name, const char* value) override;
  void setPowerSavingEnabled(bool enabled) override;
  LocationProvider* getLocationProvider() override { return _location; }
};

#ifdef DISPLAY_CLASS
#include <helpers/ui/SSD1306Display.h>
#include <helpers/ui/MomentaryButton.h>
#endif

bool prepareUniversalLoRaRadio();
mesh::LoRaRadioFamily universalLoRaRadioFamily();

class UniversalMeshadventurerBoard : public MeshadventurerBoard {
 public:
  void begin() {
    MeshadventurerBoard::begin();
    prepareUniversalLoRaRadio();
  }
  const char* getManufacturerName() const override {
#if defined(MESH_E22_M33S)
    switch (universalLoRaRadioFamily()) {
      case mesh::LoRaRadioFamily::SX126x: return "Meshadventurer 900M33S";
      case mesh::LoRaRadioFamily::SX1268: return "Meshadventurer 400M33S";
      default: return "Meshadventurer M33S (radio undetected)";
    }
#else
    switch (universalLoRaRadioFamily()) {
      case mesh::LoRaRadioFamily::SX126x: return "Meshadventurer SX1262";
      case mesh::LoRaRadioFamily::SX1268: return "Meshadventurer SX1268";
      default: return "Meshadventurer (radio undetected)";
    }
#endif
  }
};

extern UniversalMeshadventurerBoard board;
extern UniversalLoRaRadio radio_driver;
extern AutoDiscoverRTCClock rtc_clock;
extern MASensorManager sensors;

#ifdef DISPLAY_CLASS
extern DISPLAY_CLASS display;
extern MomentaryButton user_btn;
#endif

bool radio_init();
mesh::LocalIdentity radio_new_identity();
