#include <Arduino.h>
#include <Wire.h>
#include <SPI.h>

#include "RAK3401Board.h"
#include "target.h"
#include "../../src/helpers/nrf52/TerminalPeripheralShutdown.h"

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

  // Disable switched 3V3 peripherals and the RAK13302 VD boost (IO2 EN).
  // Legacy VC hardware ties boost EN to VBAT unless its resistors are changed.
  digitalWrite(PIN_3V3_EN, LOW);

  if (reason == SHUTDOWN_REASON_LOW_VOLTAGE ||
      reason == SHUTDOWN_REASON_BOOT_PROTECT) {
    configureVoltageWake(power_config.lpcomp_ain_channel, power_config.lpcomp_refsel);
  }

  enterSystemOff(reason);
}
#endif

void RAK3401Board::shutdownPeripherals() {
  // Runtime sleep/recovery must retain the RAK13302/shared rail. Only the
  // terminal power-off/deep-sleep path reaches this virtual board hook.
  const bool ethernet_stopped = mesh::nrf52::terminalEthernetShutdown();
  // Ethernet cannot register on this board (same IO slot, SPI and IO3).
  // Still fail closed if an unexpected owner appears: even base radio sleep
  // would remap that live SPI bus and lowering the FEM would reset W5100S.
  if (!ethernet_stopped) return;
  const bool storage_power_safe = sensors.canRemoveSharedStoragePowerForDeepSleep();
  NRF52Board::shutdownPeripherals();
#if ENV_INCLUDE_GPS && defined(PIN_GPS_TX) && defined(PIN_GPS_RX)
  if (storage_power_safe) {
    if (Serial1) Serial1.end();  // never end an unstarted Adafruit UART
    pinMode(PIN_GPS_TX, INPUT);
    pinMode(PIN_GPS_RX, INPUT);
  }
#endif
  Wire.end();
  pinMode(PIN_WIRE_SDA, INPUT);
  pinMode(PIN_WIRE_SCL, INPUT);
  // The RAK13302 is an external, switched-supply radio. The base shutdown
  // sleeps it but leaves SPI and NRESET/NSS driven; those signals can feed
  // an unpowered module through its I/O protection. Release both controllers
  // first (the base prepares SPI, while this radio uses SPI1). begin/end is
  // deliberate: Adafruit SPI.end() asserts if its object has never begun.
  if (storage_power_safe) {
    SPI.end();
    SPI1.begin();
    SPI1.end();
    pinMode(P_LORA_NSS, INPUT);
    pinMode(P_LORA_RESET, INPUT);
    pinMode(P_LORA_SCLK, INPUT);
    pinMode(P_LORA_MOSI, INPUT);
    pinMode(P_LORA_MISO, INPUT);
  }
  // Sleeping the SX1262 alone does not disable the SKY66122's CSD input.
  // A legacy RAK13302 VC boost can remain supplied independently of IO2.
  digitalWrite(SX126X_POWER_EN, LOW);
  pinMode(SX126X_POWER_EN, OUTPUT);
  // PoE/external 5V enters the base separately from the switched 3V3_S rail.
  // An external NOR can still be recovering an unknown-WIP operation. Keep
  // its shared supply and driven CS/header pads until qualified teardown.
  if (storage_power_safe) {
    digitalWrite(PIN_3V3_EN, LOW);
    pinMode(PIN_3V3_EN, OUTPUT);
  }
}

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

  // WB_IO2 controls shared 3V3_S and the RAK13302 VD boost (or a modified VC).
  // Keep it enabled normally. Cycling it during radio recovery also resets
  // fitted GPS/sensors and does not reliably reset the SX1262 core.
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
