#pragma once

#include <helpers/radiolib/UniversalLoRaRadio.h>
#include <helpers/esp32/TBeamBoard.h>
#include <helpers/AutoDiscoverRTCClock.h>
#include <helpers/sensors/EnvironmentSensorManager.h>
#ifdef DISPLAY_CLASS
#include <helpers/ui/SSD1306Display.h>
#include <helpers/ui/MomentaryButton.h>
#endif

bool prepareUniversalLoRaRadio();
mesh::LoRaRadioFamily universalLoRaRadioFamily();

class UniversalTBeamBoard : public TBeamBoard {
public:
  uint32_t getIRQGpio() override {
    switch (universalLoRaRadioFamily()) {
      case mesh::LoRaRadioFamily::SX126x: return P_LORA_DIO_1;
      case mesh::LoRaRadioFamily::SX127x: return P_LORA_DIO_0;
      default: return static_cast<uint32_t>(-1);
    }
  }

  const char* getManufacturerName() const override {
    switch (universalLoRaRadioFamily()) {
      case mesh::LoRaRadioFamily::SX126x: return "LilyGo T-Beam SX126x";
      case mesh::LoRaRadioFamily::SX127x: return "LilyGo T-Beam SX127x";
      default: return "LilyGo T-Beam (radio undetected)";
    }
  }
};

extern UniversalTBeamBoard board;
extern UniversalLoRaRadio radio_driver;
extern AutoDiscoverRTCClock rtc_clock;
extern EnvironmentSensorManager sensors;

#ifdef DISPLAY_CLASS
extern DISPLAY_CLASS display;
extern MomentaryButton user_btn;
#endif

bool radio_init();
mesh::LocalIdentity radio_new_identity();
