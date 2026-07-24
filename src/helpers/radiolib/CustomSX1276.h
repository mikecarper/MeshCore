#pragma once

#include <RadioLib.h>
#include "MeshCore.h"

#define RH_RF95_MODEM_STATUS_CLEAR               0x10
#define RH_RF95_MODEM_STATUS_HEADER_INFO_VALID   0x08
#define RH_RF95_MODEM_STATUS_RX_ONGOING          0x04
#define RH_RF95_MODEM_STATUS_SIGNAL_SYNCHRONIZED 0x02
#define RH_RF95_MODEM_STATUS_SIGNAL_DETECTED     0x01

class CustomSX1276 : public SX1276 {
  public:
    CustomSX1276(Module *mod) : SX1276(mod) { }

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
        Serial.print("ERROR: radio init failed: ");
        Serial.println(status);
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

      // SX127x getChannelScanResult() reports a free channel before CAD has
      // completed, so wait on its IRQ pins with the same bounded policy used
      // by the generic wrapper.
      const unsigned long started = millis();
      unsigned long last_watchdog_service = started;
      while(!this->mod->hal->digitalRead(this->mod->getIrq())) {
        this->mod->hal->yield();
        if(this->mod->hal->digitalRead(this->mod->getGpio())) {
          return(RADIOLIB_PREAMBLE_DETECTED);
        }
        const unsigned long now = millis();
        if(now - last_watchdog_service >= 1000UL) {
          board.serviceWatchdog();
          last_watchdog_service = now;
        }
        if(now - started >= timeout_ms) {
          return(RADIOLIB_ERR_RX_TIMEOUT);
        }
      }
      return(RADIOLIB_CHANNEL_FREE);
    }
};
