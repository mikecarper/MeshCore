#include "TBeam1WBoard.h"
#include <helpers/radiolib/CustomSX1262Wrapper.h>

void TBeam1WBoard::begin() {
  ESP32Board::begin();

  // Power on radio module (must be done before radio init)
  pinMode(SX126X_POWER_EN, OUTPUT);
  digitalWrite(SX126X_POWER_EN, HIGH);
  radio_powered = true;
  delay(10);  // Allow radio to power up

  // GPIO21/CTRL is initialized by RadioLib with the normal RXEN mode table.
  // Do not drive it independently during board startup.

  // Initialize LED
  pinMode(LED_PIN, OUTPUT);
  digitalWrite(LED_PIN, LOW);

  // Initialize fan control (on by default - 1W PA can overheat)
  pinMode(FAN_CTRL_PIN, OUTPUT);
  digitalWrite(FAN_CTRL_PIN, HIGH);
}

void TBeam1WBoard::onBeforeTransmit() {
  // RF switching handled by RadioLib via SX126X_DIO2_AS_RF_SWITCH and setRfSwitchPins()
  digitalWrite(LED_PIN, HIGH);  // TX LED on
}

void TBeam1WBoard::onAfterTransmit() {
  digitalWrite(LED_PIN, LOW);   // TX LED off
}

uint16_t TBeam1WBoard::getBattMilliVolts() {
  // T-Beam 1W uses 7.4V battery with voltage divider
  // ADC reads through divider - adjust multiplier based on actual divider ratio
  analogReadResolution(12);
  uint32_t raw = 0;
  for (int i = 0; i < 8; i++) {
    raw += analogRead(BATTERY_PIN);
  }
  raw = raw / 8;
  // Assuming voltage divider ratio from ADC_MULTIPLIER
  // 3.3V reference, 12-bit ADC (4095 max)
  return static_cast<uint16_t>((raw * 3300 * ADC_MULTIPLIER) / 4095);
}

const char* TBeam1WBoard::getManufacturerName() const {
  return "LilyGo T-Beam 1W";
}

void TBeam1WBoard::powerOff() {
  // Turn off radio LNA (CTRL pin must be LOW when not receiving)
  digitalWrite(SX126X_RXEN, LOW);

  // Turn off radio power
  digitalWrite(SX126X_POWER_EN, LOW);
  radio_powered = false;

  // Turn off LED and fan
  digitalWrite(LED_PIN, LOW);
  digitalWrite(FAN_CTRL_PIN, LOW);

  ESP32Board::powerOff();
}

void TBeam1WBoard::attachRadioDriver(CustomSX1262Wrapper* driver) {
  radio_driver = driver;
}

bool TBeam1WBoard::setLoRaFemLnaEnabled(bool enable) {
  // The normal/default ON state is already installed by radio.std_init().
  // Avoid touching GPIO21 or reconfiguring the radio during boot unless the
  // saved setting actually requests a different state.
  if (lna_enabled == enable) {
    return radio_driver != nullptr;
  }

  if (radio_driver == nullptr || !radio_driver->setExternalRxLnaEnabled(enable)) {
    return false;
  }

  // This is the requested RX-mode state. TX and standby always force CTRL low
  // regardless of this preference, protecting both the LNA and PA paths.
  lna_enabled = enable;
  return true;
}

bool TBeam1WBoard::canControlLoRaFemLna() const {
  return radio_driver != nullptr;
}

bool TBeam1WBoard::isLoRaFemLnaEnabled() const {
  return lna_enabled;
}

void TBeam1WBoard::setFanEnabled(bool enabled) {
  digitalWrite(FAN_CTRL_PIN, enabled ? HIGH : LOW);
}

bool TBeam1WBoard::isFanEnabled() const {
  return digitalRead(FAN_CTRL_PIN) == HIGH;
}
