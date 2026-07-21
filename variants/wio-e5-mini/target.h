#pragma once

#define RADIOLIB_STATIC_ONLY 1
#include <RadioLib.h>
#include <helpers/radiolib/RadioLibWrappers.h>
#include <helpers/stm32/STM32Board.h>
#include <helpers/radiolib/CustomSTM32WLxWrapper.h>
#include <helpers/ArduinoHelpers.h>
#include <helpers/SensorManager.h>
#ifdef DISPLAY_CLASS
  #include "NullDisplayDriver.h"
#endif

#ifndef WIO_E5_MINI_NO_EXTERNAL_SENSORS
  #include <BME280I2C.h>
  #include <Wire.h>
#endif

#ifdef DISPLAY_CLASS
  extern NullDisplayDriver display;
#endif

class WIOE5Board : public STM32Board {
public:
    void begin() override {
        STM32Board::begin();

        pinMode(LED_RED, OUTPUT);
        digitalWrite(LED_RED, HIGH);
        pinMode(USER_BTN, INPUT_PULLUP);
    }

    const char* getManufacturerName() const override {
        return "Seeed Wio E5 mini";
    }

    uint16_t getBattMilliVolts() override {
        analogReadResolution(12);
        uint32_t raw = 0;
        for (int i=0; i<8;i++) {
            raw += analogRead(PIN_A3);
        }
        // 1.73 * 5 V over eight 12-bit samples is raw * 8650 / 32768.
        // Fixed-point math avoids pulling double-precision helpers into this
        // flash-constrained STM32WL build; the half-divisor rounds to nearest.
        return (uint16_t)((raw * 8650UL + 16384UL) >> 15);
    }
};

#ifndef WIO_E5_MINI_NO_EXTERNAL_SENSORS
  class WIOE5SensorManager : public SensorManager {
      BME280I2C bme;
      bool has_bme = false;

  public:
      WIOE5SensorManager() {}
      bool begin() override;
      bool querySensors(uint8_t requester_permissions, CayenneLPP& telemetry) override;
  };
#endif

extern WIOE5Board board;
extern WRAPPER_CLASS radio_driver;
extern VolatileRTCClock rtc_clock;
#ifdef WIO_E5_MINI_NO_EXTERNAL_SENSORS
  extern SensorManager sensors;
#else
  extern WIOE5SensorManager sensors;
#endif

bool radio_init();
mesh::LocalIdentity radio_new_identity();
