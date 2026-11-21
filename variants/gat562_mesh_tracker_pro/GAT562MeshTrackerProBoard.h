#pragma once

#include <MeshCore.h>
#include <Arduino.h>
#include <helpers/NRF52Board.h>


class GAT562MeshTrackerProBoard : public NRF52BoardDCDC {
protected:
#ifdef NRF52_POWER_MANAGEMENT
  void initiateShutdown(uint8_t reason) override;
#endif

public:
  GAT562MeshTrackerProBoard() : NRF52Board("GAT562_OTA") {}
  void begin();

  #define BATTERY_SAMPLES 8

  bool supportsBatteryCalibration() const override { return true; }

  uint16_t getBattMilliVolts() override {
    analogReadResolution(12);

    const uint32_t raw = readBatteryAdcAverage(PIN_VBAT_READ, BATTERY_SAMPLES);

    return calibrateBatteryMillivolts((ADC_MULTIPLIER * raw) / 4096);
  }

  const char* getManufacturerName() const override {
    return "GAT562 Mesh Tracker Pro";
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

  void shutdownPeripherals() override {
    NRF52Board::shutdownPeripherals();

    uint32_t button_pin = PIN_BUTTON1;
    nrf_gpio_cfg_input(button_pin, NRF_GPIO_PIN_PULLUP);
    nrf_gpio_cfg_sense_set(button_pin, NRF_GPIO_PIN_SENSE_LOW);
  }
};
