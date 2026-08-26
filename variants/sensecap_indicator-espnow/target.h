#pragma once

#include <helpers/ESP32Board.h>
#include <helpers/SensorManager.h>
#include <helpers/sensors/EnvironmentSensorManager.h>
#ifdef SENSECAP_INDICATOR_LORA
  #define RADIOLIB_STATIC_ONLY 1
  #include <RadioLib.h>
  #include <helpers/radiolib/CustomSX1262Wrapper.h>
  #include "IndicatorRadioHal.h"
  #include "IndicatorSX1262Wrapper.h"
#else
  #include <helpers/esp32/ESPNOWRadio.h>
#endif
#ifdef ENV_INCLUDE_GPS
  #include <helpers/sensors/MicroNMEALocationProvider.h>
#endif
#ifdef DISPLAY_CLASS
  #include "SCIndicatorDisplay.h"
  #include <helpers/ui/MomentaryButton.h>
#endif

extern ESP32Board board;
#ifdef SENSECAP_INDICATOR_LORA
  extern WRAPPER_CLASS radio_driver;
#else
  extern ESPNOWRadio radio_driver;
#endif
extern ESP32RTCClock rtc_clock;
extern EnvironmentSensorManager sensors;

#ifdef DISPLAY_CLASS
  extern DISPLAY_CLASS display;
  extern MomentaryButton user_btn;
#endif

bool radio_init();
#ifdef RECOVERABLE_EXTERNAL_RADIO
uint32_t radio_fallback_rng_seed();
#endif
mesh::LocalIdentity radio_new_identity();
