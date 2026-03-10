#pragma once

#define RADIOLIB_STATIC_ONLY 1
#include <RadioLib.h>
#include <helpers/radiolib/RadioLibWrappers.h>
#include <XiaoNrf52Board.h>
#include <helpers/radiolib/CustomSX1262Wrapper.h>
#include <helpers/ArduinoHelpers.h>
#include <helpers/sensors/EnvironmentSensorManager.h>

#ifndef DISABLE_I2C_RTC_SCAN
  #include <helpers/AutoDiscoverRTCClock.h>
#endif

#ifdef DISPLAY_CLASS
  #include <helpers/ui/NullDisplayDriver.h>
  extern DISPLAY_CLASS display;
#endif

extern XiaoNrf52Board board;
extern WRAPPER_CLASS radio_driver;
#ifdef DISABLE_I2C_RTC_SCAN
extern VolatileRTCClock rtc_clock;
#else
extern AutoDiscoverRTCClock rtc_clock;
#endif
extern EnvironmentSensorManager sensors;

bool radio_init();
mesh::LocalIdentity radio_new_identity();

