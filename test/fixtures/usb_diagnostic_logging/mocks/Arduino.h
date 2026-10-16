#pragma once

#include <cassert>
#include <cstdarg>
#include <cstddef>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <string>

extern uint32_t mock_millis;
inline uint32_t millis() { return mock_millis; }
inline void delay(uint32_t value) { mock_millis += value; }

class Stream {
 public:
  virtual ~Stream() = default;
  virtual int available() = 0;
  virtual int read() = 0;
  virtual int peek() = 0;
  virtual void flush() = 0;
  virtual int availableForWrite() { return 0; }
  virtual size_t write(uint8_t value) = 0;
  virtual size_t write(const uint8_t* data, size_t size) = 0;
  size_t printf(const char* format, ...) {
    char buffer[256];
    va_list args;
    va_start(args, format);
    const int count = vsnprintf(buffer, sizeof(buffer), format, args);
    va_end(args);
    assert(count >= 0 && static_cast<size_t>(count) < sizeof(buffer));
    return write(reinterpret_cast<const uint8_t*>(buffer), static_cast<size_t>(count));
  }
};

class RawSerial {
 public:
  int availableForWrite() { return 256; }
  template <typename... Args> void printf(Args...) { assert(false && "raw USB write"); }
  template <typename... Args> void println(Args...) { assert(false && "raw USB write"); }
};
extern RawSerial Serial;

struct MockFicr { uint32_t DEVICEID[2] = {0x123456, 0}; };
extern MockFicr mock_ficr;
#define NRF_FICR (&mock_ficr)
#define NRF_SPIM1 1
#define WB_IO2 2
#define OUTPUT 1
#define INPUT 0
#define HIGH 1
#define INPUT_PULLUP 2
inline void pinMode(int, int) {}
inline void digitalWrite(int, int) {}
inline int digitalRead(int) { return HIGH; }
inline void vTaskDelete(void*) {}
inline void vTaskDelay(uint32_t value) { delay(value); }
inline uint32_t pdMS_TO_TICKS(uint32_t value) { return value; }
using TaskHandle_t = void*;
#define pdPASS 1
#define pdTRUE 1
#define taskENTER_CRITICAL() do {} while(0)
#define taskEXIT_CRITICAL() do {} while(0)
inline void xTaskNotifyGive(TaskHandle_t) {}
inline uint32_t ulTaskNotifyTake(int, uint32_t value) { delay(value);return 0; }
inline int xTaskCreate(void (*)(void*), const char*, int, void*, int, TaskHandle_t*) { return pdPASS; }
