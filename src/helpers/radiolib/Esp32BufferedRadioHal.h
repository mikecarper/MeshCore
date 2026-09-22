#pragma once

#include <hal/Arduino/ArduinoHal.h>

// RadioLib's Arduino HAL transfers one byte at a time. ESP32 SPIClass can
// transfer each LR1110 command in one transaction while retaining RadioLib's
// existing CS, BUSY, status and transaction handling. LR1110 permits 16 MHz.
class Esp32BufferedRadioHal : public ArduinoHal {
public:
  explicit Esp32BufferedRadioHal(SPIClass& bus, uint32_t hz = 16000000)
      : ArduinoHal(bus, SPISettings(hz, MSBFIRST, SPI_MODE0)) {}

  void spiTransfer(uint8_t* out, size_t len, uint8_t* in) override {
    if (len) spi->transferBytes(out, in, len);
  }
};
