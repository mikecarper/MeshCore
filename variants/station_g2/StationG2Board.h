#pragma once

#include <Arduino.h>
#include <helpers/ESP32Board.h>

class StationG2Board : public ESP32Board {
public:
  void begin() {
    ESP32Board::begin();

    esp_reset_reason_t reason = esp_reset_reason();
    if (reason == ESP_RST_DEEPSLEEP) {
      long wakeup_source = esp_sleep_get_ext1_wakeup_status();
      if (wakeup_source & (1 << P_LORA_DIO_1)) {  // received a LoRa packet (while in deep sleep)
        startup_reason = BD_STARTUP_RX_PACKET;
      }

      rtc_gpio_hold_dis((gpio_num_t)P_LORA_NSS);
      rtc_gpio_deinit((gpio_num_t)P_LORA_DIO_1);
    }
  }

  uint16_t getBattMilliVolts() override {
    return 0;
  }

  bool isUserGpioAvailable(uint8_t pin) const override {
    // Only permit GPIOs exposed on the Station G2 IO extension socket.
    switch (pin) {
      case 8:
      case 9:
      case 16:
      case 17:
      case 18:
      case 39:
        return ESP32Board::isUserGpioAvailable(pin);
      default:
        return false;
    }
  }

  const char* getManufacturerName() const override {
    return "Station G2";
  }
};
