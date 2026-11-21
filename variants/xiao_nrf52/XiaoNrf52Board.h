#pragma once

#include <MeshCore.h>
#include <Arduino.h>
#include <helpers/NRF52Board.h>
#include <helpers/nrf52/ShutdownButton.h>

#ifndef USER_BTN_PRESSED
#define USER_BTN_PRESSED LOW
#endif

#ifdef XIAO_NRF52

class XiaoNrf52Board : public NRF52BoardDCDC {
protected:
#if NRF52_POWER_MANAGEMENT
  void initiateShutdown(uint8_t reason) override;
#endif

public:
  XiaoNrf52Board() : NRF52Board("XIAO_NRF52_OTA") {}
  void begin();

#if defined(P_LORA_TX_LED)
  void applyLedPolicy() override {
    NRF52Board::applyLedPolicy();
    if (!mesh::leds::enabled()) digitalWrite(P_LORA_TX_LED, HIGH);
  }

  void onBeforeTransmit() override {
    if (mesh::leds::enabled()) digitalWrite(P_LORA_TX_LED, LOW);   // turn TX LED on
  }
  void onAfterTransmit() override {
    digitalWrite(P_LORA_TX_LED, HIGH);   // turn TX LED off
  }
#endif

  bool supportsBatteryCalibration() const override { return true; }

  uint16_t getBattMilliVolts() override;

  const char* getManufacturerName() const override {
#ifdef MANUFACTURER_STRING
    return MANUFACTURER_STRING;
#else
    return "Seeed Xiao-nrf52";
#endif
  }

  void shutdownPeripherals() override {
    NRF52Board::shutdownPeripherals();

    // set led on and wait for button release before poweroff
    if (mesh::leds::enabled()) digitalWrite(PIN_LED, LOW);
#ifdef PIN_USER_BTN
    mesh::nrf52::armShutdownButton(PIN_USER_BTN, USER_BTN_PRESSED, NRF_GPIO_PIN_PULLUP);
#endif
    digitalWrite(LED_GREEN, HIGH);
    digitalWrite(LED_BLUE, HIGH);
    digitalWrite(PIN_LED, HIGH);
  }
};

#endif
