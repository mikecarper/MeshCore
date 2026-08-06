#pragma once

#include <RadioLib.h>
#include "MeshCore.h"

class CustomLR2021 : public LR2021 {
  bool _rx_boosted = false;
  uint32_t _rf_switch_pins[Module::RFSWITCH_MAX_PINS] = {};
  const Module::RfSwitchMode_t* _rf_switch_table = nullptr;

  void restoreRfSwitchTable() {
    if (_rf_switch_table != nullptr) {
      LR2021::setRfSwitchTable(_rf_switch_pins, _rf_switch_table);
    }
  }

  public:
    CustomLR2021(Module *mod) : LR2021(mod) { irqDioNum = LR2021_IRQ_DIO; }

    bool std_init(SPIClass* spi = NULL)
    {

  #ifdef LR2021_TCXO_VOLTAGE
      float tcxo = LR2021_TCXO_VOLTAGE;
  #else
      float tcxo = 1.6f;
  #endif

  #ifdef LORA_CR
      uint8_t cr = LORA_CR;
  #else
      uint8_t cr = 5;
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
      int status = begin(LORA_FREQ, LORA_BW, LORA_SF, cr, RADIOLIB_LR2021_LORA_SYNC_WORD_PRIVATE, LORA_TX_POWER, 16, tcxo);
      // if radio init fails with -707/-706, try again with tcxo voltage set to 0.0f
      if (status == RADIOLIB_ERR_SPI_CMD_FAILED || status == RADIOLIB_ERR_SPI_CMD_INVALID) {
        tcxo = 0.0f;
        status = begin(LORA_FREQ, LORA_BW, LORA_SF, cr, RADIOLIB_LR2021_LORA_SYNC_WORD_PRIVATE, LORA_TX_POWER, 16, tcxo);
      }
      if (status != RADIOLIB_ERR_NONE) {
        Serial.print("ERROR: radio init failed: ");
        Serial.println(status);
        return false;  // fail
      }

      setCRC(2);
      explicitHeader();

    #ifdef LR2021_RX_BOOSTED_GAIN
      setRxBoostedGainMode(LR2021_RX_BOOSTED_GAIN);
    #endif

      // LR2021::begin() performs a hardware reset. Preserve the board-specific
      // DIO/FEM routing across watchdog and SPI-timeout recovery resets.
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

    bool isChipBusy() {
      uint32_t busy = this->mod->getGpio();
      return busy != RADIOLIB_NC && this->mod->hal->digitalRead(busy);
    }

    bool isReceiving() {
      if (isChipBusy()) return false;
      uint32_t irq = getIrqStatus();
      bool detected = ((irq & RADIOLIB_LR2021_IRQ_SYNCWORD_VALID) || (irq & RADIOLIB_LR2021_IRQ_PREAMBLE_DETECTED));
      return detected;
    }

    uint8_t getSpreadingFactor() const { return spreadingFactor; }
};
