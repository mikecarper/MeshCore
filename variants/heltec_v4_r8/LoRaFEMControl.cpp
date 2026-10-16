#include "LoRaFEMControl.h"
#include <helpers/ESP32GpioHold.h>

#include <Arduino.h>
#include <driver/rtc_io.h>
#include <esp_sleep.h>

void LoRaFEMControl::init(void) {
  pinMode(P_LORA_PA_POWER, OUTPUT);
  digitalWrite(P_LORA_PA_POWER, HIGH);
  mesh::esp32::releaseRtcGpioIfSupported((gpio_num_t)P_LORA_PA_POWER);

  esp_reset_reason_t reason = esp_reset_reason();
  if (reason != ESP_RST_DEEPSLEEP) {
    delay(1);
  }

  mesh::esp32::releaseRtcGpioIfSupported((gpio_num_t)P_LORA_KCT8103L_PA_CSD);
  mesh::esp32::releaseRtcGpioIfSupported((gpio_num_t)P_LORA_KCT8103L_PA_CTX);

  pinMode(P_LORA_KCT8103L_PA_CSD, OUTPUT);
  digitalWrite(P_LORA_KCT8103L_PA_CSD, HIGH);
  pinMode(P_LORA_KCT8103L_PA_CTX, OUTPUT);
  digitalWrite(P_LORA_KCT8103L_PA_CTX, lna_enabled ? LOW : HIGH);
  setLnaCanControl(true);
}

void LoRaFEMControl::setSleepModeEnable(void) {
  digitalWrite(P_LORA_KCT8103L_PA_CSD, LOW);
}

void LoRaFEMControl::setTxModeEnable(void) {
  digitalWrite(P_LORA_KCT8103L_PA_CSD, HIGH);
  digitalWrite(P_LORA_KCT8103L_PA_CTX, HIGH);
}

void LoRaFEMControl::setRxModeEnable(void) {
  digitalWrite(P_LORA_KCT8103L_PA_CSD, HIGH);
  digitalWrite(P_LORA_KCT8103L_PA_CTX, lna_enabled ? LOW : HIGH);
}

void LoRaFEMControl::setRxModeEnableWhenMCUSleep(void) {
  digitalWrite(P_LORA_PA_POWER, HIGH);
  mesh::esp32::holdRtcGpioIfSupported((gpio_num_t)P_LORA_PA_POWER);

  digitalWrite(P_LORA_KCT8103L_PA_CSD, HIGH);
  mesh::esp32::holdRtcGpioIfSupported((gpio_num_t)P_LORA_KCT8103L_PA_CSD);
  digitalWrite(P_LORA_KCT8103L_PA_CTX, lna_enabled ? LOW : HIGH);
  mesh::esp32::holdRtcGpioIfSupported((gpio_num_t)P_LORA_KCT8103L_PA_CTX);
}

void LoRaFEMControl::setLNAEnable(bool enabled) {
  lna_enabled = enabled;
}
