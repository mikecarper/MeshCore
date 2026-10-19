#pragma once

#include <Arduino.h>

namespace mesh { namespace ui { namespace techo_lite {

// LilyGo's P1.12 is SCREEN_BS1, the SSD1681 interface-mode strap, not a
// switched display supply. LOW selects the 4-wire SPI used by GxEPD2.
// SSD1681 Rev 0.13 pp. 10-11/29: 0x2F reads status over bidirectional SDA;
// bits 1:0 identify the chip as 01. Bits 5:4 are undefined after reset.
inline void writeCommand(uint8_t command) {
  digitalWrite(DISP_CS, LOW);
  digitalWrite(DISP_DC, LOW);
  pinMode(DISP_MOSI, OUTPUT);
  for (uint8_t mask = 0x80; mask; mask >>= 1) {
    digitalWrite(DISP_MOSI, command & mask ? HIGH : LOW);
    delayMicroseconds(5);
    digitalWrite(DISP_SCLK, HIGH);
    delayMicroseconds(5);
    digitalWrite(DISP_SCLK, LOW);
  }
  digitalWrite(DISP_CS, HIGH);
}

inline uint8_t readStatus(uint8_t bias) {
  digitalWrite(DISP_CS, LOW);
  digitalWrite(DISP_DC, LOW);
  pinMode(DISP_MOSI, OUTPUT);
  for (uint8_t mask = 0x80; mask; mask >>= 1) {
    digitalWrite(DISP_MOSI, 0x2F & mask ? HIGH : LOW);
    delayMicroseconds(5);
    digitalWrite(DISP_SCLK, HIGH);
    delayMicroseconds(5);
    // The controller drives the first read bit on the last command falling
    // edge. Release SDA first; there is no dummy read clock on this command.
    if (mask == 1) {
      pinMode(DISP_MOSI, bias);
      digitalWrite(DISP_DC, HIGH);
    }
    digitalWrite(DISP_SCLK, LOW);
  }
  uint8_t result = 0;
  for (uint8_t bit = 0; bit < 8; ++bit) {
    delayMicroseconds(5);
    result = uint8_t((result << 1) | (digitalRead(DISP_MOSI) ? 1u : 0u));
    digitalWrite(DISP_SCLK, HIGH);
    delayMicroseconds(5);
    digitalWrite(DISP_SCLK, LOW);
  }
  digitalWrite(DISP_CS, HIGH);
  return result;
}

inline bool waitReady() {
  // A disconnected BUSY line follows this pull-up and fails closed. Do not
  // equate an idle LOW alone with presence: status readback below is required.
  pinMode(DISP_BUSY, INPUT_PULLUP);
  for (uint16_t elapsed = 0; elapsed < 200; ++elapsed) {
    if (digitalRead(DISP_BUSY) == LOW) return true;
    delay(1);
  }
  return false;
}

inline void parkAbsent() {
  digitalWrite(DISP_RST, LOW);
  pinMode(DISP_CS, INPUT);
  pinMode(DISP_DC, INPUT);
  pinMode(DISP_MOSI, INPUT);
  pinMode(DISP_SCLK, INPUT);
  pinMode(DISP_BUSY, INPUT);
  // Keep BS1 LOW, as the manufacturer requires for the fitted SPI interface.
}

inline bool probe() {
  digitalWrite(DISP_BS1, LOW);
  pinMode(DISP_BS1, OUTPUT);
  digitalWrite(DISP_CS, HIGH);
  pinMode(DISP_CS, OUTPUT);
  digitalWrite(DISP_SCLK, LOW);
  pinMode(DISP_SCLK, OUTPUT);
  pinMode(DISP_DC, OUTPUT);
  digitalWrite(DISP_RST, LOW);
  pinMode(DISP_RST, OUTPUT);

  for (uint8_t attempt = 0; attempt < 2; ++attempt) {
    digitalWrite(DISP_RST, LOW);
    delay(2);  // Panel application sequence specifies at least 200 us.
    digitalWrite(DISP_RST, HIGH);
    delay(10);
    if (!waitReady()) continue;
    writeCommand(0x12);  // SW RESET, BUSY is HIGH while the command executes.
    if (!waitReady()) continue;
    uint8_t status[2];
    bool stable = true;
    for (uint8_t bias = 0; bias < 2; ++bias) {
      const uint8_t mode = bias == 0 ? INPUT_PULLUP : INPUT_PULLDOWN;
      (void)readStatus(mode);
      status[bias] = readStatus(mode) & 0xCFu;
      stable = ((readStatus(mode) & 0xCFu) == status[bias]) && stable;
    }
    // Check the chip ID, BUSY=0 and documented reserved-zero bits. Repeated
    // opposite-pull reads reject floating, stuck and unstable SDA inputs.
    if (stable && status[0] == 0x01u && status[1] == status[0]) {
      pinMode(DISP_MOSI, INPUT);
      pinMode(DISP_BUSY, INPUT);
      return true;
    }
  }
  parkAbsent();
  return false;
}

}}}  // namespace mesh::ui::techo_lite
