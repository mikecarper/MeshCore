#pragma once

#include <Arduino.h>
#include <MeshCore.h>
#include <helpers/NRF52Board.h>

#ifdef IKOKA_NRF52

class IkokaNrf52Board : public NRF52BoardDCDC {
public:
  IkokaNrf52Board() : NRF52Board("XIAO_NRF52_OTA") {}
  void begin();

#if defined(P_LORA_TX_LED)
  void applyLedPolicy() override {
    NRF52Board::applyLedPolicy();
    if (!mesh::leds::enabled()) digitalWrite(P_LORA_TX_LED, HIGH);
  }

  void onBeforeTransmit() override {
    if (mesh::leds::enabled()) digitalWrite(P_LORA_TX_LED, LOW);   // turn TX LED on
  }
  void onAfterTransmit() override {
    digitalWrite(P_LORA_TX_LED, HIGH);   // turn TX LED off
  }
#endif

  bool supportsBatteryCalibration() const override { return true; }

  uint16_t getBattMilliVolts() override {
    // Please read before going further ;)
    // https://wiki.seeedstudio.com/XIAO_BLE#q3-what-are-the-considerations-when-using-xiao-nrf52840-sense-for-battery-charging

    // We can't drive VBAT_ENABLE to HIGH as long
    // as we don't know whether we are charging or not ...
    // About 3 uA through the 1 MOhm + 510 kOhm divider, not 3 mA.
    digitalWrite(VBAT_ENABLE, LOW);
    int adcvalue = 0;
    analogReadResolution(12);
    analogReference(AR_INTERNAL_3_0);
    delay(10);
    adcvalue = analogRead(PIN_VBAT);
    return calibrateBatteryMillivolts((adcvalue * ADC_MULTIPLIER * AREF_VOLTAGE) / 4.096);
  }

  const char* getManufacturerName() const override {
#ifdef MANUFACTURER_STRING
    return MANUFACTURER_STRING;
#else
    return "Ikoka Handheld E22 30dBm (Xiao_nrf52)";
#endif
  }
};

#endif
