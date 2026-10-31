#pragma once

#include <MeshCore.h>
#include <Arduino.h>
#include <helpers/NRF52Board.h>

class MeshTrackerX1Board : public NRF52BoardDCDC {
public:
  MeshTrackerX1Board() : NRF52Board("X1_OTA") {}
  void begin();

  bool supportsBatteryCalibration() const override {
#ifdef BATTERY_PIN
    return true;
#else
    return false;
#endif
  }

  uint16_t getBattMilliVolts() override {
  #ifdef BATTERY_PIN
   #ifdef PIN_BAT_ADC_EN
    digitalWrite(PIN_BAT_ADC_EN, HIGH);
   #endif
    analogReference(AR_INTERNAL_3_0);
    analogReadResolution(12);
    delay(10);
    const int raw = analogRead(BATTERY_PIN);
   #ifdef PIN_BAT_ADC_EN
    // The divider is only needed while sampling; do not leave it powered
    // between battery checks, including after an invalid ADC result.
    digitalWrite(PIN_BAT_ADC_EN, LOW);
   #endif

    analogReference(AR_DEFAULT);  // put back to default
    analogReadResolution(10);

    if (raw < 0 || raw > 4095) return 0;
    float volts = (raw * ADC_MULTIPLIER * AREF_VOLTAGE) / 4096;
    return calibrateBatteryMillivolts(volts * 1000);
  #else
    return 0;
  #endif
  }

  const char* getManufacturerName() const override {
    return "Seeed SenseCAP MeshTracker X1";
  }

  void shutdownPeripherals() override {
    NRF52Board::shutdownPeripherals();

    #ifdef HAS_GPS
        digitalWrite(GPS_VRTC_EN, LOW);
        digitalWrite(GPS_RESET, LOW);
        digitalWrite(GPS_SLEEP_INT, LOW);
        digitalWrite(GPS_RTC_INT, LOW);
        digitalWrite(GPS_EN, LOW);
    #endif

    #ifdef PIN_DRV_EN
        digitalWrite(PIN_DRV_EN, LOW);
    #endif

    #ifdef PIN_BAT_ADC_EN
        digitalWrite(PIN_BAT_ADC_EN, LOW);
    #endif

    #ifdef PIN_3V3_EN
        digitalWrite(PIN_3V3_EN, LOW);
    #endif

    // set led on and wait for button release before poweroff
    #ifdef LED_PIN
    if (mesh::leds::enabled()) digitalWrite(LED_PIN, HIGH);
    #endif
    #ifdef BUTTON_PIN
    while(digitalRead(BUTTON_PIN));
    #endif
    #ifdef LED_PIN
    digitalWrite(LED_PIN, LOW);
    #endif

    #ifdef BUTTON_PIN
    nrf_gpio_cfg_sense_input(BUTTON_PIN, NRF_GPIO_PIN_PULLDOWN, NRF_GPIO_PIN_SENSE_HIGH);
    #endif
  }
};
