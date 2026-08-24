#include <Arduino.h>
#include <SPI.h>
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

  // Cold-start the switched auxiliary rail once. It powers the RAK13302 FEM
  // and any fitted WisBlock peripherals, not the SX1262 core. Runtime radio
  // retries must leave this rail up so GPS and sensors are not reset every
  // minute.
  pinMode(SX126X_POWER_EN, OUTPUT);
  digitalWrite(SX126X_POWER_EN, LOW);
  pinMode(PIN_3V3_EN, OUTPUT);
  digitalWrite(PIN_3V3_EN, LOW);
  delay(100);
  digitalWrite(PIN_3V3_EN, HIGH);
  delay(10);

  recoverRadio();
}

bool RAK3401Board::recoverRadio() {
  // The RAK13302 FEM/boost supply is on 3V3_S, but the SX1262 itself is on the
  // unswitched 3V3 rail. A battery-backed radio can therefore remain asleep
  // across MCU resets. Quiesce the frontend and bus first, then reset the
  // radio and use Semtech's NSS/GET_STATUS wake sequence if BUSY is still
  // asserted. Leave 3V3_S enabled here because it is shared with GPS/sensors
  // and does not power-cycle the SX1262 core anyway.
  pinMode(SX126X_POWER_EN, OUTPUT);
  digitalWrite(SX126X_POWER_EN, LOW);

  if (radio_spi_initialized) {
    SPI1.end();
    radio_spi_initialized = false;
  }

  pinMode(P_LORA_RESET, OUTPUT);
  digitalWrite(P_LORA_RESET, LOW);
  pinMode(P_LORA_NSS, INPUT);
  pinMode(P_LORA_SCLK, INPUT);
  pinMode(P_LORA_MOSI, INPUT);
  pinMode(P_LORA_MISO, INPUT);
  pinMode(P_LORA_BUSY, INPUT);
  pinMode(P_LORA_DIO_1, INPUT);

  pinMode(PIN_3V3_EN, OUTPUT);
  digitalWrite(PIN_3V3_EN, HIGH);
  digitalWrite(P_LORA_RESET, HIGH);
  delay(20);

  SPI1.setPins(P_LORA_MISO, P_LORA_SCLK, P_LORA_MOSI);
  SPI1.begin();
  radio_spi_initialized = true;
  pinMode(P_LORA_NSS, OUTPUT);
  digitalWrite(P_LORA_NSS, HIGH);
  pinMode(P_LORA_BUSY, INPUT);
  if (digitalRead(P_LORA_BUSY) == LOW) return true;

  // SetSleep is exited by an NSS falling edge. Do not use RadioLib for this
  // transaction: its normal pre-transfer BUSY wait is exactly what prevents
  // a sleeping/stuck radio from receiving the wake command.
  SPI1.beginTransaction(SPISettings(2000000, MSBFIRST, SPI_MODE0));
  digitalWrite(P_LORA_NSS, LOW);
  SPI1.transfer(0xC0);  // SX126x GET_STATUS
  SPI1.transfer(0x00);  // NOP
  digitalWrite(P_LORA_NSS, HIGH);
  SPI1.endTransaction();

  const uint32_t wake_started = millis();
  while (digitalRead(P_LORA_BUSY) == HIGH) {
    if (millis() - wake_started >= 1000) return false;
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
