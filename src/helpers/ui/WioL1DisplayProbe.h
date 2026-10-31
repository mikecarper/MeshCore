#pragma once

#include <Arduino.h>

namespace mesh { namespace ui { namespace wio_l1 {

inline void i2cLow(uint8_t pin) {
  digitalWrite(pin, LOW);
  pinMode(pin, OUTPUT);
}

inline bool i2cClockHigh() {
  pinMode(PIN_WIRE_SCL, INPUT_PULLUP);
  for (uint8_t retry = 0; retry < 50; ++retry) {
    if (digitalRead(PIN_WIRE_SCL) == HIGH) return true;
    delayMicroseconds(10);
  }
  return false;
}

// Called while Wire is disabled, before any radio/RTC background work starts.
// GPIO only drives LOW; INPUT_PULLUP releases each open-drain line. A stuck
// SCL/SDA fails in bounded time, unlike the nRF52 Wire driver's event waits.
inline bool probeOled(uint8_t address) {
  pinMode(PIN_WIRE_SDA, INPUT_PULLUP);
  bool ready = i2cClockHigh() && digitalRead(PIN_WIRE_SDA) == HIGH;
  bool ack = false;
  if (ready) {
    i2cLow(PIN_WIRE_SDA);
    delayMicroseconds(5);
    i2cLow(PIN_WIRE_SCL);
    for (uint8_t mask = 0x80; mask && ready; mask >>= 1) {
      if ((address << 1) & mask) pinMode(PIN_WIRE_SDA, INPUT_PULLUP);
      else i2cLow(PIN_WIRE_SDA);
      delayMicroseconds(5);
      ready = i2cClockHigh();
      delayMicroseconds(5);
      i2cLow(PIN_WIRE_SCL);
    }
    if (ready) {
      pinMode(PIN_WIRE_SDA, INPUT_PULLUP);
      delayMicroseconds(5);
      ready = i2cClockHigh();
      ack = ready && digitalRead(PIN_WIRE_SDA) == LOW;
      i2cLow(PIN_WIRE_SCL);
    }
    i2cLow(PIN_WIRE_SDA);
    ready = i2cClockHigh() && ready;
    delayMicroseconds(5);
  }
  pinMode(PIN_WIRE_SDA, INPUT_PULLUP);  // STOP, or release a failed bus.
  pinMode(PIN_WIRE_SCL, INPUT_PULLUP);
  delayMicroseconds(5);
  return ready && ack && digitalRead(PIN_WIRE_SDA) == HIGH;
}

// The L1's SSD1680 uses bidirectional SDA on the display MOSI pin. Read
// status 0x2F before starting SPI1; BUSY alone cannot identify a fitted panel.
// SSD1680 Rev 0.14 pp. 10-11,29: bits 1:0 are 01; bits 5:4 are undefined
// after reset. Opposite input pulls reject an absent or disconnected display.
inline void writeCommand(uint8_t command) {
  digitalWrite(PIN_DISPLAY_CS, LOW);
  digitalWrite(PIN_DISPLAY_DC, LOW);
  pinMode(PIN_SPI1_MOSI, OUTPUT);
  for (uint8_t mask = 0x80; mask; mask >>= 1) {
    digitalWrite(PIN_SPI1_MOSI, command & mask ? HIGH : LOW);
    delayMicroseconds(5);
    digitalWrite(PIN_SPI1_SCK, HIGH);
    delayMicroseconds(5);
    digitalWrite(PIN_SPI1_SCK, LOW);
  }
  digitalWrite(PIN_DISPLAY_CS, HIGH);
}

inline uint8_t readStatus(uint8_t bias) {
  digitalWrite(PIN_DISPLAY_CS, LOW);
  digitalWrite(PIN_DISPLAY_DC, LOW);
  pinMode(PIN_SPI1_MOSI, OUTPUT);
  for (uint8_t mask = 0x80; mask; mask >>= 1) {
    digitalWrite(PIN_SPI1_MOSI, 0x2F & mask ? HIGH : LOW);
    delayMicroseconds(5);
    digitalWrite(PIN_SPI1_SCK, HIGH);
    delayMicroseconds(5);
    if (mask == 1) {
      // Release SDA before the controller drives the final falling edge.
      pinMode(PIN_SPI1_MOSI, bias);
      digitalWrite(PIN_DISPLAY_DC, HIGH);
    }
    digitalWrite(PIN_SPI1_SCK, LOW);
  }
  uint8_t result = 0;
  for (uint8_t bit = 0; bit < 8; ++bit) {
    delayMicroseconds(5);
    result = uint8_t((result << 1) | (digitalRead(PIN_SPI1_MOSI) ? 1u : 0u));
    digitalWrite(PIN_SPI1_SCK, HIGH);
    delayMicroseconds(5);
    digitalWrite(PIN_SPI1_SCK, LOW);
  }
  digitalWrite(PIN_DISPLAY_CS, HIGH);
  return result;
}

inline bool waitReady() {
  pinMode(PIN_DISPLAY_BUSY, INPUT_PULLUP);
  for (uint16_t elapsed = 0; elapsed < 200; ++elapsed) {
    if (digitalRead(PIN_DISPLAY_BUSY) == LOW) return true;
    delay(1);
  }
  return false;
}

inline void parkAbsent() {
  digitalWrite(PIN_DISPLAY_RST, LOW);
  pinMode(PIN_DISPLAY_CS, INPUT);
  pinMode(PIN_DISPLAY_DC, INPUT);
  pinMode(PIN_SPI1_MOSI, INPUT);
  pinMode(PIN_SPI1_SCK, INPUT);
  pinMode(PIN_DISPLAY_BUSY, INPUT);
}

inline bool probeEink() {
  digitalWrite(PIN_DISPLAY_CS, HIGH);
  pinMode(PIN_DISPLAY_CS, OUTPUT);
  digitalWrite(PIN_SPI1_SCK, LOW);
  pinMode(PIN_SPI1_SCK, OUTPUT);
  pinMode(PIN_DISPLAY_DC, OUTPUT);
  digitalWrite(PIN_DISPLAY_RST, LOW);
  pinMode(PIN_DISPLAY_RST, OUTPUT);
  for (uint8_t attempt = 0; attempt < 2; ++attempt) {
    digitalWrite(PIN_DISPLAY_RST, LOW);
    delay(2);
    digitalWrite(PIN_DISPLAY_RST, HIGH);
    delay(10);
    if (!waitReady()) continue;
    writeCommand(0x12);
    if (!waitReady()) continue;
    uint8_t status[2];
    bool stable = true;
    for (uint8_t bias = 0; bias < 2; ++bias) {
      const uint8_t mode = bias == 0 ? INPUT_PULLUP : INPUT_PULLDOWN;
      (void)readStatus(mode);
      status[bias] = readStatus(mode) & 0xCFu;
      stable = ((readStatus(mode) & 0xCFu) == status[bias]) && stable;
    }
    if (stable && status[0] == 0x01u && status[1] == status[0]) {
      pinMode(PIN_SPI1_MOSI, INPUT);
      pinMode(PIN_DISPLAY_BUSY, INPUT);
      return true;
    }
  }
  parkAbsent();
  return false;
}

}}}  // namespace mesh::ui::wio_l1
