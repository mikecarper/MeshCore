#pragma once

#include <MeshCore.h>
#include <Arduino.h>
#include <helpers/NRF52Board.h>

// The Muzi Base PCB ships in two radio flavors. Keep the user-facing identity
// (OTA/DFU name and reported manufacturer) distinct per radio even though the
// board logic is shared.
#if defined(MESH_RADIO_AUTO_FAMILY) && MESH_RADIO_AUTO_FAMILY && MESH_RADIO_AUTO_LR1121
  #define MUZI_BASE_OTA_NAME  "MUZI_BASE_OTA"
  #define MUZI_BASE_MFR_NAME  "Muzi Base"
#elif defined(USE_SX1262)
  #define MUZI_BASE_OTA_NAME  "MUZI_BASE_UNO_OTA"
  #define MUZI_BASE_MFR_NAME  "Muzi Base Uno"
#else
  #define MUZI_BASE_OTA_NAME  "MUZI_BASE_DUO_OTA"
  #define MUZI_BASE_MFR_NAME  "Muzi Base Duo"
#endif

class MuziBaseBoard : public NRF52BoardDCDC {
protected:
#ifdef NRF52_POWER_MANAGEMENT
  void initiateShutdown(uint8_t reason) override;
#endif

public:
  MuziBaseBoard() : NRF52Board(MUZI_BASE_OTA_NAME) {}
  void begin();
#if defined(MESH_RADIO_AUTO_FAMILY) && MESH_RADIO_AUTO_FAMILY && MESH_RADIO_AUTO_LR1121
  void shutdownPeripherals() override;
#endif

  #define BATTERY_SAMPLES 8

  bool supportsBatteryCalibration() const override { return true; }

  uint16_t getBattMilliVolts() override {
    analogReadResolution(12);
    analogReference(AR_INTERNAL_3_0);
    delay(1);

    uint32_t raw = 0;
    for (int i = 0; i < BATTERY_SAMPLES; i++) {
      raw += analogRead(PIN_VBAT_READ);
    }
    raw = raw / BATTERY_SAMPLES;

    // ADC_MULTIPLIER is the voltage divider ratio
    return calibrateBatteryMillivolts((raw * ADC_MULTIPLIER * AREF_VOLTAGE) / 4.096);
  }

  const char* getManufacturerName() const override {
#if defined(MESH_RADIO_AUTO_FAMILY) && MESH_RADIO_AUTO_FAMILY && MESH_RADIO_AUTO_LR1121
    return detectedManufacturerName();
#else
    return MUZI_BASE_MFR_NAME;
#endif
  }

#if defined(MESH_RADIO_AUTO_FAMILY) && MESH_RADIO_AUTO_FAMILY && MESH_RADIO_AUTO_LR1121
private:
  const char* detectedManufacturerName() const;
#endif
};
