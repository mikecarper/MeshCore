#pragma once

#include <MeshCore.h>
#include <Arduino.h>
#include <helpers/NRF52Board.h>

class WioTrackerL1Board : public NRF52BoardDCDC {
protected:
  uint8_t btn_prev_state;

public:
  WioTrackerL1Board() : NRF52Board("WioTrackerL1_OTA") {}
  void begin();

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

  bool supportsBatteryCalibration() const override { return true; }

  uint16_t getBattMilliVolts() override {
    int adcvalue = 0;
    analogReadResolution(12);
    analogReference(AR_INTERNAL);
    delay(10);
    adcvalue = analogRead(PIN_VBAT_READ);
    return calibrateBatteryMillivolts((adcvalue * ADC_MULTIPLIER * AREF_VOLTAGE) / 4.096);
  }

  const char* getManufacturerName() const override {
    // Keep the physical display/power variants distinguishable to read-only
    // USB clients. Older releases used the same name for Pro, E-Ink and 1W.
#if defined(WIO_TRACKER_L1_EINK)
    return "Seeed Wio Tracker L1 E-Ink";
#else
    return "Seeed Wio Tracker L1 Pro";
#endif
  }
};
