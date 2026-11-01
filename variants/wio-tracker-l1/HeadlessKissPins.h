#pragma once

#include <Arduino.h>

inline void parkWioL1KissDisplayPins() {
  // OLED, e-paper and Lite share this board. The modem uses neither display;
  // keep optional e-paper reset asserted and its separate SPI bus deselected.
  digitalWrite(PIN_DISPLAY_CS, HIGH);
  pinMode(PIN_DISPLAY_CS, OUTPUT);
  digitalWrite(PIN_DISPLAY_RST, LOW);
  pinMode(PIN_DISPLAY_RST, OUTPUT);
}
