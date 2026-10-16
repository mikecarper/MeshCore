#pragma once
#include <helpers/ESP32GpioHold.h>

#include <Arduino.h>
#include <helpers/ESP32Board.h>
#include <driver/rtc_io.h>
#include "LoRaFEMControl.h"
#include "StationG3RadioSlot.h"

class StationG3Board : public ESP32Board {
  KeyValueStore* _prefs = NULL;

public:
  LoRaFEMControl loRaFEMControl;

  void begin() {
    station_g3::loadSlotSettings();
    const auto& slotSettings = station_g3::slotState();
    station_g3::slotState().candidate = slotSettings.bootMode == 2 ||
        (slotSettings.bootMode == 0 && slotSettings.last == 2) ? 2 : 1;
    ESP32Board::begin();
    // Release holds from EITHER previously selected socket, before probing.
    for (uint8_t slot = 1; slot <= 2; ++slot) {
      const auto& pins = station_g3::pinsForSlot(slot);
      mesh::esp32::releaseOutputAfterDeepSleep((gpio_num_t)pins.nss, HIGH);
      mesh::esp32::releaseOutputAfterDeepSleep((gpio_num_t)pins.lna, HIGH);
    }
    loRaFEMControl.init();

    esp_reset_reason_t reason = esp_reset_reason();
    if (reason == ESP_RST_DEEPSLEEP) {
      uint64_t wakeup_source = esp_sleep_get_ext1_wakeup_status();
      // A newly saved override may differ from the socket which woke us.
      const uint64_t radio_wake_mask = (1ULL << station_g3::pinsForSlot(1).irq) |
          (1ULL << station_g3::pinsForSlot(2).irq);
      if (wakeup_source & radio_wake_mask) {
        startup_reason = BD_STARTUP_RX_PACKET;
      }

      for (uint8_t slot = 1; slot <= 2; ++slot)
        mesh::esp32::deinitRtcGpioIfSupported(
            (gpio_num_t)station_g3::pinsForSlot(slot).irq);
    }
  }

  uint32_t getIRQGpio() override { return station_g3::activePins().irq; }

  void attachDynamicPrefs(KeyValueStore* prefs);

  bool handleCommand(const char* command, uint32_t sender_timestamp, char* reply) override;

  void setPrimaryLNAEnable(bool enabled) {
    loRaFEMControl.setLNAEnable(enabled);
  }

  void setPrimaryPAHighPower(bool enabled) {
    loRaFEMControl.setPAGainEnable(enabled);
  }

  void onBeforeTransmit() override {
    ESP32Board::onBeforeTransmit();
    loRaFEMControl.setTxModeEnable();
  }

  void onAfterTransmit() override {
    ESP32Board::onAfterTransmit();
    loRaFEMControl.setRxModeEnable();
  }

  bool setLoRaFemLnaEnabled(bool enable) override;
  bool canControlLoRaFemLna() const override;
  bool isLoRaFemLnaEnabled() const override;
  bool setLoRaFemPaGainEnabled(bool enable) override;
  bool canControlLoRaFemPaGain() const override;
  bool isLoRaFemPaGainEnabled() const override;

  void shutdownPeripherals() override;

  uint16_t getBattMilliVolts() override {
    return 0;
  }

  const char* getManufacturerName() const override {
    return "Station G3 ESP32";
  }
};
