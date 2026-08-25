#pragma once

#include <RadioLib.h>
#include "MeshCore.h"
#include "LR2021Band.h"

#ifndef LR2021_TX_BUSY_TIMEOUT_MS
#define LR2021_TX_BUSY_TIMEOUT_MS 1000UL
#endif

class CustomLR2021 : public LR2021 {
  uint32_t _preambleMillis = 66;
  uint32_t _maxPayloadMillis = 3934;
  uint32_t _activityAt = 0;
  bool _headerSeen = false;
  bool _rx_boosted = false;
  uint32_t _rf_switch_pins[Module::RFSWITCH_MAX_PINS] = {};
  const Module::RfSwitchMode_t* _rf_switch_table = nullptr;

  void restoreRfSwitchTable() {
    if (_rf_switch_table != nullptr) {
      LR2021::setRfSwitchTable(_rf_switch_pins, _rf_switch_table);
    }
  }

  uint8_t rfSwitchRxMode() const {
    return mesh::lr2021::isHighBand(freqMHz) ? LR2021::MODE_RX_HF : LR2021::MODE_RX;
  }

  uint8_t rfSwitchTxMode() const {
    return mesh::lr2021::isHighBand(freqMHz) ? LR2021::MODE_TX_HF : LR2021::MODE_TX;
  }

  public:
    CustomLR2021(Module *mod) : LR2021(mod) { irqDioNum = LR2021_IRQ_DIO; }

    bool std_init(SPIClass* spi = NULL) {
  #ifdef LR2021_TCXO_VOLTAGE
      const float tcxo = LR2021_TCXO_VOLTAGE;
  #else
      const float tcxo = 1.6f;
  #endif

  #ifdef LORA_CR
      const uint8_t cr = LORA_CR;
  #else
      const uint8_t cr = 5;
  #endif

  #if defined(P_LORA_SCLK)
    #ifdef NRF52_PLATFORM
      if (spi) { spi->setPins(P_LORA_MISO, P_LORA_SCLK, P_LORA_MOSI); spi->begin(); }
    #elif defined(RP2040_PLATFORM)
      if (spi) {
        spi->setMISO(P_LORA_MISO);
        //spi->setCS(P_LORA_NSS); // Setting CS results in freeze
        spi->setSCK(P_LORA_SCLK);
        spi->setMOSI(P_LORA_MOSI);
        spi->begin();
      }
    #else
      if (spi) spi->begin(P_LORA_SCLK, P_LORA_MISO, P_LORA_MOSI);
    #endif
  #endif
      int16_t status = begin(LORA_FREQ, LORA_BW, LORA_SF, cr,
                             RADIOLIB_LR2021_LORA_SYNC_WORD_PRIVATE,
                             LORA_TX_POWER, 16, tcxo);
      if (status != RADIOLIB_ERR_NONE) {
        mesh::usbLoggingPort().print("ERROR: radio init failed: ");
        mesh::usbLoggingPort().println(status);
        return false;  // fail
      }

      status = setCRC(2);
      if (status != RADIOLIB_ERR_NONE) return false;
      status = explicitHeader();
      if (status != RADIOLIB_ERR_NONE) return false;

    #ifdef LR2021_RX_BOOSTED_GAIN
      setRxBoostedGainMode(LR2021_RX_BOOSTED_GAIN);
    #endif

      // begin() resets the radio, including its DIO-controlled RF routing.
      restoreRfSwitchTable();

      return true;  // success
    }

    void setRfSwitchTable(const uint32_t (&pins)[Module::RFSWITCH_MAX_PINS],
                          const Module::RfSwitchMode_t table[]) {
      for (size_t i = 0; i < Module::RFSWITCH_MAX_PINS; i++) {
        _rf_switch_pins[i] = pins[i];
      }
      _rf_switch_table = table;
      LR2021::setRfSwitchTable(pins, table);
    }

    int16_t setSideDetector(const LR2021LoRaSideDetector_t* cfg, size_t numDetectors) {
      if (numDetectors > 0 && cfg == nullptr) return RADIOLIB_ERR_INVALID_SIDE_DETECT;
      if (numDetectors == 0) {
        // RadioLib 7.7.1 returns success without issuing this command for a
        // zero-length list. The LR2021 command encodes detector count in its
        // payload length, so the empty command is the actual disable operation.
        return setLoRaSideDetConfig(nullptr, 0);
      }
      return LR2021::setSideDetector(cfg, numDetectors);
    }

