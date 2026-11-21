#pragma once

#include <Arduino.h>
#include <MeshCore.h>
#include <helpers/NRF52Board.h>
#include <helpers/nrf52/ShutdownButton.h>

class MeshtinyBoard : public NRF52BoardDCDC {
protected:
  uint8_t btn_prev_state;

public:
  MeshtinyBoard() : NRF52Board("Meshtiny_OTA") {}
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
    analogReference(AR_INTERNAL_3_0);
    delay(10);
    adcvalue = analogRead(PIN_VBAT_READ);
    return calibrateBatteryMillivolts((adcvalue * ADC_MULTIPLIER * AREF_VOLTAGE) / 4.096);
  }

  const char *getManufacturerName() const override { return "Meshtiny"; }

  void reboot() override { NRF52Board::reboot(); }

  void shutdownPeripherals() override {
    NRF52Board::shutdownPeripherals();

#ifdef PIN_USER_BTN
    mesh::nrf52::armShutdownButton(PIN_USER_BTN, LOW, NRF_GPIO_PIN_PULLUP);
#endif

#ifdef PIN_3V3_EN
    pinMode(PIN_3V3_EN, OUTPUT);
    digitalWrite(PIN_3V3_EN, LOW);
#endif


#ifdef PIN_LED1
    digitalWrite(PIN_LED1, LOW);
#endif

#ifdef PIN_LED2
    digitalWrite(PIN_LED2, LOW);
#endif
  }
};
