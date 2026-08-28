#include "TBeam1WBoard.h"
#include <helpers/radiolib/CustomSX1262Wrapper.h>
#include <driver/gpio.h>
#include <driver/rtc_io.h>

#ifndef FAN_TEMP_ON_C
#define FAN_TEMP_ON_C 45.0f
#endif
#ifndef FAN_TEMP_OFF_C
#define FAN_TEMP_OFF_C 41.0f
#endif
#ifndef FAN_MIN_RUN_TIME_MS
#define FAN_MIN_RUN_TIME_MS 5000UL
#endif
#ifndef FAN_TEMP_POLL_INTERVAL_MS
#define FAN_TEMP_POLL_INTERVAL_MS 1000UL
#endif

void TBeam1WBoard::begin() {
  ESP32Board::begin();

  // ESP32Board::enterDeepSleep() holds NSS high. The LilyGo factory firmware
  // can also hold the dedicated radio LDO off. These pad holds survive wake
  // and can survive a subsequent software reset, so release them on every
  // startup before RadioLib tries to select the SX1262.
  esp_err_t nss_hold_result = rtc_gpio_hold_dis((gpio_num_t)P_LORA_NSS);
  esp_err_t dio_result = rtc_gpio_deinit((gpio_num_t)P_LORA_DIO_1);
  esp_err_t power_hold_result = gpio_hold_dis((gpio_num_t)SX126X_POWER_EN);
  MESH_DEBUG_PRINTLN("T-Beam 1W pad recovery: NSS=%d DIO1=%d LDO=%d",
                     nss_hold_result, dio_result, power_hold_result);

  // The microSD card and radio share this SPI bus. Keep the card deselected
  // before RadioLib starts; a floating SD CS can make the card drive MISO and
  // make the SX1262 appear absent (all reads return 0xFF).
  pinMode(SDCARD_CS, OUTPUT);
  digitalWrite(SDCARD_CS, HIGH);

  // A software reset can leave the external radio regulator enabled while the
  // ESP32 and SX1262 are in different states. Force a real regulator cycle so
  // flashing or rebooting does not require removing USB/battery power before
  // RadioLib probes the chip.
  pinMode(SX126X_POWER_EN, OUTPUT);
  powerCycleRadio();

  // GPIO21/CTRL is initialized by RadioLib with the normal RXEN mode table.
  // Do not drive it independently during board startup.

  // Initialize LED
  pinMode(LED_PIN, OUTPUT);
  digitalWrite(LED_PIN, LOW);

  // Start with the fan off. updateFanControl() turns it on at the validated
  // temperature threshold and applies hysteresis, avoiding the former
  // always-on battery drain while still failing safe on an invalid reading.
  pinMode(FAN_CTRL_PIN, OUTPUT);
  fan_running = false;
  fan_temperature_valid = false;
  fan_started_at_ms = 0;
  fan_last_poll_ms = 0;
  digitalWrite(FAN_CTRL_PIN, LOW);
}

void TBeam1WBoard::powerCycleRadio() {
  digitalWrite(SX126X_POWER_EN, LOW);
  radio_powered = false;
  delay(100);
  int power_off_level = digitalRead(SX126X_POWER_EN);
  digitalWrite(SX126X_POWER_EN, HIGH);
  radio_powered = true;
  delay(100);
  MESH_DEBUG_PRINTLN("T-Beam 1W radio LDO cycle: off=%d on=%d",
                     power_off_level, digitalRead(SX126X_POWER_EN));
}

void TBeam1WBoard::onBeforeTransmit() {
  // RF switching handled by RadioLib via SX126X_DIO2_AS_RF_SWITCH and setRfSwitchPins()
  updateFanControl();
  digitalWrite(LED_PIN, HIGH);  // TX LED on
}

void TBeam1WBoard::onAfterTransmit() {
  digitalWrite(LED_PIN, LOW);   // TX LED off
  updateFanControl();
}

