#pragma once
typedef unsigned char byte;
typedef unsigned int uint32_t;
typedef int int32_t;
typedef unsigned int size_t;
typedef unsigned char uint8_t;
typedef unsigned short uint16_t;
typedef short int16_t;
typedef signed char int8_t;
typedef unsigned int uintptr_t;
#define LOW 0
#define HIGH 1
#define OUTPUT 1
extern uint32_t now_ms;
extern unsigned tone_calls;
extern unsigned silence_calls;
inline uint32_t millis() { return now_ms; }
inline int isdigit(int c) { return c >= '0' && c <= '9'; }
inline void tone(byte, unsigned int, unsigned long) { ++tone_calls; }
inline void noTone(byte) { ++silence_calls; }
inline void pinMode(int, int) {}
inline void digitalWrite(int, int) {}
