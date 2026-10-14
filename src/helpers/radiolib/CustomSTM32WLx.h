#pragma once

#include <RadioLib.h>
#include <math.h>
#include "SX126xMorseRssi.h"

class CustomSTM32WLx : public STM32WLx {
  uint32_t _preambleMillis = 66;
  uint32_t _maxPayloadMillis = 3934;
  uint32_t _activityAt = 0;
  bool _headerSeen = false;

  public:
    CustomSTM32WLx(STM32WLx_Module *mod) : STM32WLx(mod) { }
    // STM32WLx inherits the SX126x LoRa packet setters. Unlike CustomSX1262,
    // this driver has no acknowledged fast-hop cache: every protocol change
    // repairs the complete CRC/header/IQ tuple through the ordinary API.
    int16_t setProfileProtocol(uint8_t syncWord) {
      int16_t status = STM32WLx::setSyncWord(syncWord);
      if (status == RADIOLIB_ERR_NONE) status = STM32WLx::setCRC(1);
      if (status == RADIOLIB_ERR_NONE) status = STM32WLx::explicitHeader();
      if (status == RADIOLIB_ERR_NONE) status = STM32WLx::invertIQ(false);
      return status;
    }
    int16_t setMorseFastRssi() {
      return setSX126xMorseRssiWindow(this);
    }
    float getMorseRssi() {
      uint8_t raw = 0;
      const int16_t status = this->getMod()->SPIreadStream(RADIOLIB_SX126X_CMD_GET_RSSI_INST, &raw, 1);
      return status == RADIOLIB_ERR_NONE ? raw * -0.5f : NAN;
    }

    int16_t startReceive() override {
      // include the PREAMBLE_DETECTED irq bit in reported flags
      return STM32WLx::startReceive(RADIOLIB_SX126X_RX_TIMEOUT_INF, RADIOLIB_IRQ_RX_DEFAULT_FLAGS | (1UL << RADIOLIB_IRQ_PREAMBLE_DETECTED), RADIOLIB_IRQ_RX_DEFAULT_MASK, 0);
    }

    bool isReceiving() {
      uint32_t irq = getIrqFlags();
      bool preamble = irq & RADIOLIB_SX126X_IRQ_PREAMBLE_DETECTED; // bit 2
      bool header   = irq & RADIOLIB_SX126X_IRQ_HEADER_VALID;      // bit 4
      bool hdrErr   = irq & RADIOLIB_SX126X_IRQ_HEADER_ERR;        // bit 5
      uint32_t now  = millis();
      if (hdrErr) {
        clearIrqFlags(RADIOLIB_SX126X_IRQ_PREAMBLE_DETECTED | RADIOLIB_SX126X_IRQ_HEADER_VALID | RADIOLIB_SX126X_IRQ_HEADER_ERR | RADIOLIB_SX126X_IRQ_SYNC_WORD_VALID);
        _activityAt = 0;
        _headerSeen = false;
        return false;
      }
      if (!header && _headerSeen) {
        // something cleared the header flag, reset our state.
        _activityAt = 0; _headerSeen = false;
        return false;
      }

      if (header) {
        if (!_headerSeen) { _headerSeen = true; _activityAt = now; };
        if (now - _activityAt > _maxPayloadMillis) {
          MESH_DEBUG_PRINTLN("Clearing header IRQ after %ums", _maxPayloadMillis);
          clearIrqFlags(RADIOLIB_SX126X_IRQ_PREAMBLE_DETECTED | RADIOLIB_SX126X_IRQ_HEADER_VALID | RADIOLIB_SX126X_IRQ_HEADER_ERR | RADIOLIB_SX126X_IRQ_SYNC_WORD_VALID);
          _activityAt = 0; _headerSeen = false;
          return false;
        }
        return true;
      }
      if (preamble) {
        if (_activityAt == 0) _activityAt = now;
        if (now - _activityAt > _preambleMillis) {
          clearIrqFlags(RADIOLIB_SX126X_IRQ_PREAMBLE_DETECTED);
          _activityAt = 0;
          MESH_DEBUG_PRINTLN("Clearing preamble IRQ after %ums", _preambleMillis);

          return false;
        }
        return true;
      }
      _activityAt = 0; _headerSeen = false;
      return false;
    }

    void setPreambleMillis(uint32_t preambleMillis) {
      _preambleMillis = preambleMillis;
      MESH_DEBUG_PRINTLN("Set _preambleMillis=%u", _preambleMillis);
    }
    void setMaxPayloadMillis(uint32_t payloadMillis) {
      _maxPayloadMillis = payloadMillis;
      MESH_DEBUG_PRINTLN("Set _maxPayloadMillis=%u", _maxPayloadMillis);
    }

};
