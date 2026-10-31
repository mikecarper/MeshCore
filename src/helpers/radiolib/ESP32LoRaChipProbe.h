#pragma once

#include <Arduino.h>
#include <SPI.h>
#include <RadioLib.h>
#include <helpers/LoRaChipProbe.h>
#include <helpers/UsbLogging.h>

namespace mesh {

struct LoRaChipProbePins {
  uint32_t nss;
  uint32_t reset;
  uint32_t busy;
};

class ESP32LoRaChipProbeIO {
  SPIClass& spi_;
  const LoRaChipProbePins& pins_;
 public:
  ESP32LoRaChipProbeIO(SPIClass& spi, const LoRaChipProbePins& pins)
      : spi_(spi), pins_(pins) {}
  uint32_t now() const { return millis(); }
  void pause(uint32_t ms) {
#if MESH_USB_CONSOLE_COOPERATIVE
    serviceUsbLoggingPort();
    serviceUsbTerminalPort();
#endif
    delay(ms);
  }
  void reset() {
    digitalWrite(pins_.nss, HIGH);
    pinMode(pins_.nss, OUTPUT);
    pinMode(pins_.busy, INPUT_PULLDOWN);
    digitalWrite(pins_.reset, HIGH);
    pinMode(pins_.reset, OUTPUT);
    digitalWrite(pins_.reset, LOW);
    pause(1);
    digitalWrite(pins_.reset, HIGH);
    pause(10);
  }
  bool ready() const { return digitalRead(pins_.busy) == LOW; }
  uint8_t readSX127Version() {
    spi_.beginTransaction(SPISettings(2000000, MSBFIRST, SPI_MODE0));
    digitalWrite(pins_.nss, LOW);
    spi_.transfer(0x42);  // SX127x RegVersion; read, not write.
    const uint8_t version = spi_.transfer(0);
    digitalWrite(pins_.nss, HIGH);
    spi_.endTransaction();
    return version;
  }
  bool readSX126Version(char* data, size_t length) {
    if (!ready()) return false;
    spi_.beginTransaction(SPISettings(2000000, MSBFIRST, SPI_MODE0));
    digitalWrite(pins_.nss, LOW);
    spi_.transfer(RADIOLIB_SX126X_CMD_READ_REGISTER);
    spi_.transfer(RADIOLIB_SX126X_REG_VERSION_STRING >> 8);
    spi_.transfer(RADIOLIB_SX126X_REG_VERSION_STRING & 0xff);
    spi_.transfer(0);  // command status precedes register contents.
    for (size_t i = 0; i < length; ++i) data[i] = spi_.transfer(0);
    digitalWrite(pins_.nss, HIGH);
    spi_.endTransaction();
    delayMicroseconds(1);
    return true;
  }
};

// Caller powers the radio and starts this board's SPI pins first. Leave NSS
// deselected; the chosen chip's normal initialization performs its own reset.
inline LoRaRadioFamily probeUniversalLoRaChip(SPIClass& spi,
                                            const LoRaChipProbePins& pins) {
  ESP32LoRaChipProbeIO io(spi, pins);
  const auto family = probeLoRaChip(io);
  if (family == LoRaRadioFamily::SX127x) pinMode(pins.busy, INPUT);
  return family;
}

}  // namespace mesh
