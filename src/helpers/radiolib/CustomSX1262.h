#pragma once

#include <RadioLib.h>
#include "MeshCore.h"

#define SX126X_IRQ_HEADER_VALID                0b0000010000  //  4     4     valid LoRa header received
#define SX126X_IRQ_PREAMBLE_DETECTED           0x04

#ifndef SX126X_TX_BUSY_TIMEOUT_MS
#define SX126X_TX_BUSY_TIMEOUT_MS              1000UL
#endif

class CustomSX1262 : public SX1262 {
  public:
    CustomSX1262(Module *mod) : SX1262(mod) { }

  #ifdef RP2040_PLATFORM
    bool std_init(SPIClassRP2040* spi = NULL)
  #else
    bool std_init(SPIClass* spi = NULL)
  #endif
    {
  #ifdef SX126X_DIO3_TCXO_VOLTAGE
      float tcxo = SX126X_DIO3_TCXO_VOLTAGE;
  #else
      float tcxo = 1.6f;
  #endif

  #ifdef LORA_CR
      uint8_t cr = LORA_CR;
  #else
      uint8_t cr = 5;
  #endif

  #ifdef SX126X_USE_REGULATOR_LDO
      constexpr bool useRegulatorLDO = SX126X_USE_REGULATOR_LDO;
  #else
      constexpr bool useRegulatorLDO = false;
  #endif

      MESH_DEBUG_PRINTLN("SX1262 regulator requested: %s", useRegulatorLDO ? "LDO" : "DC-DC");

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
      int status = begin(LORA_FREQ, LORA_BW, LORA_SF, cr, RADIOLIB_SX126X_SYNC_WORD_PRIVATE, LORA_TX_POWER, 16, tcxo, useRegulatorLDO);
      // if radio init fails with -707/-706, try again with tcxo voltage set to 0.0f
      if (status == RADIOLIB_ERR_SPI_CMD_FAILED || status == RADIOLIB_ERR_SPI_CMD_INVALID) {
        MESH_DEBUG_PRINTLN("SX1262 init failed with error %d, retrying with TCXO at 0.0V", status);
        tcxo = 0.0f;
        status = begin(LORA_FREQ, LORA_BW, LORA_SF, cr, RADIOLIB_SX126X_SYNC_WORD_PRIVATE, LORA_TX_POWER, 16, tcxo, useRegulatorLDO);
      }
      if (status != RADIOLIB_ERR_NONE) {
        Serial.print("ERROR: radio init failed: ");
        Serial.println(status);
        return false;  // fail
      }
    
      setCRC(1);
  
  #ifdef SX126X_CURRENT_LIMIT
      setCurrentLimit(SX126X_CURRENT_LIMIT);
  #endif
  #ifdef SX126X_DIO2_AS_RF_SWITCH
      setDio2AsRfSwitch(SX126X_DIO2_AS_RF_SWITCH);
  #endif
  #ifdef SX126X_RX_BOOSTED_GAIN
      setRxBoostedGainMode(SX126X_RX_BOOSTED_GAIN);
  #endif
  #if defined(SX126X_RXEN) || defined(SX126X_TXEN)
    #ifndef SX126X_RXEN
      #define SX126X_RXEN RADIOLIB_NC
    #endif
    #ifndef SX126X_TXEN
      #define SX126X_TXEN RADIOLIB_NC
    #endif
      setRfSwitchPins(SX126X_RXEN, SX126X_TXEN);
  #endif 

  // for improved RX with Heltec v4
  #ifdef SX126X_REGISTER_PATCH
    uint8_t r_data = 0;
    readRegister(0x8B5, &r_data, 1);
    r_data |= 0x01;
    writeRegister(0x8B5, &r_data, 1);
  #endif

      MESH_DEBUG_PRINTLN("SX1262 status=0x%02X device_errors=0x%04X", getStatus(), getDeviceErrors());

      return true;  // success
    }

    // BUSY high means the chip is asleep (RX duty-cycle sleep window) or mid
    // command; any SPI access would stall until the chip's next listen window.
    bool isChipBusy() {
      uint32_t busy = this->mod->getGpio();
      return busy != RADIOLIB_NC && this->mod->hal->digitalRead(busy);
    }

    // RadioLib waits without a deadline for BUSY to fall after SetTx. A radio
    // fault there blocks the entire main loop until the MCU watchdog reboots
    // the node. Keep the normal launch behavior, but return the same timeout
    // used by bounded RadioLib SPI waits so the wrapper can reset the radio.
    int16_t launchMode() override {
      if (this->stagedMode != RADIOLIB_RADIO_MODE_TX) {
        return SX1262::launchMode();
      }

      this->mod->setRfSwitchState(this->txMode);
      int16_t state = this->setTx(RADIOLIB_SX126X_TX_TIMEOUT_NONE);
      if (state != RADIOLIB_ERR_NONE) {
        this->stagedMode = RADIOLIB_RADIO_MODE_NONE;
        return state;
      }

      const unsigned long started = millis();
      while (isChipBusy()) {
        yield();
        if (millis() - started >= SX126X_TX_BUSY_TIMEOUT_MS) {
          this->stagedMode = RADIOLIB_RADIO_MODE_NONE;
          return RADIOLIB_ERR_SPI_CMD_TIMEOUT;
        }
      }

      this->stagedMode = RADIOLIB_RADIO_MODE_NONE;
      return RADIOLIB_ERR_NONE;
    }

    bool isReceiving() {
      if (isChipBusy()) return false;   // asleep, cannot be mid-receive

      uint16_t irq = getIrqFlags();
      bool detected = (irq & SX126X_IRQ_HEADER_VALID) || (irq & SX126X_IRQ_PREAMBLE_DETECTED);
      return detected;
    }

    // Port of Semtech's sx126x_stop_rtc() (same registers as RadioLib's
    // fixImplicitTimeout / datasheet errata 15.3): after duty-cycle RX ends via
    // RxDone or SetStandby, the internal RTC keeps running and its pending
    // event can silently knock a subsequently started RX back to standby with
    // no IRQ, leaving the node deaf. Must be called before re-arming RX.
    int16_t stopRTC() {
      uint8_t rtcStop = 0x00;
      int16_t state = writeRegister(RADIOLIB_SX126X_REG_RTC_CTRL, &rtcStop, 1);
      RADIOLIB_ASSERT(state);

      uint8_t rtcEvent = 0;
      state = readRegister(RADIOLIB_SX126X_REG_EVENT_MASK, &rtcEvent, 1);
      RADIOLIB_ASSERT(state);

      rtcEvent |= 0x02;   // clear the RX timeout event
      return writeRegister(RADIOLIB_SX126X_REG_EVENT_MASK, &rtcEvent, 1);
    }

    bool getRxBoostedGainMode() {
      uint8_t rxGain = 0;
      readRegister(RADIOLIB_SX126X_REG_RX_GAIN, &rxGain, 1);
      return (rxGain == RADIOLIB_SX126X_RX_GAIN_BOOSTED);
    }
};