uint16_t TBeam1WBoard::getBattMilliVolts() {
  // T-Beam 1W uses a 2S battery through the GPIO4 divider. These settings
  // match the hardware-tested fork and the Meshtastic board definition.
  static bool adc_initialized = false;
  if (!adc_initialized) {
    pinMode(BATTERY_PIN, INPUT);
    analogReadResolution(12);
    analogSetPinAttenuation(BATTERY_PIN, ADC_11db);
    adc_initialized = true;
  }

  uint32_t raw = 0;
  for (int i = 0; i < BATTERY_SENSE_SAMPLES; i++) {
    raw += analogRead(BATTERY_PIN);
    delayMicroseconds(100);
  }
  raw /= BATTERY_SENSE_SAMPLES;
  return static_cast<uint16_t>(
      (static_cast<float>(raw) / 4095.0f) * 3300.0f * ADC_MULTIPLIER);
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
  setFanEnabled(false);

  ESP32Board::powerOff();
}

void TBeam1WBoard::attachRadioDriver(CustomSX1262Wrapper* driver) {
  radio_driver = driver;
  // radio.std_init() installs the normal RXEN table, whose RX state is on.
  // A setting saved while the radio was unavailable still needs to be pushed
  // into that fresh table when the radio later recovers.
  lna_driver_synced = lna_enabled;
}

bool TBeam1WBoard::setLoRaFemLnaEnabled(bool enable) {
  if (radio_driver == nullptr) {
    // Preserve the requested setting while booting in radio-unavailable mode.
    // It will be applied after a later successful probe.
    lna_enabled = enable;
    lna_driver_synced = false;
    return true;
  }

  // The normal/default ON state is already installed by radio.std_init().
  // Avoid touching GPIO21 or reconfiguring the radio unless the desired table
  // differs from the one currently installed in the radio driver.
  if (lna_driver_synced && lna_enabled == enable) return true;

  if (radio_driver == nullptr || !radio_driver->setExternalRxLnaEnabled(enable)) {
    return false;
  }

  // This is the requested RX-mode state. TX and standby always force CTRL low
  // regardless of this preference, protecting both the LNA and PA paths.
  lna_enabled = enable;
  lna_driver_synced = true;
  return true;
}

bool TBeam1WBoard::canControlLoRaFemLna() const {
  return true;
}

bool TBeam1WBoard::isLoRaFemLnaEnabled() const {
  return lna_enabled;
}

void TBeam1WBoard::setFanEnabled(bool enabled) {
  if (fan_running == enabled) return;
  fan_running = enabled;
  if (enabled) fan_started_at_ms = millis();
  digitalWrite(FAN_CTRL_PIN, enabled ? HIGH : LOW);
}

bool TBeam1WBoard::isFanEnabled() const {
  return fan_running;
}

void TBeam1WBoard::updateFanControl() {
  const uint32_t now = millis();
  if (!fan_temperature_valid
      || static_cast<uint32_t>(now - fan_last_poll_ms)
          >= FAN_TEMP_POLL_INTERVAL_MS) {
    const float temperature_c = getMCUTemperature();
    fan_last_poll_ms = now;
    if (isnan(temperature_c)) {
      // Cooling is safer than silently leaving the 1 W PA unprotected if the
      // ESP32 temperature sensor ever reports an invalid sample.
      setFanEnabled(true);
      return;
    }
    fan_last_temperature_c = temperature_c;
    fan_temperature_valid = true;
  }

  if (fan_running) {
    if (static_cast<uint32_t>(now - fan_started_at_ms) >= FAN_MIN_RUN_TIME_MS
        && fan_last_temperature_c < FAN_TEMP_OFF_C) {
      setFanEnabled(false);
    }
  } else if (fan_last_temperature_c >= FAN_TEMP_ON_C) {
    setFanEnabled(true);
  }
}
