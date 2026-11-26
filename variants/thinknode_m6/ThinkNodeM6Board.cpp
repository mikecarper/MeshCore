#include "ThinkNodeM6Board.h"
#include <Arduino.h>

#ifdef THINKNODE_M6

#include <Wire.h>

// A reset during an RTC read can leave SDA held low. Release the clock line
// between pulses so the RTC can finish its byte without bus contention.
static void recoverRtcBus() {
  pinMode(PIN_WIRE_SDA, INPUT_PULLUP);
  pinMode(PIN_WIRE_SCL, INPUT_PULLUP);
  for (uint8_t pulse = 0; pulse < 9 && digitalRead(PIN_WIRE_SDA) == LOW; ++pulse) {
    if (digitalRead(PIN_WIRE_SCL) == LOW) break;
    digitalWrite(PIN_WIRE_SCL, LOW);
    pinMode(PIN_WIRE_SCL, OUTPUT);
    delayMicroseconds(5);
    pinMode(PIN_WIRE_SCL, INPUT_PULLUP);
    delayMicroseconds(5);
  }
}

void ThinkNodeM6Board::begin() {
  NRF52Board::begin();

  recoverRtcBus();
  Wire.begin();

#ifdef P_LORA_TX_LED
  pinMode(P_LORA_TX_LED, OUTPUT);
  digitalWrite(P_LORA_TX_LED, LOW);
#endif

  pinMode(EXT_PWR_DETECT, INPUT);
  pinMode(EXT_CHRG_DETECT, INPUT);

  delay(10); // give sx1262 some time to power up
}

uint16_t ThinkNodeM6Board::getBattMilliVolts() {
  int adcvalue = 0;

  digitalWrite(PIN_ADC_CTRL, HIGH);
  analogReference(AR_INTERNAL_3_0);
  analogReadResolution(12);
  delay(10);

  // ADC range is 0..3000mV and resolution is 12-bit (0..4095)
  adcvalue = analogRead(PIN_VBAT_READ);
  digitalWrite(PIN_ADC_CTRL, LOW);
  // Convert the raw value to compensated mv, taking the resistor-
  // divider into account (providing the actual LIPO voltage)
  return calibrateBatteryMillivolts((uint16_t)((float)adcvalue * REAL_VBAT_MV_PER_LSB));
}
#endif
