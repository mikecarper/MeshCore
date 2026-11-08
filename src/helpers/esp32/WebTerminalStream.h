#pragma once

#include <Arduino.h>
#include <esp_heap_caps.h>
#include <freertos/FreeRTOS.h>
#include <freertos/semphr.h>
#include <helpers/TerminalSession.h>

// Allocated only while a browser terminal is open. PSRAM is preferred; boards
// without it keep at least 32 KiB of internal heap after allocating scrollback.
// Large unread replies can grow the buffer; draining them returns it to the
// configured initial capacity (4 KiB by default).
class WebTerminalStream : public Stream {
  char* _storage;
  size_t _capacity = mesh::kTerminalInitialOutputCapacity;
  uint64_t _acknowledged = 0;
  SemaphoreHandle_t _lock;
  mesh::TerminalOutputBuffer _output;

  static char* allocate(size_t capacity) {
    void* data = heap_caps_malloc(capacity,
                                  MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
    if (!data && heap_caps_get_free_size(MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT)
                     >= capacity + 32768) {
      data = heap_caps_malloc(capacity,
                              MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT);
    }
    return static_cast<char*>(data);
  }

  void resize(size_t capacity) {
    char* storage = allocate(capacity);
    if (!storage) return;
    _output.rebind(storage, capacity);
    free(_storage);
    _storage = storage;
    _capacity = capacity;
  }

public:
  char session[24] = {0};
  mesh::TerminalCommandQueue queue;
  bool attached = false;
  bool running = false;
  bool closed = false;
  uint32_t last_seen = 0;

  WebTerminalStream() : _storage(allocate(mesh::kTerminalInitialOutputCapacity)),
                        _lock(xSemaphoreCreateMutex()),
                        _output(_storage, _storage ? mesh::kTerminalInitialOutputCapacity : 0) {}
  ~WebTerminalStream() override {
    free(_storage);
    if (_lock) vSemaphoreDelete(_lock);
  }
  bool ready() const { return _storage && _lock; }
  using Print::write;
  size_t write(uint8_t byte) override { return write(&byte, 1); }
  size_t write(const uint8_t* data, size_t len) override {
    if (!ready()) return 0;
    xSemaphoreTake(_lock, portMAX_DELAY);
    size_t wanted = _capacity;
    const uint64_t unread = _output.end() - _acknowledged + len;
    while (wanted < unread && wanted < mesh::kTerminalOutputCapacity) wanted *= 2;
    if (wanted > _capacity) resize(wanted);
    _output.append(data, len);
    xSemaphoreGive(_lock);
    return len;
  }
  size_t readOutput(uint64_t& cursor, char* out, size_t size, bool& lost) {
    xSemaphoreTake(_lock, portMAX_DELAY);
    if (cursor <= _output.end() && cursor > _acknowledged) _acknowledged = cursor;
    if (_capacity > mesh::kTerminalInitialOutputCapacity
        && _output.end() - _acknowledged <= mesh::kTerminalInitialOutputCapacity) {
      resize(mesh::kTerminalInitialOutputCapacity);
    }
    const size_t n = _output.read(cursor, out, size, lost);
    xSemaphoreGive(_lock);
    return n;
  }
  int availableForWrite() override {
    if (!ready()) return 0;
    xSemaphoreTake(_lock, portMAX_DELAY);
    const uint64_t unread = _output.end() - _acknowledged;
    const int room = unread < _capacity ? static_cast<int>(_capacity - unread) : 0;
    xSemaphoreGive(_lock);
    return room;
  }
  int available() override { return 0; }
  int read() override { return -1; }
  int peek() override { return -1; }
  void flush() override {}
};
