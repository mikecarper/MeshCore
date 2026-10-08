#pragma once
#include <Arduino.h>
namespace mesh {
inline bool isUsbDebugLoggingEnabled() { return false; }
class NoUsbLog : public Stream {
public:
  int available() override { return 0; }
  int read() override { return -1; }
  int peek() override { return -1; }
  void flush() override {}
  size_t write(uint8_t) override { assert(false); return 0; }
};
inline Stream& usbLoggingPort() { static NoUsbLog port; return port; }
}
