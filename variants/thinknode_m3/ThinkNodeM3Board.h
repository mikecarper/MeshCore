#pragma once

#include <Arduino.h>
#include <MeshCore.h>
#include <helpers/NRF52Board.h>

#define ADC_FACTOR ((1000.0*ADC_MULTIPLIER*AREF_VOLTAGE)/ADC_MAX)

class ThinkNodeM3Board : public NRF52BoardDCDC {
protected:
#if NRF52_POWER_MANAGEMENT
  void initiateShutdown(uint8_t reason) override;
#endif

public:
  ThinkNodeM3Board() : NRF52Board("THINKNODE_M3_OTA") {}
  void begin();
  bool supportsBatteryCalibration() const override { return true; }

  uint16_t getBattMilliVolts() override;

#ifdef P_LORA_TX_LED
  void applyLedPolicy() override {
    NRF52Board::applyLedPolicy();
    if (!mesh::leds::enabled()) digitalWrite(P_LORA_TX_LED, !LED_STATE_ON);
  }

  void onBeforeTransmit() override {
    if (mesh::leds::enabled()) digitalWrite(P_LORA_TX_LED, LED_STATE_ON);  // turn TX LED on
  }
  void onAfterTransmit() override {
    digitalWrite(P_LORA_TX_LED, !LED_STATE_ON); // turn TX LED off
  }
#endif

  const char* getManufacturerName() const override {
    return "Elecrow ThinkNode M3";
  }

  void shutdownPeripherals() override {
    NRF52Board::shutdownPeripherals();

    // turn off all leds, sd_power_system_off will not do this for us
    digitalWrite(PIN_LED_BLUE, !LED_STATE_ON);
    digitalWrite(PIN_LED_GREEN, !LED_STATE_ON);
    digitalWrite(PIN_LED_RED, !LED_STATE_ON);
  }
};
