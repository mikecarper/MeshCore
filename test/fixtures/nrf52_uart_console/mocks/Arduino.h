#pragma once
#pragma GCC diagnostic push
#pragma GCC diagnostic ignored "-Wunused-parameter"
#pragma GCC diagnostic ignored "-Wsign-compare"
#include "../../../mocks/Arduino.h"
#pragma GCC diagnostic pop
#include <cassert>
#include <deque>
#include <functional>
#include <vector>

inline bool isInISR() { return false; }
class MockUart : public Stream {
 public:
  std::deque<uint8_t> input;
  std::vector<uint8_t> output;
  int capacity = 0;
  std::function<void()> duringWrite;
  int available() override { return input.size(); }
  int read() override { if (input.empty()) return -1; const int value=input.front();input.pop_front();return value; }
  int peek() override { return input.empty() ? -1 : input.front(); }
  int availableForWrite() override { return capacity; }
  size_t write(uint8_t byte) override { return write(&byte,1); }
  size_t write(const uint8_t* data, size_t size) override {
    if (duringWrite) { auto callback=duringWrite;duringWrite={};callback(); }
    assert(size <= static_cast<size_t>(capacity));
    output.insert(output.end(),data,data+size);capacity-=size;return size;
  }
  void flush() override { assert(false && "UART facade must not wait for wire drain"); }
};
class UnusedNativeUsb : public Stream {
 public:
  int available() override { assert(false);return 0; }
  int read() override { assert(false);return -1; }
  int peek() override { assert(false);return -1; }
  int availableForWrite() override { assert(false);return 0; }
  size_t write(uint8_t) override { assert(false);return 0; }
  size_t write(const uint8_t*,size_t) override { assert(false);return 0; }
  void flush() override { assert(false); }
};
extern MockUart Serial1;
extern UnusedNativeUsb Serial;