    float getFreqMHz() const { return freqMHz; }

    int16_t setRxBoostedGainMode(uint8_t level) {
      int16_t status = LR2021::setRxBoostedGainMode(level);
      if (status == RADIOLIB_ERR_NONE) _rx_boosted = level != 0;
      return status;
    }

    bool getRxBoostedGainMode() const { return _rx_boosted; }
    float getBandwidthKhz() const { return bandwidthKhz; }

    int16_t launchMode() override {
      const bool transmitting = this->stagedMode == RADIOLIB_RADIO_MODE_TX;
      int16_t state;
      if (this->stagedMode == RADIOLIB_RADIO_MODE_RX) {
        this->mod->setRfSwitchState(rfSwitchRxMode());
        state = this->setRx(this->rxTimeout);
      } else if (transmitting) {
        this->mod->setRfSwitchState(rfSwitchTxMode());
        state = this->setTx(RADIOLIB_LR2021_TX_TIMEOUT_NONE);
      } else {
        return RADIOLIB_ERR_UNSUPPORTED;
      }

      if (state != RADIOLIB_ERR_NONE) {
        this->stagedMode = RADIOLIB_RADIO_MODE_NONE;
        return state;
      }

      if (transmitting) {
        const RadioLibTime_t started = this->mod->hal->millis();
        while (this->mod->hal->digitalRead(this->mod->getGpio())) {
          this->mod->hal->yield();
          if (this->mod->hal->millis() - started >= LR2021_TX_BUSY_TIMEOUT_MS) {
            this->stagedMode = RADIOLIB_RADIO_MODE_NONE;
            return RADIOLIB_ERR_SPI_CMD_TIMEOUT;
          }
        }
      }

      this->stagedMode = RADIOLIB_RADIO_MODE_NONE;
      return RADIOLIB_ERR_NONE;
    }

    bool isChipBusy() {
      uint32_t busy = this->mod->getGpio();
      return busy != RADIOLIB_NC && this->mod->hal->digitalRead(busy);
    }

    int16_t startReceive() override {
      // include the PREAMBLE_DETECTED irq bit in reported flags
      return LR2021::startReceive(RADIOLIB_LR2021_RX_TIMEOUT_INF, RADIOLIB_IRQ_RX_DEFAULT_FLAGS | (1UL << RADIOLIB_LR2021_IRQ_PREAMBLE_DETECTED), RADIOLIB_IRQ_RX_DEFAULT_MASK, 0);
    }

    bool isReceiving() {
      if (isChipBusy()) return false;
      uint32_t irq = getIrqStatus();
      bool preamble = irq & RADIOLIB_LR2021_IRQ_PREAMBLE_DETECTED;  // bit 5
      bool header   = irq & RADIOLIB_LR2021_IRQ_LORA_HEADER_VALID;  // bit 6
      bool hdrErr   = irq & RADIOLIB_LR2021_IRQ_LORA_HDR_CRC_ERROR; // bit 9
      uint32_t now  = millis();
      if (hdrErr) {
        clearIrqFlags(RADIOLIB_LR2021_IRQ_PREAMBLE_DETECTED | RADIOLIB_LR2021_IRQ_LORA_HEADER_VALID | RADIOLIB_LR2021_IRQ_LORA_HDR_CRC_ERROR);
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
          clearIrqFlags(RADIOLIB_LR2021_IRQ_PREAMBLE_DETECTED | RADIOLIB_LR2021_IRQ_LORA_HEADER_VALID | RADIOLIB_LR2021_IRQ_LORA_HDR_CRC_ERROR);
          _activityAt = 0; _headerSeen = false;
          return false;
        }
        return true;
      }
      if (preamble) {
        if (_activityAt == 0) _activityAt = now;
        if (now - _activityAt > _preambleMillis) {
          clearIrqFlags(RADIOLIB_LR2021_IRQ_PREAMBLE_DETECTED);
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


    uint8_t getSpreadingFactor() const { return spreadingFactor; }
};
