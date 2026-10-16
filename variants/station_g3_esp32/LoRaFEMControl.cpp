#include "LoRaFEMControl.h"
#include <helpers/ESP32GpioHold.h>

#include <Arduino.h>
#include <driver/rtc_io.h>
#include "StationG3RadioSlot.h"

void LoRaFEMControl::init() {
#ifdef P_PA1_EN
  mesh::esp32::releaseOutputAfterDeepSleep((gpio_num_t)P_PA1_EN,
      pa_gain_enabled ? P_PA1_EN_ACTIVE : !P_PA1_EN_ACTIVE);
  applyPAGain();
#endif

  // Neither socket's receive amplifier is enabled before radio detection.
  for (uint8_t slot = 1; slot <= 2; ++slot)
    mesh::esp32::releaseOutputAfterDeepSleep(
        (gpio_num_t)station_g3::pinsForSlot(slot).lna, HIGH);
}

void LoRaFEMControl::setSleepModeEnable() {
#ifdef P_PA1_EN
  // PA PL1 low/open selects the lower of the two hardware-jumper-selected levels.
  digitalWrite(P_PA1_EN, !P_PA1_EN_ACTIVE);
#endif
#ifdef P_PRIMARY_LNA_EN
  // LNA P/S are active LOW. Bypass/disable the receive amplifier on shutdown,
  // rather than retaining the same enabled state used during normal receive.
  digitalWrite(station_g3::activePins().lna, !P_PRIMARY_LNA_EN_ACTIVE);
#endif
}

void LoRaFEMControl::setTxModeEnable() {
  // Latch the requested PA level here, before the SX1262 starts driving the PA. PA PL1
  // retargets the PA's DC-DC rail, so moving it mid-transmit collapses the supply while
  // the PA is still driven at full input power.
  applyPAGain();
#ifdef P_PRIMARY_LNA_EN
  digitalWrite(station_g3::activePins().lna, !P_PRIMARY_LNA_EN_ACTIVE);
#endif
}

void LoRaFEMControl::setRxModeEnable() {
#ifdef P_PRIMARY_LNA_EN
  digitalWrite(station_g3::activePins().lna,
      lna_enabled ? P_PRIMARY_LNA_EN_ACTIVE : !P_PRIMARY_LNA_EN_ACTIVE);
#endif
}

void LoRaFEMControl::setLNAEnable(bool enabled) {
  lna_enabled = enabled;
  setRxModeEnable();
}

void LoRaFEMControl::setPAGainEnable(bool enabled) {
  // Recorded only -- the pin is driven from setTxModeEnable(). The PA level only matters
  // while transmitting, so deferring costs nothing and keeps the rail change out of an
  // in-flight transmit (the CLI runs on every main-loop pass, including mid-TX).
  pa_gain_enabled = enabled;
}

void LoRaFEMControl::applyPAGain() {
#ifdef P_PA1_EN
  digitalWrite(P_PA1_EN, pa_gain_enabled ? P_PA1_EN_ACTIVE : !P_PA1_EN_ACTIVE);
#endif
}

bool LoRaFEMControl::canControlLNA() const {
#ifdef P_PRIMARY_LNA_EN
  return true;
#else
  return false;
#endif
}

bool LoRaFEMControl::canControlPAGain() const {
#ifdef P_PA1_EN
  return true;
#else
  return false;
#endif
}
