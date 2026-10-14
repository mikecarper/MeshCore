#pragma once

#include <RadioLib.h>
#include <math.h>
#include "MeshCore.h"
#include "../MorseRx.h"

#define RH_RF95_MODEM_STATUS_CLEAR               0x10
#define RH_RF95_MODEM_STATUS_HEADER_INFO_VALID   0x08
#define RH_RF95_MODEM_STATUS_RX_ONGOING          0x04
#define RH_RF95_MODEM_STATUS_SIGNAL_SYNCHRONIZED 0x02
#define RH_RF95_MODEM_STATUS_SIGNAL_DETECTED     0x01

class CustomSX1276 : public SX1276 {
  public:
    CustomSX1276(Module *mod) : SX1276(mod) { }

    int16_t setProfileProtocol(uint8_t syncWord) {
      // Both wire protocols use CRC-on, explicit-header, normal-IQ LoRa.
      // Use RadioLib's setters so its CRC/header caches and IQ workaround
      // registers agree with the hardware before the next RX or TX.
      int16_t status = SX1276::setSyncWord(syncWord);
      if (status == RADIOLIB_ERR_NONE) status = SX1276::setCRC(true);
      if (status == RADIOLIB_ERR_NONE) status = SX1276::explicitHeader();
      if (status == RADIOLIB_ERR_NONE) status = SX1276::invertIQ(false);
      return status;
    }

    int16_t beginMorseReceive(float frequency_mhz) {
      int16_t status = SX1276::beginFSK(frequency_mhz,
          mesh::MorseRx::ReceiverBitRateKbps, 0.6f, 2.6f, 2, 16, false);
#ifdef SX127X_CURRENT_LIMIT
      if (status == RADIOLIB_ERR_NONE) status = setCurrentLimit(SX127X_CURRENT_LIMIT);
#endif
      // The API argument is the register encoding: zero means two samples,
      // not zero samples. Continuous direct RX has no preamble/sync gating.
      if (status == RADIOLIB_ERR_NONE) status = setRSSIConfig(0);
      return status;
    }

    float getMorseRssi() {
      // SX127x has no SPI command-status byte. Check modem and chip identity
      // instead of interpreting an absent/wrong-modem read as valid power.
      const uint8_t version = getChipVersion();
      if (getActiveModem() != RADIOLIB_SX127X_FSK_OOK
          || (version != RADIOLIB_SX1278_CHIP_VERSION
              && version != RADIOLIB_SX1278_CHIP_VERSION_ALT
              && version != RADIOLIB_SX1278_CHIP_VERSION_RFM9X)) return NAN;
      const int16_t raw = this->mod->SPIgetRegValue(RADIOLIB_SX127X_REG_RSSI_VALUE_FSK);
      return raw < 0 ? NAN : raw * -0.5f;
    }

    int16_t setMorsePaRamp() {
      // Slowest supported PA envelope (3400 us), to soften CW keying edges.
      // std_init() restores RadioLib's normal packet ramp after Morse.
      return this->mod->SPIsetRegValue(RADIOLIB_SX127X_REG_PA_RAMP, 0, 3, 0);
    }

  #ifdef RP2040_PLATFORM
    bool std_init(SPIClassRP2040* spi = NULL)
  #else
    bool std_init(SPIClass* spi = NULL)
  #endif
    {
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
      int status = begin(LORA_FREQ, LORA_BW, LORA_SF, cr, RADIOLIB_SX126X_SYNC_WORD_PRIVATE, LORA_TX_POWER, 16);
      // if radio init fails with -707/-706, try again with tcxo voltage set to 0.0f
      if (status != RADIOLIB_ERR_NONE) {
        mesh::usbLoggingPort().print("ERROR: radio init failed: ");
        mesh::usbLoggingPort().println(status);
        return false;  // fail
      }
  #ifdef SX127X_CURRENT_LIMIT
      setCurrentLimit(SX127X_CURRENT_LIMIT);
  #endif

  #if defined(SX127X_RXEN) || defined(SX127X_TXEN)
    #ifndef SX127X_RXEN
      #define SX127X_RXEN RADIOLIB_NC
    #endif
    #ifndef SX127X_TXEN
      #define SX127X_TXEN RADIOLIB_NC
    #endif
      setRfSwitchPins(SX127X_RXEN, SX127X_TXEN);
  #endif

      setCRC(1);

      return true;  // success
    }

    bool isReceiving() {
      return (getModemStatus() &
         (RH_RF95_MODEM_STATUS_SIGNAL_DETECTED
        | RH_RF95_MODEM_STATUS_SIGNAL_SYNCHRONIZED
        | RH_RF95_MODEM_STATUS_HEADER_INFO_VALID)) != 0;
    }

    int tryScanChannel(unsigned long timeout_ms, mesh::MainBoard& board) {
      // start CAD
      int16_t state = startChannelScan();
      RADIOLIB_ASSERT(state);

      // SX127x getChannelScanResult() reports FREE even before CAD completes.
      // DIO0 proves CAD_DONE; then read CAD_DETECTED from the actual IRQ flags,
      // including when completion/detection already happened before this poll.
      const uint32_t started = millis();
      uint32_t last_watchdog_service = started;
      for (;;) {
        if(this->mod->hal->digitalRead(this->mod->getIrq())) {
          return getChannelScanResult();
        }
        if(this->mod->hal->digitalRead(this->mod->getGpio())) {
          return(RADIOLIB_PREAMBLE_DETECTED);
        }
        const uint32_t now = millis();
        if(uint32_t(now - started) >= timeout_ms) {
          return(RADIOLIB_ERR_RX_TIMEOUT);
        }
        if(uint32_t(now - last_watchdog_service) >= 1000UL) {
          board.serviceWatchdog();
          last_watchdog_service = now;
        }
        this->mod->hal->yield();
      }
    }
};
