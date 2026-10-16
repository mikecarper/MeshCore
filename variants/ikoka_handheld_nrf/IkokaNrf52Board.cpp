#ifdef IKOKA_NRF52

#include <Arduino.h>
#include <Wire.h>

#include "IkokaNrf52Board.h"

void IkokaNrf52Board::begin() {
  NRF52Board::begin();

  // ensure we have pull ups on the screen i2c, this isn't always available
  // in hardware and it should only be 20k ohms. Disable the pullups if we
  // are using the rotated lcd breakout board
  #if defined(DISPLAY_CLASS) && DISPLAY_ROTATION == 0
    pinMode(PIN_WIRE_SDA, INPUT_PULLUP);
    pinMode(PIN_WIRE_SCL, INPUT_PULLUP);
  #endif

  pinMode(PIN_VBAT, INPUT);
  // Seeed's XIAO charging guidance requires READ_BAT_ENABLE to stay LOW.
  // Preload the safe latch before enabling the output; do not pulse HIGH.
  digitalWrite(VBAT_ENABLE, LOW);
  pinMode(VBAT_ENABLE, OUTPUT);

  // required button pullup is handled as part of button initilization
  // in target.cpp

#if defined(PIN_WIRE_SDA) && defined(PIN_WIRE_SCL)
  Wire.setPins(PIN_WIRE_SDA, PIN_WIRE_SCL);
#endif

  Wire.begin();

#ifdef P_LORA_TX_LED
  pinMode(P_LORA_TX_LED, OUTPUT);
  digitalWrite(P_LORA_TX_LED, HIGH);
#endif

  delay(10); // give sx1262 some time to power up
}

#endif
