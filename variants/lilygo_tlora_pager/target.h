#pragma once

// Shared I2C, SPI, chip selects, radio controls, display and user input.
#define USER_GPIO_RESERVED_PINS 2, 3, 7, 14, 21, 33, 34, 35, 36, 37, 38, 39, 42, 47, 48

#define RADIOLIB_STATIC_ONLY 1
#include <RadioLib.h>
#include <helpers/radiolib/RadioLibWrappers.h>
#ifdef USE_SX1262
#include <helpers/radiolib/CustomSX1262Wrapper.h>
#else
#include <helpers/radiolib/CustomLR1121Wrapper.h>
#endif
#include <TLoRaPagerBoard.h>
#include <helpers/SensorManager.h>
#ifdef DISPLAY_CLASS
  #include <helpers/ui/ST7789LCDDisplay.h>
  #include <helpers/ui/MomentaryButton.h>
#endif
#include "helpers/sensors/EnvironmentSensorManager.h"
#include "helpers/sensors/MicroNMEALocationProvider.h"

extern TLoRaPagerBoard board;
extern WRAPPER_CLASS radio_driver;
extern ESP32RTCClock rtc_clock;
extern EnvironmentSensorManager sensors;

#ifdef DISPLAY_CLASS
  extern DISPLAY_CLASS display;
  extern MomentaryButton user_btn;
#endif

bool radio_init();
mesh::LocalIdentity radio_new_identity();
