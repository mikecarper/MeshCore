#pragma once

#include <RadioLib.h>
#include "MeshCore.h"
#include "LR2021Band.h"
#include "../MorseRx.h"

#ifndef LR2021_TX_BUSY_TIMEOUT_MS
#define LR2021_TX_BUSY_TIMEOUT_MS 1000UL
#endif

// SIMO needs a fitted external inductor. Opt in only for verified board
// layouts; LR2021 also supports LDO-only designs without that circuitry.
#ifndef LR2021_USE_DCDC
#define LR2021_USE_DCDC 0
#endif

class CustomLR2021 : public LR2021 {
  uint32_t _preambleMillis = 66;
  uint32_t _maxPayloadMillis = 3934;
  uint32_t _activityAt = 0;
  bool _headerSeen = false;
  bool _rx_boosted = false;
  uint32_t _rf_switch_pins[Module::RFSWITCH_MAX_PINS] = {};
  const Module::RfSwitchMode_t* _rf_switch_table = nullptr;

  int16_t restoreRfSwitchTable() {
    if (!_rf_switch_table) return RADIOLIB_ERR_NONE;
    for (size_t i = 0; i < Module::RFSWITCH_MAX_PINS; ++i) {
      const uint32_t pin = _rf_switch_pins[i];
      if (pin == RADIOLIB_NC || !(pin & RFSWITCH_PIN_FLAG)) continue;
      const uint8_t dio = RADIOLIB_LRXXXX_DIOx_VAL(pin);
      if (dio >= 7) return RADIOLIB_ERR_UNKNOWN;
      uint8_t config = 0;
      for (size_t j = 0; j < 8 && _rf_switch_table[j].mode != LR2021::MODE_END_OF_TABLE; ++j) {
        const uint8_t mode = _rf_switch_table[j].mode;
        if (mode > 0 && mode <= 8
            && _rf_switch_table[j].values[i] == this->mod->hal->GpioLevelHigh)
          config |= 1UL << (mode - 1);
      }
      const uint8_t pull = dio == 0 ? RADIOLIB_LR2021_DIO_SLEEP_PULL_UP
                                   : RADIOLIB_LR2021_DIO_SLEEP_PULL_AUTO;
      int16_t status = setDioFunction(dio + 5, RADIOLIB_LR2021_DIO_FUNCTION_RF_SWITCH, pull);
      if (status == RADIOLIB_ERR_NONE) status = setDioRfSwitchConfig(dio + 5, config);
      if (status != RADIOLIB_ERR_NONE) return status;
    }
    return RADIOLIB_ERR_NONE;
  }

  uint8_t rfSwitchRxMode() const {
    return mesh::lr2021::isHighBand(freqMHz) ? LR2021::MODE_RX_HF : LR2021::MODE_RX;
  }

  uint8_t rfSwitchTxMode() const {
    return mesh::lr2021::isHighBand(freqMHz) ? LR2021::MODE_TX_HF : LR2021::MODE_TX;
  }

  public:
    CustomLR2021(Module *mod) : LR2021(mod) { irqDioNum = LR2021_IRQ_DIO; }

    int16_t setProfileProtocol(uint8_t syncWord) {
      int16_t status = LR2021::setSyncWord(syncWord);
      if (status == RADIOLIB_ERR_NONE) status = LR2021::setCRC(2);
      if (status == RADIOLIB_ERR_NONE) status = LR2021::explicitHeader();
      if (status == RADIOLIB_ERR_NONE) status = LR2021::invertIQ(false);
      return status;
    }

