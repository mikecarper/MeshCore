#pragma once

#include <Arduino.h>

inline void parkHeltecR8KissDisplayPins() {
  // These optional Expansion Kit pins are unused by the headless modem.
  // Preload the output levels before enabling them to avoid a backlight pulse
  // or selecting the LCD / microSD during startup.
  digitalWrite(44, LOW);   // LCD backlight, active high
  pinMode(44, OUTPUT);
  digitalWrite(47, HIGH);  // LCD chip select
  pinMode(47, OUTPUT);
  digitalWrite(3, HIGH);   // Expansion Kit V2 microSD chip select
  pinMode(3, OUTPUT);
  digitalWrite(21, LOW);   // Shared LCD/touch or OLED reset
  pinMode(21, OUTPUT);
}
