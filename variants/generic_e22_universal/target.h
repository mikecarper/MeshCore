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
    switch (universalLoRaRadioFamily()) {
      case mesh::LoRaRadioFamily::SX126x: return "Generic E22 SX1262";
      case mesh::LoRaRadioFamily::SX1268: return "Generic E22 SX1268";
      default: return "Generic E22 (radio undetected)";
    }
  }
};

extern UniversalE22Board board;
extern UniversalLoRaRadio radio_driver;
extern AutoDiscoverRTCClock rtc_clock;
extern SensorManager sensors;

bool radio_init();
mesh::LocalIdentity radio_new_identity();
