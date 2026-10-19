#pragma once

#define RADIOLIB_STATIC_ONLY 1
#include <RadioLib.h>
#include <helpers/radiolib/RadioLibWrappers.h>
#include <helpers/stm32/STM32Board.h>
#include <helpers/radiolib/CustomSTM32WLxWrapper.h>
#include <helpers/ArduinoHelpers.h>
#include <helpers/SensorManager.h>

#define  PIN_VBAT_READ    A0
#define  ADC_MULTIPLIER   (5 * 1.73 * 1000)

class RAK3x72Board : public STM32Board {
public:
    void begin() override {
        STM32Board::begin();
        pinMode(PA0, OUTPUT);
        pinMode(PA1, OUTPUT);
    }

    const char* getManufacturerName() const override {
        return "RAK 3x72";
    }

    uint16_t getBattMilliVolts() override {
        analogReadResolution(12);
        uint32_t raw = 0;
        for (int i=0; i<8;i++) {
            raw += analogRead(PIN_VBAT_READ);
        }
        // The eight 12-bit samples sum to at most 32760. This is the exact
        // previous 8650 / 32768 scale and truncation without double helpers;
        // the largest product (283374000) fits in uint32_t.
        return (uint16_t)((raw * 8650UL) >> 15);
    }

    void setGpio(uint32_t values) override {
        // set led values
        digitalWrite(PA0, values & 1);
        digitalWrite(PA1, (values & 2) >> 1);
    }

    uint32_t getGpio() override {
        // get led value
        return (digitalRead(PA1) << 1) | digitalRead(PA0);
    }
};

extern RAK3x72Board board;
extern WRAPPER_CLASS radio_driver;
extern VolatileRTCClock rtc_clock;
extern SensorManager sensors;

bool radio_init();
mesh::LocalIdentity radio_new_identity();
