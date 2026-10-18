#pragma once

#include <Arduino.h>

namespace mesh { namespace ui { namespace t114 {

// The stock ST7789V2 shares command/output data on SDA; SPI1's MISO is not
// connected to the panel. Read before starting SPI1 or allocating its buffers.
inline uint32_t readDisplayId(uint8_t bias) {
  digitalWrite(PIN_TFT_CS, LOW);
  digitalWrite(PIN_TFT_DC, LOW);
  pinMode(PIN_TFT_SDA, OUTPUT);
  for (uint8_t mask = 0x80; mask; mask >>= 1) {
    digitalWrite(PIN_TFT_SDA, (0x04 & mask) ? HIGH : LOW);  // RDDID
    delayMicroseconds(5);
    digitalWrite(PIN_TFT_SCL, HIGH);
    delayMicroseconds(5);
    // The panel may drive SDA on the last command bit's falling edge.
    // Release our output after that bit was latched, before lowering SCL.
    if (mask == 1) pinMode(PIN_TFT_SDA, bias);
    digitalWrite(PIN_TFT_SCL, LOW);
  }

  digitalWrite(PIN_TFT_DC, HIGH);
  // RDDID has one turnaround/dummy clock before its three ID bytes.
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

inline void powerOff() {
#ifdef PIN_TFT_LEDA_CTL_ACTIVE
  digitalWrite(PIN_TFT_LEDA_CTL, !PIN_TFT_LEDA_CTL_ACTIVE);
#else
  digitalWrite(PIN_TFT_LEDA_CTL, HIGH);
#endif
  digitalWrite(PIN_TFT_RST, LOW);
  digitalWrite(PIN_TFT_VDD_CTL, HIGH);
  // Avoid feeding an unpowered/absent panel through its bus pins.
  pinMode(PIN_TFT_CS, INPUT);
  pinMode(PIN_TFT_DC, INPUT);
  pinMode(PIN_TFT_SDA, INPUT);
  pinMode(PIN_TFT_SCL, INPUT);
}

inline bool probe() {
#ifdef PIN_TFT_LEDA_CTL_ACTIVE
  digitalWrite(PIN_TFT_LEDA_CTL, !PIN_TFT_LEDA_CTL_ACTIVE);
#else
  digitalWrite(PIN_TFT_LEDA_CTL, HIGH);
#endif
  pinMode(PIN_TFT_LEDA_CTL, OUTPUT);
  digitalWrite(PIN_TFT_VDD_CTL, HIGH);
  pinMode(PIN_TFT_VDD_CTL, OUTPUT);
  digitalWrite(PIN_TFT_CS, HIGH);
  pinMode(PIN_TFT_CS, OUTPUT);
  digitalWrite(PIN_TFT_SCL, LOW);
  pinMode(PIN_TFT_SCL, OUTPUT);
  pinMode(PIN_TFT_DC, OUTPUT);
  digitalWrite(PIN_TFT_RST, LOW);
  pinMode(PIN_TFT_RST, OUTPUT);
  digitalWrite(PIN_TFT_VDD_CTL, LOW);

  for (uint8_t attempt = 0; attempt < 2; ++attempt) {
    digitalWrite(PIN_TFT_RST, LOW);
    delay(10);
    digitalWrite(PIN_TFT_RST, HIGH);
    // NVM ID loading needs >=5 ms after reset; 120 ms also covers Sleep Out.
    delay(120);
    uint32_t ids[2];
    bool stable = true;
    for (uint8_t bias = 0; bias < 2; ++bias) {
      const uint8_t mode = bias == 0 ? INPUT_PULLUP : INPUT_PULLDOWN;
      (void)readDisplayId(mode);  // The stock panel needs an initial read.
      ids[bias] = readDisplayId(mode);
      stable = (readDisplayId(mode) == ids[bias]) && stable;
    }
    // IDs are factory-programmable. Check driven, stable data instead of a
    // single magic value; opposite pulls expose a floating/absent SDA line.
    if (stable && ids[0] == ids[1] && ids[0] != 0 && ids[0] != 0xFFFFFFu) {
      pinMode(PIN_TFT_SDA, INPUT);
      return true;
    }
  }
  powerOff();
  return false;
}

}}}  // namespace mesh::ui::t114
