#include <Arduino.h>
#include <Wire.h>

#include "MuziBaseBoard.h"

#if defined(MESH_RADIO_AUTO_FAMILY) && MESH_RADIO_AUTO_FAMILY && MESH_RADIO_AUTO_LR1121
#include <helpers/LoRaRadioFamily.h>
extern bool prepareUniversalLoRaRadio();
extern mesh::LoRaRadioFamily universalLoRaRadioFamily();

const char* MuziBaseBoard::detectedManufacturerName() const {
  switch (universalLoRaRadioFamily()) {
    case mesh::LoRaRadioFamily::SX126x: return "Muzi Base Uno";
    case mesh::LoRaRadioFamily::LR1121: return "Muzi Base Duo";
    default: return "Muzi Base (radio not detected)";
  }
}

void MuziBaseBoard::shutdownPeripherals() {
  NRF52BoardDCDC::shutdownPeripherals();
  // The base knows only the recipe's default IRQ. Both candidate pins are
  // reserved for this board's radio; neither may retain GPIO sensing while
  // entering SYSTEMOFF, including the Duo's runtime-selected IRQ on P1.08.
  detachInterrupt(LORA_SX1262_DIO_1);
  pinMode(LORA_SX1262_DIO_1, INPUT);
  detachInterrupt(LORA_LR1121_DIO_1);
  pinMode(LORA_LR1121_DIO_1, INPUT);
}
#endif

#ifdef NRF52_POWER_MANAGEMENT
const PowerMgtConfig power_config = {
  .lpcomp_ain_channel = PWRMGT_LPCOMP_AIN,
  .lpcomp_refsel = PWRMGT_LPCOMP_REFSEL,
  .voltage_bootlock = PWRMGT_VOLTAGE_BOOTLOCK
};

void MuziBaseBoard::initiateShutdown(uint8_t reason) {
  // Disable LoRa module power before shutdown
  if (reason == SHUTDOWN_REASON_LOW_VOLTAGE ||
      reason == SHUTDOWN_REASON_BOOT_PROTECT) {
    configureVoltageWake(power_config.lpcomp_ain_channel, power_config.lpcomp_refsel);
  }

  enterSystemOff(reason);
}
#endif // NRF52_POWER_MANAGEMENT

void MuziBaseBoard::begin() {
  NRF52BoardDCDC::begin();
  pinMode(PIN_VBAT_READ, INPUT);
  pinMode(PIN_BATTERY_CHARGING, INPUT);
  pinMode(PIN_CHARGER_FAULT, INPUT);
  pinMode(LED_PIN, OUTPUT);
  digitalWrite(LED_PIN, LOW);
  // output latches default to HIGH, so pull these low right after enabling
  pinMode(BUZZER_PIN, OUTPUT);
  digitalWrite(BUZZER_PIN, LOW);
  // gps power is driven by the sensor manager (mode switch). off to start.
  pinMode(PIN_GPS_EN, OUTPUT);
  digitalWrite(PIN_GPS_EN, LOW);
  // 12V rail is only needed for the superIO display
  pinMode(SCREEN_12V_ENABLE, OUTPUT);
#if defined(MUZI_BASE_ACCESSORY_AUTODETECT) && MUZI_BASE_ACCESSORY_AUTODETECT
  // A bare Base must not run the optional OLED's boost converter. The
  // display driver owns the bounded probe and powers it down on absence.
  digitalWrite(SCREEN_12V_ENABLE, LOW);
#elif defined(MUZI_BASE_SUPERIO)
  digitalWrite(SCREEN_12V_ENABLE, HIGH); // Enable 12V power for SH1107 display
  delay(250);
#else
  digitalWrite(SCREEN_12V_ENABLE, LOW);
#endif
  Wire.begin();
  // delay(1000); // wait for display to initialize. otherwise it doesn't come up on boot.

#ifdef PIN_USER_BTN
  pinMode(PIN_USER_BTN, INPUT_PULLUP);
#endif
  pinMode(PIN_BUTTON1, INPUT_PULLUP);
  pinMode(PIN_BUTTON2, INPUT_PULLUP);
  pinMode(PIN_BUTTON3, INPUT_PULLUP);
  pinMode(PIN_BUTTON4, INPUT_PULLUP);
  pinMode(PIN_BUTTON5, INPUT_PULLUP);
  pinMode(PIN_BUTTON6, INPUT_PULLUP);

// #if defined(PIN_BOARD_SDA) && defined(PIN_BOARD_SCL)
//   Wire.setPins(PIN_BOARD_SDA, PIN_BOARD_SCL);
// #endif
#ifdef NRF52_POWER_MANAGEMENT
  checkBootVoltage(&power_config);
#endif
#if defined(MESH_RADIO_AUTO_FAMILY) && MESH_RADIO_AUTO_FAMILY && MESH_RADIO_AUTO_LR1121
  // Identify only after power/voltage protection, before startup loads the
  // selected radio's regulator and switch-tuning preferences.
  prepareUniversalLoRaRadio();
#endif
}
