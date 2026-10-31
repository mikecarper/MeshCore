#pragma once

#include <Arduino.h>
#include <SPI.h>
#include <RadioLib.h>
#include <driver/gpio.h>
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
  bool wake_failed_ = false;

  static bool inputPin(uint32_t pin) {
    return pin < 64 && GPIO_IS_VALID_GPIO(pin);
  }
  static bool outputPin(uint32_t pin) {
    return pin < 64 && GPIO_IS_VALID_OUTPUT_GPIO(pin);
  }
  bool waitReady(uint32_t timeout_ms) {
    const uint32_t started = now();
    while (!ready()) {
      if (uint32_t(now() - started) >= timeout_ms) return false;
      pause(1);
    }
    return true;
  }
  void wakeToStandby() {
    // Match RadioLib's wake sequence: a NOP wakes a sleeping SX126x, then
    // RC standby needs no TCXO or band-specific configuration. This path is
    // allowed only for SX126x-only recipes without a connected reset pin.
    spi_.beginTransaction(SPISettings(2000000, MSBFIRST, SPI_MODE0));
    digitalWrite(pins_.nss, LOW);
    spi_.transfer(RADIOLIB_SX126X_CMD_NOP);
    digitalWrite(pins_.nss, HIGH);
    spi_.endTransaction();
    if (!waitReady(250)) { wake_failed_ = true; return; }
    spi_.beginTransaction(SPISettings(2000000, MSBFIRST, SPI_MODE0));
    digitalWrite(pins_.nss, LOW);
    spi_.transfer(RADIOLIB_SX126X_CMD_SET_STANDBY);
    spi_.transfer(0);  // STDBY_RC, without enabling an oscillator supply.
    digitalWrite(pins_.nss, HIGH);
    spi_.endTransaction();
    pause(1);
  }
 public:
  ESP32LoRaChipProbeIO(SPIClass& spi, const LoRaChipProbePins& pins)
      : spi_(spi), pins_(pins) {}
  bool valid() const {
    return outputPin(pins_.nss)
        && (pins_.reset == RADIOLIB_NC ? !MESH_RADIO_AUTO_SX127X : outputPin(pins_.reset))
        && (pins_.busy == RADIOLIB_NC || inputPin(pins_.busy));
  }
  uint32_t now() const { return millis(); }
  void pause(uint32_t ms) {
#if MESH_USB_CONSOLE_COOPERATIVE
    serviceUsbLoggingPort();
    serviceUsbTerminalPort();
#endif
    delay(ms);
  }
  void reset() {
    if (!valid()) return;
    wake_failed_ = false;
    digitalWrite(pins_.nss, HIGH);
    pinMode(pins_.nss, OUTPUT);
    if (inputPin(pins_.busy)) pinMode(pins_.busy, INPUT_PULLDOWN);
    if (pins_.reset == RADIOLIB_NC) {
      wakeToStandby();
      return;
    }
    digitalWrite(pins_.reset, HIGH);
    pinMode(pins_.reset, OUTPUT);
    digitalWrite(pins_.reset, LOW);
    pause(1);
    digitalWrite(pins_.reset, HIGH);
    pause(10);
  }
  bool ready() const {
    return !wake_failed_ && (pins_.busy == RADIOLIB_NC || digitalRead(pins_.busy) == LOW);
  }
  uint8_t readSX127Version() {
    if (!valid()) return 0xff;
    spi_.beginTransaction(SPISettings(2000000, MSBFIRST, SPI_MODE0));
    digitalWrite(pins_.nss, LOW);
    spi_.transfer(0x42);  // SX127x RegVersion; read, not write.
    const uint8_t version = spi_.transfer(0);
    digitalWrite(pins_.nss, HIGH);
    spi_.endTransaction();
    return version;
  }
  bool readSX126Version(char* data, size_t length) {
    if (!valid() || !ready()) return false;
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
  if (!io.valid()) return LoRaRadioFamily::None;
  const auto family = probeLoRaChip(io);
  if (family == LoRaRadioFamily::SX127x && pins.busy != RADIOLIB_NC)
    pinMode(pins.busy, INPUT);
  return family;
}

}  // namespace mesh
