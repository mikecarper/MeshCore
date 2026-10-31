#pragma once

#include <helpers/radiolib/UniversalLoRaRadio.h>
#include <helpers/AutoDiscoverRTCClock.h>
#include <helpers/SensorManager.h>
#include "../xiao_c3/XiaoC3Board.h"

bool prepareUniversalLoRaRadio();
mesh::LoRaRadioFamily universalLoRaRadioFamily();

class UniversalTenstarC3Board : public XiaoC3Board {
 public:
  void begin() {
    XiaoC3Board::begin();
    prepareUniversalLoRaRadio();
  }
  const char* getManufacturerName() const override {
    switch (universalLoRaRadioFamily()) {
      case mesh::LoRaRadioFamily::SX126x: return "Tenstar C3 SX1262";
      case mesh::LoRaRadioFamily::SX1268: return "Tenstar C3 SX1268";
      default: return "Tenstar C3 (radio undetected)";
    }
  }
};

extern UniversalTenstarC3Board board;
extern UniversalLoRaRadio radio_driver;
extern AutoDiscoverRTCClock rtc_clock;
extern SensorManager sensors;

bool radio_init();
mesh::LocalIdentity radio_new_identity();
