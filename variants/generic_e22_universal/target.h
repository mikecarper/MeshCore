#pragma once

#include <helpers/radiolib/UniversalLoRaRadio.h>
#include <helpers/AutoDiscoverRTCClock.h>
#include <helpers/SensorManager.h>
#include <helpers/ESP32Board.h>

bool prepareUniversalLoRaRadio();
mesh::LoRaRadioFamily universalLoRaRadioFamily();

class UniversalE22Board : public ESP32Board {
 public:
  void begin() {
    ESP32Board::begin();
    prepareUniversalLoRaRadio();
  }
  const char* getManufacturerName() const override {
#if defined(MESH_E22_M33S)
    switch (universalLoRaRadioFamily()) {
      case mesh::LoRaRadioFamily::SX126x: return "Generic E22 900M33S";
      case mesh::LoRaRadioFamily::SX1268: return "Generic E22 400M33S";
      default: return "Generic E22 M33S (radio undetected)";
    }
#else
    switch (universalLoRaRadioFamily()) {
      case mesh::LoRaRadioFamily::SX126x: return "Generic E22 SX1262";
      case mesh::LoRaRadioFamily::SX1268: return "Generic E22 SX1268";
      default: return "Generic E22 (radio undetected)";
    }
#endif
  }
};

extern UniversalE22Board board;
extern UniversalLoRaRadio radio_driver;
extern AutoDiscoverRTCClock rtc_clock;
extern SensorManager sensors;

bool radio_init();
mesh::LocalIdentity radio_new_identity();
