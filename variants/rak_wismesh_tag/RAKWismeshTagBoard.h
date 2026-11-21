#pragma once

#include <MeshCore.h>
#include <Arduino.h>
#include <helpers/NRF52Board.h>
#include <helpers/nrf52/ShutdownButton.h>

// built-ins
#define  PIN_VBAT_READ    5
#define  ADC_MULTIPLIER   (3 * 1.73 * 1.187 * 1000)

class RAKWismeshTagBoard : public NRF52BoardDCDC {
public:
  RAKWismeshTagBoard() : NRF52Board("WISMESHTAG_OTA") {}
  void begin();

#if defined(P_LORA_TX_LED) && defined(LED_STATE_ON)
  void applyLedPolicy() override {
    NRF52Board::applyLedPolicy();
    if (!mesh::leds::enabled()) digitalWrite(P_LORA_TX_LED, !LED_STATE_ON);
  }

  void onBeforeTransmit() override {
    if (mesh::leds::enabled()) digitalWrite(P_LORA_TX_LED, LED_STATE_ON);   // turn TX LED on
  }
  void onAfterTransmit() override {
    digitalWrite(P_LORA_TX_LED, !LED_STATE_ON);   // turn TX LED off
  }
#endif

  #define BATTERY_SAMPLES 8

  bool supportsBatteryCalibration() const override { return true; }

  uint16_t getBattMilliVolts() override {
    analogReadResolution(12);

    const uint32_t raw = readBatteryAdcAverage(PIN_VBAT_READ, BATTERY_SAMPLES);

    return calibrateBatteryMillivolts((ADC_MULTIPLIER * raw) / 4096);
  }

  const char* getManufacturerName() const override {
    return "RAK WisMesh Tag";
  }

  void shutdownPeripherals() override {
    NRF52Board::shutdownPeripherals();

    #ifdef BUZZER_EN
        digitalWrite(BUZZER_EN, LOW);
    #endif

    #ifdef PIN_GPS_EN
        digitalWrite(PIN_GPS_EN, LOW);
    #endif

    // set led on and wait for button release before poweroff
    #ifdef LED_PIN
    if (mesh::leds::enabled()) digitalWrite(LED_PIN, HIGH);
    #endif
    #ifdef BUTTON_PIN
    mesh::nrf52::armShutdownButton(BUTTON_PIN, LOW, NRF_GPIO_PIN_PULLUP);
    #endif
    #ifdef LED_GREEN
    digitalWrite(LED_GREEN, LOW);
    #endif
    #ifdef LED_BLUE
    digitalWrite(LED_BLUE, LOW);
    #endif
  }
};
