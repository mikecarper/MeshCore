#pragma once

#include <MeshCore.h>
#include <Arduino.h>
#include <helpers/NRF52Board.h>

class KeepteenLT1Board : public NRF52Board {
protected:
  uint8_t btn_prev_state;

public:
  KeepteenLT1Board() : NRF52Board("KeepteenLT1_OTA") {}
  void begin();

  #define BATTERY_SAMPLES 8

  bool supportsBatteryCalibration() const override { return true; }

  uint16_t getBattMilliVolts() override {
    analogReadResolution(12);

    const uint32_t raw = readBatteryAdcAverage(PIN_VBAT_READ, BATTERY_SAMPLES);
    return calibrateBatteryMillivolts((ADC_MULTIPLIER * raw));
  }

  const char* getManufacturerName() const override {
    return "Keepteen LT1";
  }

#if defined(P_LORA_TX_LED)
  void applyLedPolicy() override {
    NRF52Board::applyLedPolicy();
    if (!mesh::leds::enabled()) digitalWrite(P_LORA_TX_LED, LOW);
  }

  void onBeforeTransmit() override {
    if (mesh::leds::enabled()) digitalWrite(P_LORA_TX_LED, HIGH);   // turn TX LED on
  }
  void onAfterTransmit() override {
    digitalWrite(P_LORA_TX_LED, LOW);   // turn TX LED off
  }
#endif
};
