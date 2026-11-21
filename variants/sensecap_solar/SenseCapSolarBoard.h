#pragma once

#include <MeshCore.h>
#include <Arduino.h>
#include <helpers/NRF52Board.h>
#include <helpers/nrf52/ShutdownButton.h>

class SenseCapSolarBoard : public NRF52BoardDCDC {
protected:
#ifdef NRF52_POWER_MANAGEMENT
  void initiateShutdown(uint8_t reason) override;
#endif

public:
  SenseCapSolarBoard() : NRF52Board("SENSECAP_SOLAR_OTA") {}
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
    digitalWrite(VBAT_ENABLE, LOW);
    int adcvalue = 0;
    analogReadResolution(12);
    analogReference(AR_INTERNAL_3_0);
    delay(10);
    adcvalue = analogRead(BATTERY_PIN);
    return calibrateBatteryMillivolts((adcvalue * ADC_MULTIPLIER * AREF_VOLTAGE) / 4.096);
  }

  const char* getManufacturerName() const override {
    return "Seeed SenseCap Solar";
  }

  void shutdownPeripherals() override {
    NRF52Board::shutdownPeripherals();

    digitalWrite(LED_WHITE, LOW);
    digitalWrite(LED_BLUE, LOW);

#ifdef PIN_USER_BTN
    // Keep pull-up enabled in system-off so the wake line doesn't float low.
    mesh::nrf52::armShutdownButton(PIN_USER_BTN, LOW, NRF_GPIO_PIN_PULLUP);
#elif defined(PIN_BUTTON1)
    // Keep pull-up enabled in system-off so the wake line doesn't float low.
    mesh::nrf52::armShutdownButton(PIN_BUTTON1, LOW, NRF_GPIO_PIN_PULLUP);
#endif
  }

  void powerOff() override {
#ifdef NRF52_POWER_MANAGEMENT
    shutdownPeripherals();
    initiateShutdown(SHUTDOWN_REASON_USER);
#else
    NRF52Board::powerOff();
#endif
  }
};
