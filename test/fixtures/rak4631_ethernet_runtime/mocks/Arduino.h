#pragma once

#include <atomic>
#include <cassert>
#include <cstdarg>
#include <cstddef>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <mutex>
#include <string>

class Print {
public:
  virtual ~Print() = default;
  virtual size_t write(uint8_t) = 0;
  virtual size_t write(const uint8_t* data, size_t size) {
    size_t written = 0;
    while (written < size && write(data[written])) ++written;
    return written;
  }
  size_t write(const char* data) {
    return write(reinterpret_cast<const uint8_t*>(data), std::strlen(data));
  }
  size_t print(const char* data) { return write(data); }
  size_t println(const char* data = "") { return print(data) + print("\r\n"); }
  size_t printf(const char* format, ...) {
    char buffer[256];
    va_list args;
    va_start(args, format);
    const int length = vsnprintf(buffer, sizeof(buffer), format, args);
    va_end(args);
    assert(length >= 0 && length < static_cast<int>(sizeof(buffer)));
    return write(reinterpret_cast<const uint8_t*>(buffer), static_cast<size_t>(length));
  }
};
class Stream : public Print {
public:
  virtual int available() = 0;
  virtual int read() = 0;
  virtual int peek() = 0;
  virtual void flush() = 0;
  virtual int availableForWrite() { return 0; }
};

extern std::atomic<uint32_t> mock_millis;
inline uint32_t millis() { return mock_millis.load(); }
inline uint32_t pdMS_TO_TICKS(uint32_t milliseconds) { return milliseconds; }
struct MockFicr { uint32_t DEVICEID[2] = {0x123456, 0}; };
extern MockFicr mock_ficr;
#define NRF_FICR (&mock_ficr)
#define NRF_SPIM1 1
#define WB_IO2 34
#define OUTPUT 1
#define HIGH 1
#define LOW 0
void pinMode(int pin, int mode);
void digitalWrite(int pin, int value);

struct MockTask;
using TaskHandle_t = MockTask*;
using BaseType_t = int;
struct StaticTask_t { uint8_t bytes[192]; };
constexpr int pdPASS = 1;
constexpr int pdTRUE = 1;
constexpr uint32_t portMAX_DELAY = UINT32_MAX;
extern std::recursive_mutex mock_scheduler_mutex;
#define taskENTER_CRITICAL() mock_scheduler_mutex.lock()
#define taskEXIT_CRITICAL() mock_scheduler_mutex.unlock()
BaseType_t xTaskCreate(void (*function)(void*), const char*, unsigned stack_words,
                      void* argument, int priority, TaskHandle_t* handle);
void xTaskNotifyGive(TaskHandle_t handle);
uint32_t ulTaskNotifyTake(int clear_on_exit, uint32_t ticks);
void vTaskDelay(uint32_t ticks);
void vTaskDelete(TaskHandle_t handle);

void mock_bus_touch();
void mock_worker_bus_touch();
void mock_spi_begin();
void mock_spi_end();
