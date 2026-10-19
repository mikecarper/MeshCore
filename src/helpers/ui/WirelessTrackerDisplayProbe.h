#pragma once

#include <Arduino.h>

namespace mesh { namespace ui { namespace wireless_tracker {

// Wireless Tracker V1's optional ST7735 panel is connected directly to SDA
// (GPIO42), not radio MISO. The ST7735 serial read protocol returns data on
// that same pin: release it before the last command clock's falling edge.
// The caller owns a reference to the shared TFT/GNSS peripheral power rail.
inline uint32_t readDisplayId(uint8_t bias) {
  digitalWrite(PIN_TFT_CS, LOW);
  digitalWrite(PIN_TFT_DC, LOW);
  pinMode(PIN_TFT_SDA, OUTPUT);
  for (uint8_t mask = 0x80; mask; mask >>= 1) {
    digitalWrite(PIN_TFT_SDA, (0x04 & mask) ? HIGH : LOW);
    delayMicroseconds(5);
    digitalWrite(PIN_TFT_SCL, HIGH);
    delayMicroseconds(5);
    if (mask == 1) pinMode(PIN_TFT_SDA, bias);
    digitalWrite(PIN_TFT_SCL, LOW);
  }
  digitalWrite(PIN_TFT_DC, HIGH);
  // RDDID returns 24 bits after one turnaround/dummy clock.
  delayMicroseconds(5);
  digitalWrite(PIN_TFT_SCL, HIGH);
  delayMicroseconds(5);
  digitalWrite(PIN_TFT_SCL, LOW);
  uint32_t id = 0;
  for (uint8_t bit = 0; bit < 24; ++bit) {
    delayMicroseconds(5);
    id = (id << 1) | (digitalRead(PIN_TFT_SDA) ? 1u : 0u);
    digitalWrite(PIN_TFT_SCL, HIGH);
    delayMicroseconds(5);
    digitalWrite(PIN_TFT_SCL, LOW);
  }
  digitalWrite(PIN_TFT_CS, HIGH);
  return id;
}

inline void parkAbsentPanel() {
  digitalWrite(PIN_TFT_LEDA_CTL, !PIN_TFT_LEDA_CTL_ACTIVE);
  digitalWrite(PIN_TFT_RST, LOW);
  pinMode(PIN_TFT_CS, INPUT);
  pinMode(PIN_TFT_DC, INPUT);
  pinMode(PIN_TFT_SDA, INPUT);
  pinMode(PIN_TFT_SCL, INPUT);
  // Do not drive Vext: the caller releases only the display's power claim,
  // allowing GNSS or another peripheral to retain its independent claim.
}

inline bool probe() {
  digitalWrite(PIN_TFT_LEDA_CTL, !PIN_TFT_LEDA_CTL_ACTIVE);
  pinMode(PIN_TFT_LEDA_CTL, OUTPUT);
  digitalWrite(PIN_TFT_CS, HIGH);
  pinMode(PIN_TFT_CS, OUTPUT);
  digitalWrite(PIN_TFT_SCL, LOW);
  pinMode(PIN_TFT_SCL, OUTPUT);
  pinMode(PIN_TFT_DC, OUTPUT);
  digitalWrite(PIN_TFT_RST, LOW);
  pinMode(PIN_TFT_RST, OUTPUT);

  for (uint8_t attempt = 0; attempt < 2; ++attempt) {
    digitalWrite(PIN_TFT_RST, LOW);
    delay(10);
    digitalWrite(PIN_TFT_RST, HIGH);
    delay(120);
    uint32_t ids[2];
    bool stable = true;
    for (uint8_t bias = 0; bias < 2; ++bias) {
      const uint8_t mode = bias == 0 ? INPUT_PULLUP : INPUT_PULLDOWN;
      (void)readDisplayId(mode);
      ids[bias] = readDisplayId(mode);
      stable = (readDisplayId(mode) == ids[bias]) && stable;
    }
    // IDs are programmable; require stable driven data under opposite pulls.
    if (stable && ids[0] == ids[1] && ids[0] != 0 && ids[0] != 0xFFFFFFu) {
      pinMode(PIN_TFT_SDA, INPUT);
      return true;
    }
  }
  parkAbsentPanel();
  return false;
}

}}}  // namespace mesh::ui::wireless_tracker
