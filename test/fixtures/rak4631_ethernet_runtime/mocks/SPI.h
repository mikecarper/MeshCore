#pragma once
#include <Arduino.h>
struct SPISettings {};
class SPIClass {
public:
  SPIClass(int, int, int, int) {}
  void begin() { mock_spi_begin(); }
  void end() { mock_spi_end(); }
  void beginTransaction(const SPISettings&) { mock_bus_touch(); }
  void endTransaction() { mock_bus_touch(); }
};
