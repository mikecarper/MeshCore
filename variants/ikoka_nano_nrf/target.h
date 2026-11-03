#pragma once

#define RADIOLIB_STATIC_ONLY 1
#include <RadioLib.h>
#include <helpers/radiolib/RadioLibWrappers.h>
#include <IkokaNanoNRFBoard.h>
#if defined(USE_SX1268)
#include <helpers/radiolib/CustomSX1268Wrapper.h>
#else
#include <helpers/radiolib/CustomSX1262Wrapper.h>
#endif
#include <helpers/AutoDiscoverRTCClock.h>
#include <helpers/ArduinoHelpers.h>
#include <helpers/sensors/EnvironmentSensorManager.h>

#ifdef DISPLAY_CLASS
  #include <helpers/ui/NullDisplayDriver.h>
  #include <helpers/ui/MomentaryButton.h>
  extern DISPLAY_CLASS display;
  // extern MomentaryButton user_btn;
#endif

extern IkokaNanoNRFBoard board;
extern WRAPPER_CLASS radio_driver;
extern AutoDiscoverRTCClock rtc_clock;
extern EnvironmentSensorManager sensors;

bool radio_init();
mesh::LocalIdentity radio_new_identity();

