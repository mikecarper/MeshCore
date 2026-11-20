#pragma once
#include <cassert>
#include <cstddef>
#include <cstdint>
#include <cstring>
#include <string>
#include <vector>

#define LOW 0
#define HIGH 1
#define OUTPUT 1
#define PIN_DISPLAY_CS 1
#define PIN_DISPLAY_DC 2
#define PIN_DISPLAY_RST 3
#define PIN_DISPLAY_BUSY 4
inline uint32_t test_ms = 0;
inline uint32_t millis() { return test_ms; }
inline void delay(unsigned) {}
inline void digitalWrite(int, int) {}
inline void pinMode(int, int) {}
