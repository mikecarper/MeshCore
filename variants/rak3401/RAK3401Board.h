#pragma once

#include <MeshCore.h>
#include <Arduino.h>
#include <helpers/NRF52Board.h>

// built-ins
#define  PIN_VBAT_READ    5
#define  ADC_MULTIPLIER   (3 * 1.73 * 1.187 * 1000)

#define PIN_3V3_EN (34)
#define WB_IO2 PIN_3V3_EN

class RAK3401Board : public NRF52BoardDCDC {
#if MESH_ENABLE_EMERGENCY_POWER
  bool emergency_fem_suspended = false;
  bool emergency_fem_was_enabled = false;
#endif
protected:
#ifdef NRF52_POWER_MANAGEMENT
  void initiateShutdown(uint8_t reason) override;
#endif
public:
  RAK3401Board() : NRF52Board("RAK3401_OTA") {}
  void begin();
  void shutdownPeripherals() override;
  bool recoverRadio();
  void enableRadioFrontend();
#if MESH_ENABLE_EMERGENCY_POWER
  void setEmergencyRadioSleeping(bool sleeping) override;
#endif
  bool supportsRadioHardReset() const override { return true; }
  bool resetRadio() override { return recoverRadio(); }
  bool finishRadioHardReset() override {
    enableRadioFrontend();
    return true;
  }

  #define BATTERY_SAMPLES 8

  bool supportsBatteryCalibration() const override { return true; }

  uint16_t getBattMilliVolts() override {
    analogReadResolution(12);

    uint32_t raw = 0;
    for (int i = 0; i < BATTERY_SAMPLES; i++) {
      raw += analogRead(PIN_VBAT_READ);
    }
    raw = raw / BATTERY_SAMPLES;

    return calibrateBatteryMillivolts((ADC_MULTIPLIER * raw) / 4096);
  }

  const char* getManufacturerName() const override {
    return "RAK 3401";
  }

  // TX/RX switching is handled by SX1262 DIO2 -> SKY66122 CTX (hardware-timed).
  // No onBeforeTransmit/onAfterTransmit overrides needed.
};
