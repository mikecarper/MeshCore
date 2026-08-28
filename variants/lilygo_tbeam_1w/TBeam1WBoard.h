#pragma once

#include <Arduino.h>
#include <helpers/ESP32Board.h>
#include "variant.h"

class CustomSX1262Wrapper;

// LilyGo T-Beam 1W with SX1262 + external PA (XY16P35 module)
//
// Power architecture (LDO is separate chip on T-Beam board, not inside XY16P35):
//
//   VCC (+4.0~+8.0V) --+------------------> XY16P35 VCC pin 5 (PA direct)
//   (USB or Battery)   |
//                      |   +-----------+
//                      +-->| LDO Chip  |--> +3.3V --> XY16P35 (SX1262 + LNA)
//                          | EN=GPIO40 |
//                          +-----------+
//                      LDO_EN (GPIO 40): H @ +1.2V~VIN, active high, not floating
//
// Control signals:
//   - LDO_EN (GPIO 40): HIGH enables LDO -> powers SX1262 + LNA
//   - TCXO_EN (DIO3):   3.0 V (XY16P35 specifies 2.85-3.15 V)
//   - CTL (GPIO 21):    HIGH=RX (LNA on), LOW=TX (LNA off)
//   - DIO2:             AUTO via SX126X_DIO2_AS_RF_SWITCH (TX path)
//
// Power notes:
//   - PA needs VCC 4.0-8.0V for full 32dBm output
//   - USB-C (3.9-6V) marginal; 7.4V battery recommended
//   - Battery must support 2A+ discharge for high-power TX

class TBeam1WBoard : public ESP32Board {
private:
  bool radio_powered = false;
  bool lna_enabled = true;
  bool lna_driver_synced = false;
  CustomSX1262Wrapper* radio_driver = nullptr;
  bool fan_running = false;
  bool fan_temperature_valid = false;
  uint32_t fan_started_at_ms = 0;
  uint32_t fan_last_poll_ms = 0;
  float fan_last_temperature_c = 0.0f;

public:
  void begin();
  void onBeforeTransmit() override;
  void onAfterTransmit() override;
  uint16_t getBattMilliVolts() override;
  const char* getManufacturerName() const override;
  void powerOff() override;
  void powerCycleRadio();
  bool supportsRadioHardReset() const override { return true; }
  bool resetRadio() override {
    powerCycleRadio();
    return true;
  }

  void attachRadioDriver(CustomSX1262Wrapper* driver);
  bool setLoRaFemLnaEnabled(bool enable) override;
  bool canControlLoRaFemLna() const override;
  bool isLoRaFemLnaEnabled() const override;

  // Temperature-gated cooling for the external PA. Every T-Beam 1W role
  // services this from its main loop; the TX hooks also run it at the points
  // where the PA can begin heating.
  void updateFanControl();
  void setFanEnabled(bool enabled);
  bool isFanEnabled() const;
};
