#pragma once
#include "RadioFastCode.h"
#include "TunableRadioHal.h"
#if defined(MESH_HIL_ESP_SPI_AB) && MESH_HIL_ESP_SPI_AB
#include "ESP32SpiABDiagnostics.h"
#endif

#ifndef MC_SX1262_SPI_HZ
#define MC_SX1262_SPI_HZ 8000000
#endif

// Opted-in ESP32 boards only. Keep RadioLib's transaction/CS, BUSY polling and
// status parsing unchanged; send each full-duplex buffer in one SPI operation.
// SX1262 boards retain their configured default; LR1110 boards pass 16 MHz.
class ESP32BufferedRadioHal : public TunableRadioHal {
#if defined(MESH_HIL_ESP_SPI_AB) && MESH_HIL_ESP_SPI_AB
  uint32_t _hilClockDivider = 0;
  bool _hilClockSeen = false;
#endif
 public:
  explicit ESP32BufferedRadioHal(SPIClass& bus, uint32_t hz = MC_SX1262_SPI_HZ)
      : TunableRadioHal(bus, hz, false, true,
                        hz >= 16000000 ? 16000000 : 8888888) {}
#if defined(MESH_HIL_ESP_SPI_AB) && MESH_HIL_ESP_SPI_AB
  MC_RADIO_FAST_CODE_ATTR void spiBeginTransaction() override {
    ArduinoHal::spiBeginTransaction();
    // The base call owns the transaction locks and has installed the radio's
    // divider. This SDK getter reads the live register without taking a lock.
    const uint32_t divider = spi->getClockDivider();
    if (!_hilClockSeen || divider != _hilClockDivider) {
      _hilClockSeen = true;
      _hilClockDivider = divider;
      mesh::esp_spiab::recordClock(divider, spiClockDivToFrequency(divider));
    }
  }
#endif
  MC_RADIO_FAST_CODE_ATTR void spiTransfer(uint8_t* out, size_t len, uint8_t* in) override {
    if (!bufferedTransfersEnabled()) ArduinoHal::spiTransfer(out, len, in);
    else if (len) spi->transferBytes(out, in, len);
  }
};
