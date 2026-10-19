#pragma once

#include <Arduino.h>

namespace mesh { namespace ui { namespace rc32 {

// Heltec's optional T108 uses NV3001B on the shared, bidirectional SDA pin.
// Read commands/ID: meshtastic/firmware src/mesh/NodeDB.cpp, TFT_NV3001B_DETECT.
// Pins/power: HelTecAutomation/RadioCore_Library examples/NV3001B_Display.
// Probe before claiming the SPI peripheral; no independent MISO is connected.
inline uint32_t readIdCommand(uint8_t command, uint8_t bits, uint8_t dummy, uint8_t bias) {
  digitalWrite(PIN_TFT_CS, LOW);
  digitalWrite(PIN_TFT_DC, LOW);
  pinMode(PIN_TFT_SDA, OUTPUT);
  for (uint8_t mask = 0x80; mask; mask >>= 1) {
    digitalWrite(PIN_TFT_SDA, (command & mask) ? HIGH : LOW);
    delayMicroseconds(5);
    digitalWrite(PIN_TFT_SCL, HIGH);
    delayMicroseconds(5);
    // Release after the final command bit is latched, before the panel can
    // drive its first response bit on the falling edge.
    if (mask == 1) pinMode(PIN_TFT_SDA, bias);
    digitalWrite(PIN_TFT_SCL, LOW);
  }
  digitalWrite(PIN_TFT_DC, HIGH);
  for (uint8_t i = 0; i < dummy; ++i) {
    delayMicroseconds(5);
    digitalWrite(PIN_TFT_SCL, HIGH);
    delayMicroseconds(5);
    digitalWrite(PIN_TFT_SCL, LOW);
  }
  uint32_t id = 0;
  for (uint8_t i = 0; i < bits; ++i) {
    delayMicroseconds(5);
    id = (id << 1) | (digitalRead(PIN_TFT_SDA) ? 1u : 0u);
    digitalWrite(PIN_TFT_SCL, HIGH);
    delayMicroseconds(5);
    digitalWrite(PIN_TFT_SCL, LOW);
  }
  digitalWrite(PIN_TFT_CS, HIGH);
  return id;
}

inline uint32_t readId(uint8_t bias, bool individual_bytes) {
  if (!individual_bytes) return readIdCommand(0x04, 24, 1, bias);
  const uint32_t first = readIdCommand(0xDA, 8, 0, bias);
  const uint32_t second = readIdCommand(0xDB, 8, 0, bias);
  return (first << 16) | (second << 8) | readIdCommand(0xDC, 8, 0, bias);
}

inline bool stableId(bool individual_bytes) {
  uint32_t ids[2];
  bool stable = true;
  for (uint8_t i = 0; i < 2; ++i) {
    const uint8_t bias = i == 0 ? INPUT_PULLUP : INPUT_PULLDOWN;
    (void)readId(bias, individual_bytes);  // Discard stale data after changing pull.
    ids[i] = readId(bias, individual_bytes);
    stable = (readId(bias, individual_bytes) == ids[i]) && stable;
  }
  // Recognize this controller, not merely nonzero data from another header
  // peripheral. Opposing pulls also reject an absent or weakly driven line.
  return stable && ids[0] == ids[1] && ids[0] == 0x300101;
}

inline void powerOff() {
  digitalWrite(PIN_TFT_BL, !PIN_TFT_BL_ACTIVE);
  digitalWrite(PIN_TFT_RST, LOW);
  // Do not back-power an unpowered panel through GPIO or the SPI peripheral.
  pinMode(PIN_TFT_CS, INPUT);
  pinMode(PIN_TFT_DC, INPUT);
  pinMode(PIN_TFT_SDA, INPUT);
  pinMode(PIN_TFT_SCL, INPUT);
  digitalWrite(PIN_TFT_EN, !PIN_TFT_EN_ACTIVE);
}

inline bool probe() {
  digitalWrite(PIN_TFT_BL, !PIN_TFT_BL_ACTIVE);
  pinMode(PIN_TFT_BL, OUTPUT);
  digitalWrite(PIN_TFT_EN, !PIN_TFT_EN_ACTIVE);
  pinMode(PIN_TFT_EN, OUTPUT);
  digitalWrite(PIN_TFT_CS, HIGH);
  pinMode(PIN_TFT_CS, OUTPUT);
  digitalWrite(PIN_TFT_SCL, LOW);
  pinMode(PIN_TFT_SCL, OUTPUT);
  pinMode(PIN_TFT_DC, OUTPUT);
  digitalWrite(PIN_TFT_RST, LOW);
  pinMode(PIN_TFT_RST, OUTPUT);
  digitalWrite(PIN_TFT_EN, PIN_TFT_EN_ACTIVE);
  delay(120);
  for (uint8_t attempt = 0; attempt < 2; ++attempt) {
    digitalWrite(PIN_TFT_RST, LOW);
    delay(120);
    digitalWrite(PIN_TFT_RST, HIGH);
    delay(120);
    // Some T108 panels answer only the individual ID-byte commands.
    if (stableId(false) || stableId(true)) return true;
  }
  powerOff();
  return false;
}

}}}  // namespace mesh::ui::rc32
