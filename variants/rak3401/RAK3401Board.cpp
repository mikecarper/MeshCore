#include <Arduino.h>
#include <Wire.h>

#include "RAK3401Board.h"

#ifdef NRF52_POWER_MANAGEMENT
// Static configuration for power management
// Values set in variant.h defines
const PowerMgtConfig power_config = {
  .lpcomp_ain_channel = PWRMGT_LPCOMP_AIN,
  .lpcomp_refsel = PWRMGT_LPCOMP_REFSEL,
  .voltage_bootlock = PWRMGT_VOLTAGE_BOOTLOCK
};

void RAK3401Board::initiateShutdown(uint8_t reason) {
  // Disable SKY66122 FEM (CSD+CPS LOW = shutdown, <1 uA)
  digitalWrite(SX126X_POWER_EN, LOW);

  // Disable 3V3 switched peripherals and 5V boost
  digitalWrite(PIN_3V3_EN, LOW);

  if (reason == SHUTDOWN_REASON_LOW_VOLTAGE ||
      reason == SHUTDOWN_REASON_BOOT_PROTECT) {
    configureVoltageWake(power_config.lpcomp_ain_channel, power_config.lpcomp_refsel);
  }

  enterSystemOff(reason);
}
#endif

void RAK3401Board::begin() {
  NRF52BoardDCDC::begin();
  pinMode(PIN_VBAT_READ, INPUT);
#ifdef PIN_USER_BTN
  pinMode(PIN_USER_BTN, INPUT_PULLUP);
#endif

#ifdef PIN_USER_BTN_ANA
  pinMode(PIN_USER_BTN_ANA, INPUT_PULLUP);
#endif

#if defined(PIN_BOARD_SDA) && defined(PIN_BOARD_SCL)
  Wire.setPins(PIN_BOARD_SDA, PIN_BOARD_SCL);
#endif

  Wire.begin();

#ifdef NRF52_POWER_MANAGEMENT
  // Boot voltage protection check (may not return if voltage too low)
  checkBootVoltage(&power_config);
#endif

  // WB_IO2 controls the shared 3V3_S peripheral rail and the RAK13302 boost
  // converter. Keep it enabled during normal operation. Cycling this rail as
  // part of radio recovery also resets fitted GPS and sensor modules and does
  // not provide a reliable reset of the SX1262 core.
  pinMode(PIN_3V3_EN, OUTPUT);
  digitalWrite(PIN_3V3_EN, HIGH);

  // CSD and CPS are tied together on the RAK13302 and routed to IO3. Keep the
  // FEM quiescent until the radio probe succeeds; enableRadioFrontend() then
  // restores it for normal RX/TX operation.
  pinMode(SX126X_POWER_EN, OUTPUT);
  digitalWrite(SX126X_POWER_EN, LOW);
}

bool RAK3401Board::recoverRadio() {
  // Quiesce the FEM, leave the shared peripheral rail and SPI controller
  // alone, and pulse the SX1262's dedicated NRST pin. BUSY must fall after a
  // physical reset before any SPI command is safe. Bounding that wait keeps a
  // missing or disconnected RAK13302 from trapping RadioLib in initialization.
  pinMode(SX126X_POWER_EN, OUTPUT);
  digitalWrite(SX126X_POWER_EN, LOW);

  pinMode(P_LORA_NSS, OUTPUT);
  digitalWrite(P_LORA_NSS, HIGH);
  pinMode(P_LORA_BUSY, INPUT);
  pinMode(P_LORA_RESET, OUTPUT);
  digitalWrite(P_LORA_RESET, LOW);
  // SX126x requires NRST low for at least 100 us. Keep the pulse explicit so
  // recovery does not accidentally depend on how long GPIO setup happens to
  // take in a particular Arduino core or optimization level.
  delay(1);
  digitalWrite(P_LORA_RESET, HIGH);

  const uint32_t reset_started = millis();
  while (digitalRead(P_LORA_BUSY) == HIGH) {
    serviceWatchdog();
    if (millis() - reset_started >= 100UL) return false;
    delay(1);
  }
  return true;
}

void RAK3401Board::enableRadioFrontend() {
  // CSD+CPS enable the SKY66122-11 FEM. SX1262 DIO2 performs the subsequent
  // TX/RX path switching in hardware.
  digitalWrite(SX126X_POWER_EN, HIGH);
  delay(1);
}