    int16_t beginMorseReceive(float frequency_mhz) {
      const uint8_t gain = mesh::lr2021::isHighBand(frequency_mhz) ? gainModeHf : gainModeLf;
      int16_t status = LR2021::beginGFSK(frequency_mhz,
          mesh::MorseRx::ReceiverBitRateKbps, 0.6f, 4.8f, 2, 16, tcxoVoltage);
      // Native modem initialization resets the regulator to LDO. Verified
      // SIMO boards must restore DC/DC before admitting RX after any reset.
    #if LR2021_USE_DCDC
      if (status == RADIOLIB_ERR_NONE) status = setRegulatorDCDC();
    #endif
      if (status == RADIOLIB_ERR_NONE) status = restoreRfSwitchTable();
      if (status == RADIOLIB_ERR_NONE) status = LR2021::setRxBoostedGainMode(gain);
      if (status == RADIOLIB_ERR_NONE) {
        uint8_t unused_sync = 0;
        status = LR2021::setSyncWord(&unused_sync, 0);
      }
      if (status == RADIOLIB_ERR_NONE) {
        preambleDetLength = 0;
        status = LR2021::setCRC(0);
      }
      // launchMode() retains this chip's band-aware RX RF switch selection.
      return status;
    }

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

    #if LR2021_USE_DCDC
      // Also reached by radioDeepInit() when restoring LoRa after Morse RX.
      // Check this before later setters can overwrite a regulator error.
      status = setRegulatorDCDC();
      if (status != RADIOLIB_ERR_NONE) return false;
    #endif

      status = setCRC(2);
      if (status != RADIOLIB_ERR_NONE) return false;
      status = explicitHeader();
      if (status != RADIOLIB_ERR_NONE) return false;

    #ifdef LR2021_RX_BOOSTED_GAIN
      setRxBoostedGainMode(LR2021_RX_BOOSTED_GAIN);
    #endif

      // begin() resets the radio, including its DIO-controlled RF routing.
      status = restoreRfSwitchTable();
      if (status != RADIOLIB_ERR_NONE) return false;

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

    int16_t transmitMorseCarrier() {
      // RadioLib's generic direct-TX path always selects MODE_TX. Use the
      // band-aware routing also used by our normal packet transmit path.
      this->mod->setRfSwitchState(rfSwitchTxMode());
      return this->setTxTestMode(RADIOLIB_LR2021_TX_TEST_MODE_CW);
    }

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
      // setRxPath requires standby, including retries after failed read cleanup.
      int16_t status = standby();
      if (status != RADIOLIB_ERR_NONE) return status;
      // Failed cleanup can leave bytes behind; no new RX window may inherit
      // the preceding FIFO even when the first discard attempt failed.
      status = clearRxFifo();
      if (status != RADIOLIB_ERR_NONE) return status;
      // include the PREAMBLE_DETECTED irq bit in reported flags
      return LR2021::startReceive(RADIOLIB_LR2021_RX_TIMEOUT_INF, RADIOLIB_IRQ_RX_DEFAULT_FLAGS | (1UL << RADIOLIB_IRQ_PREAMBLE_DETECTED), RADIOLIB_IRQ_RX_DEFAULT_MASK, 0);
    }

    bool isReceiving() {
      if (isChipBusy()) return false;
      uint32_t irq = getIrqStatus();
      bool preamble = irq & RADIOLIB_LR2021_IRQ_PREAMBLE_DETECTED;  // bit 5
      bool header   = irq & RADIOLIB_LR2021_IRQ_LORA_HEADER_VALID;  // bit 6
      uint32_t now  = millis();
      if (!header && _headerSeen) {
        // something cleared the header flag, reset our state.
        _activityAt = 0; _headerSeen = false;
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

    int16_t readData(uint8_t* data, size_t len) override {
      uint16_t fifo_level = 0;
      const int16_t status = getRxFifoLevel(&fifo_level);
      if (status != RADIOLIB_ERR_NONE) return status;
      const size_t expected = len != 0 ? len : getPacketLength();
      if (fifo_level != expected || expected == 0) {
        // RX_DONE with an empty FIFO, or overlapping continuous-RX packets,
        // must not turn repeated padding or mixed bytes into a mesh packet.
        // Consume the suspect FIFO so a subsequent clean frame can recover.
        const int16_t cleared = clearRxFifo();
        if (cleared != RADIOLIB_ERR_NONE) return cleared;
        return RADIOLIB_ERR_PACKET_TOO_SHORT;
      }
      return LR2021::readData(data, len);
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
