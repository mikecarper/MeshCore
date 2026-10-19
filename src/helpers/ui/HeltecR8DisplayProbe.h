#pragma once

#include <Arduino.h>

namespace mesh { namespace ui { namespace heltec_r8 {

// Expansion Kit V2 routes the LCD's separate SDO to GPIO 45 (LCD_MISO).
// This is not the T114's bidirectional SDA interface. Probe before the SPI
// peripheral owns these pads, and never use the touch controller as proof of
// LCD presence. The LCD/TP reset is shared and must finish HIGH even on failure.
static_assert(PIN_TFT_MISO >= 0 && PIN_TFT_RST >= 0,
              "R8 display detection requires LCD SDO and shared reset");
static_assert(SDCARD_CS >= 0 && SDCARD_CS != PIN_TFT_CS
                  && SDCARD_CS != PIN_TFT_SDA && SDCARD_CS != PIN_TFT_SCL
                  && SDCARD_CS != PIN_TFT_MISO && SDCARD_CS != PIN_TFT_RST,
              "R8 LCD probe requires the separate expansion microSD chip select");
static_assert(PIN_TFT_MISO != PIN_TFT_SDA && PIN_TFT_MISO != PIN_TFT_SCL
                  && PIN_TFT_MISO != PIN_TFT_CS && PIN_TFT_MISO != PIN_TFT_DC
                  && PIN_TFT_MISO != PIN_TFT_RST,
              "R8 LCD SDO must be a separate input");

inline void backlightOff() {
  if (PIN_TFT_LEDA_CTL >= 0) {
    // Preload the inactive latch before enabling the output to avoid a flash.
    digitalWrite(PIN_TFT_LEDA_CTL, !PIN_TFT_LEDA_CTL_ACTIVE);
    pinMode(PIN_TFT_LEDA_CTL, OUTPUT);
  }
}

inline uint32_t readDisplayId(uint8_t bias) {
  pinMode(PIN_TFT_MISO, bias);
  delayMicroseconds(5);
  digitalWrite(PIN_TFT_CS, LOW);
  digitalWrite(PIN_TFT_DC, LOW);
  for (uint8_t mask = 0x80; mask; mask >>= 1) {
    digitalWrite(PIN_TFT_SDA, (0x04 & mask) ? HIGH : LOW);  // RDDID
    delayMicroseconds(5);
    digitalWrite(PIN_TFT_SCL, HIGH);
    delayMicroseconds(5);
    digitalWrite(PIN_TFT_SCL, LOW);
  }
  digitalWrite(PIN_TFT_DC, HIGH);
  // ST7789 RDDID has one dummy clock followed by three ID bytes on SDO.
  delayMicroseconds(5);
  digitalWrite(PIN_TFT_SCL, HIGH);
  delayMicroseconds(5);
  digitalWrite(PIN_TFT_SCL, LOW);
  uint32_t id = 0;
  for (uint8_t bit = 0; bit < 24; ++bit) {
    delayMicroseconds(5);
    id = (id << 1) | (digitalRead(PIN_TFT_MISO) ? 1u : 0u);
    digitalWrite(PIN_TFT_SCL, HIGH);
    delayMicroseconds(5);
    digitalWrite(PIN_TFT_SCL, LOW);
  }
  digitalWrite(PIN_TFT_CS, HIGH);
  return id;
}

inline void releaseBus() {
  pinMode(PIN_TFT_CS, INPUT);
  pinMode(PIN_TFT_DC, INPUT);
  pinMode(PIN_TFT_SDA, INPUT);
  pinMode(PIN_TFT_SCL, INPUT);
  pinMode(PIN_TFT_MISO, INPUT);
}

inline bool probe() {
  backlightOff();
  // V2.03 shares LCD SCK/MOSI/MISO with microSD; GPIO 3 selects the card.
  // A warm reset can leave it selected. Deselect before any probe clock and
  // retain that level when parking the LCD bus, including the failure path.
  digitalWrite(SDCARD_CS, HIGH);
  pinMode(SDCARD_CS, OUTPUT);
  digitalWrite(PIN_TFT_CS, HIGH);
  pinMode(PIN_TFT_CS, OUTPUT);
  digitalWrite(PIN_TFT_SCL, LOW);
  pinMode(PIN_TFT_SCL, OUTPUT);
  pinMode(PIN_TFT_SDA, OUTPUT);
  pinMode(PIN_TFT_DC, OUTPUT);
  digitalWrite(PIN_TFT_RST, HIGH);
  pinMode(PIN_TFT_RST, OUTPUT);

  for (uint8_t attempt = 0; attempt < 2; ++attempt) {
    digitalWrite(PIN_TFT_RST, LOW);
    delay(10);
    digitalWrite(PIN_TFT_RST, HIGH);
    delay(120);  // Reset/NVM settling, including the sleep-out interval.
    uint32_t ids[2];
    bool stable = true;
    for (uint8_t bias = 0; bias < 2; ++bias) {
      const uint8_t mode = bias == 0 ? INPUT_PULLUP : INPUT_PULLDOWN;
      (void)readDisplayId(mode);  // Discard the first read after changing bias.
      ids[bias] = readDisplayId(mode);
      stable = (readDisplayId(mode) == ids[bias]) && stable;
    }
    // IDs are programmable: require stable, actively driven data rather than
    // guessing one factory ID. Opposite pulls expose a floating absent panel.
    if (stable && ids[0] == ids[1] && ids[0] != 0 && ids[0] != 0xFFFFFFu) {
      pinMode(PIN_TFT_MISO, INPUT);
      return true;
    }
  }
  releaseBus();
  return false;
}

}}}  // namespace mesh::ui::heltec_r8
