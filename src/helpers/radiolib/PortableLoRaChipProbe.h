#pragma once

#include <Arduino.h>
#include <SPI.h>
#include <RadioLib.h>
#include <helpers/LR1121ChipProbe.h>

namespace mesh {

struct PortableLoRaChipProbePins {
  uint32_t nss;
  uint32_t reset;
  uint32_t busy;
};

// nRF52 boards supply an Arduino pin map; never pass an NC sentinel or an
// unmapped pin to its GPIO API. Existing ESP32 families keep their own probe.
class PortableLoRaChipProbeIO {
  SPIClass& spi_;
  const PortableLoRaChipProbePins& pins_;

  static bool validPin(uint32_t pin) {
#if defined(NRF52_PLATFORM) && defined(PINS_COUNT)
    return pin < PINS_COUNT && g_ADigitalPinMap[pin] < 48;
#elif defined(NUM_DIGITAL_PINS)
    return pin < NUM_DIGITAL_PINS;
#else
    (void)pin;
    return false;
#endif
  }
  bool waitReady() {
    const uint32_t started = now();
    while (!ready()) {
      if (uint32_t(now() - started) >= 250) return false;
      pause(1);
    }
    return true;
  }
  void beginTransaction() {
    spi_.beginTransaction(SPISettings(1000000, MSBFIRST, SPI_MODE0));
    digitalWrite(pins_.nss, LOW);
  }
  void endTransaction() {
    digitalWrite(pins_.nss, HIGH);
    spi_.endTransaction();
    delayMicroseconds(1);
  }

 public:
  PortableLoRaChipProbeIO(SPIClass& spi, const PortableLoRaChipProbePins& pins)
      : spi_(spi), pins_(pins) {}
  bool valid() const {
    return validPin(pins_.nss) && validPin(pins_.reset) && validPin(pins_.busy)
        && pins_.nss != pins_.reset && pins_.nss != pins_.busy
        && pins_.reset != pins_.busy;
  }
  uint32_t now() const { return millis(); }
  void pause(uint32_t ms) { delay(ms); }
  void reset() {
    if (!valid()) return;
    digitalWrite(pins_.nss, HIGH);
    pinMode(pins_.nss, OUTPUT);
    pinMode(pins_.busy, INPUT);
    digitalWrite(pins_.reset, HIGH);
    pinMode(pins_.reset, OUTPUT);
    digitalWrite(pins_.reset, LOW);
    pause(1);
    digitalWrite(pins_.reset, HIGH);
    pause(10);
  }
  bool ready() const { return valid() && digitalRead(pins_.busy) == LOW; }
  bool readSX126Version(char* data, size_t length) {
    if (!data || length != 6 || !ready()) return false;
    beginTransaction();
    spi_.transfer(RADIOLIB_SX126X_CMD_READ_REGISTER);
    spi_.transfer(RADIOLIB_SX126X_REG_VERSION_STRING >> 8);
    spi_.transfer(RADIOLIB_SX126X_REG_VERSION_STRING & 0xff);
    spi_.transfer(0);
    for (size_t i = 0; i < length; ++i) data[i] = spi_.transfer(0);
    endTransaction();
    return waitReady();
  }
  bool readLR1121Version(uint8_t* data, size_t length) {
    if (!data || length != 4 || !ready()) return false;
    // LR11xx reads use two NSS transactions: send the 16-bit read command,
    // wait for BUSY, then clock one status byte followed by its four bytes.
    beginTransaction();
    spi_.transfer(0x01);
    spi_.transfer(0x01);  // GetVersion 0x0101; never a write or RF command.
    endTransaction();
    if (!waitReady()) return false;
    beginTransaction();
    const uint8_t status = spi_.transfer(0);
    for (size_t i = 0; i < length; ++i) data[i] = spi_.transfer(0);
    endTransaction();
    const uint8_t command_status = status & 0x0e;
    return status != 0xff && (command_status == 0x04 || command_status == 0x06)
        && waitReady();
  }
};

// Caller has powered the board and started SPI with its shared radio pins.
// Identifying a chip does not initialize either backend or attach an IRQ.
inline LoRaRadioFamily probePortableLoRaChip(SPIClass& spi,
                                           const PortableLoRaChipProbePins& pins) {
  PortableLoRaChipProbeIO io(spi, pins);
  return io.valid() ? probeSX1262OrLR1121(io) : LoRaRadioFamily::None;
}

}  // namespace mesh
