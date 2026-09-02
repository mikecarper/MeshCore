#pragma once

#include <cstdint>
#include <cmath>
#include <cstdio>
#include "Stream.h"

inline uint32_t g_mock_millis = 0;

constexpr uint8_t LOW = 0;
constexpr uint8_t HIGH = 1;
constexpr uint8_t INPUT = 0;
constexpr uint8_t OUTPUT = 1;
constexpr uint8_t INPUT_PULLUP = 2;
constexpr uint8_t INPUT_PULLDOWN = 3;

inline uint8_t g_mock_pin_modes[64] = {};
inline uint8_t g_mock_pin_levels[64] = {};
inline int g_mock_analog_levels[64] = {};

using std::isnan;

inline uint32_t millis() {
  return g_mock_millis;
}

inline void delay(uint32_t ms) {
  g_mock_millis += ms;
}

inline char* ltoa(long value, char* dest, int base) {
  if (base == 10) {
    std::snprintf(dest, 16, "%ld", value);
  } else {
    dest[0] = '\0';
  }
  return dest;
}

inline void pinMode(uint8_t pin, uint8_t mode) {
  if (pin < 64) g_mock_pin_modes[pin] = mode;
}

inline void digitalWrite(uint8_t pin, uint8_t level) {
  if (pin < 64) g_mock_pin_levels[pin] = level;
}

inline int digitalRead(uint8_t pin) {
  return pin < 64 ? g_mock_pin_levels[pin] : LOW;
}

inline int analogRead(uint8_t pin) {
  return pin < 64 ? g_mock_analog_levels[pin] : 0;
}

inline void resetArduinoMock() {
  g_mock_millis = 0;
  for (uint8_t pin = 0; pin < 64; pin++) {
    g_mock_pin_modes[pin] = INPUT;
    g_mock_pin_levels[pin] = LOW;
    g_mock_analog_levels[pin] = 0;
  }
}
