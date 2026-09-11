#pragma once

#include <Arduino.h>
#include <helpers/ESP32Board.h>

// The common shape of an ExpressLRS ESP32 transmitter module: powered from the
// JR bay or USB rather than a battery, a fan over the amplifier, and an
// ESP8285 "backpack" hanging off a second UART.
//
// A variant supplies whichever of these its ExpressLRS hardware.json lists:
//
//   MANUFACTURER_NAME   name reported to clients
//   PIN_FAN_EN          hardware.json "misc_fan_en"     (HIGH = on)
//   PIN_BACKPACK_EN     hardware.json "backpack_en"     (HIGH = enabled)
//   PIN_BACKPACK_BOOT   hardware.json "backpack_boot"
//
// There is no battery divider on these boards, and ESP32Board already returns
// 0 from getBattMilliVolts() when PIN_VBAT_READ is undefined, so nothing here
// needs to override it.

#ifndef MANUFACTURER_NAME
  #define MANUFACTURER_NAME "ExpressLRS TX"
#endif

class ELRSTxBoard : public ESP32Board {
public:
  void begin() {
    ESP32Board::begin();

  #ifdef PIN_BACKPACK_EN
    // MeshCore has no use for the backpack. Hold it off so it cannot chatter
    // on the shared UART.
    pinMode(PIN_BACKPACK_EN, OUTPUT);
    digitalWrite(PIN_BACKPACK_EN, LOW);
  #endif
  #ifdef PIN_BACKPACK_BOOT
    pinMode(PIN_BACKPACK_BOOT, INPUT);   // usually a strapping pin, leave it
  #endif

  #ifdef PIN_FAN_EN
    // ExpressLRS only spins the fan above 250 mW, but MeshCore transmits for
    // far longer than an ExpressLRS packet, so just leave it running.
    pinMode(PIN_FAN_EN, OUTPUT);
    digitalWrite(PIN_FAN_EN, HIGH);
  #endif
  }

  const char* getManufacturerName() const override {
    return MANUFACTURER_NAME;
  }

  uint32_t getIRQGpio() override {
    return P_LORA_DIO_0;   // SX127x signals RxDone/TxDone on DIO0
  }
};
