#include "MeshnologyW12Board.h"

void MeshnologyW12Board::begin() {
    ESP32Board::begin();

    rtc_gpio_hold_dis((gpio_num_t)P_LORA_LF_PA_POWER);
    rtc_gpio_hold_dis((gpio_num_t)P_LORA_HF_PA_POWER);
    pinMode(PIN_ADC_CTRL, OUTPUT);
    digitalWrite(PIN_ADC_CTRL, LOW); // Disable battery sense until required

    pinMode(P_LORA_LF_PA_POWER, OUTPUT);
    pinMode(P_LORA_HF_PA_POWER, OUTPUT);
    digitalWrite(P_LORA_LF_PA_POWER, LOW);
    digitalWrite(P_LORA_HF_PA_POWER, LOW);
    prepareRadioFrequency(LORA_FREQ);

    periph_power.begin();
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

  void MeshnologyW12Board::onBeforeTransmit(void) {
    neopixelWrite(NEOPIXEL_LED, NEOPIXEL_BRIGHTNESS, NEOPIXEL_BRIGHTNESS, NEOPIXEL_BRIGHTNESS);   // turn TX neopixel on (White)
  }

  void MeshnologyW12Board::onAfterTransmit(void) {
    neopixelWrite(NEOPIXEL_LED, 0, 0, 0);   // turn TX neopixel off
  }

  bool MeshnologyW12Board::prepareRadioFrequency(float frequency) {
    const bool high_band = mesh::lr2021::isHighBand(frequency);
    if (radio_band_initialized && high_band == radio_high_band) return true;

    // The W12 schematic connects PA_EN_M (GPIO4) to the GC1109 sub-GHz rail
    // and PA_EN_G (GPIO3) to the RFX2402E 2.4 GHz rail. Keep only the selected
    // FEM powered, and disable the old path before enabling the new one.
    digitalWrite(P_LORA_LF_PA_POWER, LOW);
    digitalWrite(P_LORA_HF_PA_POWER, LOW);
    digitalWrite(high_band ? P_LORA_HF_PA_POWER : P_LORA_LF_PA_POWER, HIGH);
    delay(100);

    radio_high_band = high_band;
    radio_band_initialized = true;
    return true;
  }

  void MeshnologyW12Board::enterDeepSleep(uint32_t secs, int pin_wake_btn) {
    esp_sleep_pd_config(ESP_PD_DOMAIN_RTC_PERIPH, ESP_PD_OPTION_ON);

    // Make sure the DIO1 and NSS GPIOs are hold on required levels during deep sleep
    rtc_gpio_set_direction((gpio_num_t)P_LORA_DIO_1, RTC_GPIO_MODE_INPUT_ONLY);
    rtc_gpio_pulldown_en((gpio_num_t)P_LORA_DIO_1);

    rtc_gpio_hold_en((gpio_num_t)P_LORA_NSS);

    if (pin_wake_btn < 0) {
      esp_sleep_enable_ext1_wakeup( (1L << P_LORA_DIO_1), ESP_EXT1_WAKEUP_ANY_HIGH);  // wake up on: recv LoRa packet
    } else {
      esp_sleep_enable_ext1_wakeup( (1L << P_LORA_DIO_1) | (1L << pin_wake_btn), ESP_EXT1_WAKEUP_ANY_HIGH);  // wake up on: recv LoRa packet OR wake btn
    }

    if (secs > 0) {
      esp_sleep_enable_timer_wakeup(secs * 1000000);
    }

    // Finally set ESP32 into sleep
    esp_deep_sleep_start();   // CPU halts here and never returns!
  }

  void MeshnologyW12Board::powerOff()  {
    // A shutdown must not retain the packet-wake deep-sleep behavior above.
    // Disable both external FEM rails, then use the base shutdown path so the
    // display, GPS, radio, serial buffers, and stale wake sources are handled.
    digitalWrite(P_LORA_LF_PA_POWER, LOW);
    digitalWrite(P_LORA_HF_PA_POWER, LOW);
    rtc_gpio_hold_en((gpio_num_t)P_LORA_LF_PA_POWER);
    rtc_gpio_hold_en((gpio_num_t)P_LORA_HF_PA_POWER);
    ESP32Board::powerOff();
  }

  uint16_t MeshnologyW12Board::getBattMilliVolts()  {
    analogReadResolution(12);
    analogSetAttenuation(ADC_11db);
    digitalWrite(PIN_ADC_CTRL, HIGH);
    delay(10);
    uint32_t raw = 0;
    for (int i = 0; i < 8; i++) {
      raw += analogRead(PIN_VBAT_READ);
    }
    raw = raw / 8;

    digitalWrite(PIN_ADC_CTRL, LOW);

    return (adc_mult * (3.3 / 4096.0) * raw) * 1000;
  }

  const char* MeshnologyW12Board::getManufacturerName() const {
    return "Meshnology W12";
  }
