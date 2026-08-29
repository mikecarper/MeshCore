#include "HeltecV4R8Board.h"

// Expansion Kit V2 control-pin notes (kept here beside the shared-reset
// sequencing): display/touch I2C is SDA 17 / SCL 18; GPIO 21 is the shared
// LCD_RESET / TP_RESET line; GPIO 44 drives the active-high backlight. The
// optional R13 touch interrupt lands on GPIO 43, which is also ESP32-S3 U0TXD,
// so production builds deliberately poll touch and do not define PIN_TOUCH_INT.

void HeltecV4R8Board::begin() {
  ESP32Board::begin();

  periph_power.begin();
  periph_power.claim();  // R8 VEXT also feeds the LoRa antenna boost rail.

  loRaFEMControl.init();

#ifdef PIN_TOUCH_RST
  pinMode(PIN_TOUCH_RST, OUTPUT);
  digitalWrite(PIN_TOUCH_RST, HIGH);
  delay(10);
  digitalWrite(PIN_TOUCH_RST, LOW);
  delay(100);
  digitalWrite(PIN_TOUCH_RST, HIGH);
#endif

  esp_reset_reason_t reason = esp_reset_reason();
  if (reason == ESP_RST_DEEPSLEEP) {
    long wakeup_source = esp_sleep_get_ext1_wakeup_status();
    if (wakeup_source & (1 << P_LORA_DIO_1)) {
      startup_reason = BD_STARTUP_RX_PACKET;
    }

    rtc_gpio_hold_dis((gpio_num_t)P_LORA_NSS);
    rtc_gpio_deinit((gpio_num_t)P_LORA_DIO_1);
  }
}

void HeltecV4R8Board::onBeforeTransmit(void) {
  digitalWrite(P_LORA_TX_LED, HIGH);
  loRaFEMControl.setTxModeEnable();
}

void HeltecV4R8Board::onAfterTransmit(void) {
  digitalWrite(P_LORA_TX_LED, LOW);
  loRaFEMControl.setRxModeEnable();
}

void HeltecV4R8Board::powerOff() {
  loRaFEMControl.setSleepModeEnable();
  digitalWrite(P_LORA_PA_POWER, LOW);
  rtc_gpio_hold_en((gpio_num_t)P_LORA_PA_POWER);
  periph_power.release();  // Drop the permanent antenna-boost/VEXT claim from begin().
  ESP32Board::powerOff();
}

uint16_t HeltecV4R8Board::getBattMilliVolts() {
  analogReadResolution(12);

  uint32_t raw = 0;
  for (int i = 0; i < 8; i++) {
    raw += analogReadMilliVolts(PIN_VBAT_READ);
  }
  raw = raw / 8;

  return (adc_mult * raw);
}

const char* HeltecV4R8Board::getManufacturerName() const {
#ifdef HELTEC_V4_R8_TFT
  return "Heltec V4 R8 TFT";
#else
  return "Heltec V4 R8 OLED";
#endif
}

bool HeltecV4R8Board::setLoRaFemLnaEnabled(bool enable) {
  if (!loRaFEMControl.isLnaCanControl()) return false;

  loRaFEMControl.setLNAEnable(enable);
  loRaFEMControl.setRxModeEnable();
  return true;
}

bool HeltecV4R8Board::canControlLoRaFemLna() const {
  return loRaFEMControl.isLnaCanControl();
}

bool HeltecV4R8Board::isLoRaFemLnaEnabled() const {
  return loRaFEMControl.isLNAEnabled();
}
