#pragma once

#include <RadioLib.h>
#include "MeshCore.h"
#include "RXPowerSaving.h"
#include "SX1262ProfileSwitchState.h"

#ifndef MC_SX1262_FAST_PROFILE_SWITCH
#define MC_SX1262_FAST_PROFILE_SWITCH 0
#endif

#ifndef SX126X_TX_BUSY_TIMEOUT_MS
#define SX126X_TX_BUSY_TIMEOUT_MS              1000UL
#endif

class CustomSX1262 : public SX1262 {
  uint32_t _preambleMillis = 66;
  uint32_t _maxPayloadMillis = 3934;
  uint32_t _activityAt = 0;
  bool _headerSeen = false;
  bool _rx_ps_rf_rx_disabled = false;
  uint32_t _rxDutyCycleTransitionUs = 1000;
  SX1262ProfileSwitchState _profileSwitch{MC_SX1262_FAST_PROFILE_SWITCH != 0};
  uint32_t _profileFastResumes = 0;
  bool _coldStandby = true;
  bool _tcxoWakePending = false;

  public:
    CustomSX1262(Module *mod) : SX1262(mod) { }

    void setProfileSwitchOptimization(bool enabled) {
      _profileSwitch.enabled = enabled;
      _profileSwitch.invalidate();
    }
    void beginProfileSwitch(bool continuousRx) { _profileSwitch.begin(continuousRx, standbyXOSC); }
    void endProfileSwitch(bool success) { _profileSwitch.end(success); }
    bool profileSwitchFailed() const { return _profileSwitch.failed(); }
    uint32_t getOptimizedProfileSwitches() const { return _profileFastResumes; }

    int16_t restoreTcxoAfterSleep() {
      if (!_tcxoWakePending) return RADIOLIB_ERR_NONE;
      // Both RC and warm policies showed a wake-only XOSC error on the WIO
      // module. Restore the existing DIO3 voltage/delay, never disable TCXO.
      // RadioLib clears XOSC_START_ERR here; do not hide unrelated faults.
      if (getDeviceErrors() & ~RADIOLIB_SX126X_XOSC_START_ERR) return RADIOLIB_ERR_SPI_CMD_FAILED;
      _tcxoWakePending = false; // setTCXO calls our explicit standby override
      const int16_t rc = SX1262::setTCXO(tcxoVoltage, tcxoDelay);
      if (rc != RADIOLIB_ERR_NONE) _tcxoWakePending = true;
      return rc;
    }

    int16_t standby() override {
      const bool alreadyAwake = !_coldStandby && _profileSwitch.active && _profileSwitch.rxValid;
      if (!alreadyAwake) _profileSwitch.invalidate();
      int16_t rc = alreadyAwake
          ? SX1262::standby(RADIOLIB_SX126X_STANDBY_XOSC, false)
          : _coldStandby ? SX1262::standby(RADIOLIB_SX126X_STANDBY_RC) : SX1262::standby();
      if (rc == RADIOLIB_ERR_NONE) rc = restoreTcxoAfterSleep();
      if (rc != RADIOLIB_ERR_NONE) _coldStandby = true;
      _profileSwitch.standbyResult(rc);
      return rc;
    }
    int16_t standby(uint8_t mode) override {
      _profileSwitch.invalidate();
      int16_t rc = SX1262::standby(mode);
      if (rc == RADIOLIB_ERR_NONE) rc = restoreTcxoAfterSleep();
      if (mode == RADIOLIB_SX126X_STANDBY_RC || rc != RADIOLIB_ERR_NONE) _coldStandby = true;
      _profileSwitch.standbyResult(rc);
      return rc;
    }
    // SetSleep is only valid from standby (SX1261/2 datasheet 13.1.1).
    // ResetAGC may call us directly from RX. Use RC for sleep entry/wake,
    // retaining the requested warm policy for subsequent established RX.
    int16_t sleep() override { return sleep(true); }
    int16_t sleep(bool retainConfig) {
      _profileSwitch.invalidate();
      _coldStandby = true;
      const int16_t rc = SX1262::standby(RADIOLIB_SX126X_STANDBY_RC);
      _tcxoWakePending = tcxoVoltage > 0.0f;
      return rc == RADIOLIB_ERR_NONE ? SX1262::sleep(retainConfig) : rc;
    }
    int16_t reset(bool verify = true) { _profileSwitch.invalidate(); _coldStandby = true; _tcxoWakePending = false; return SX1262::reset(verify); }
    int16_t stageMode(RadioModeType_t mode, RadioModeConfig_t* cfg) override {
      // All ordinary RX/TX staging rebuilds hardware state. In particular TX
      // changes IRQ mapping, packet length and buffer contents/base settings.
      _profileSwitch.invalidate();
      return SX1262::stageMode(mode, cfg);
    }
    int16_t setPreambleLength(size_t symbols) override {
      if (_profileSwitch.canResumeFast()) {
        // The owned hop will commit the complete packet tuple once, in RX
        // resume. A failed hop discards this capability before rollback.
        preambleLengthLoRa = symbols;
        return RADIOLIB_ERR_NONE;
      }
      return SX1262::setPreambleLength(symbols);
    }

    // Apply one complete LoRa modulation tuple instead of three separate
    // SetModulationParams commands. Keep RadioLib's caches and automatic LDRO
    // calculation consistent with its ordinary SF/BW/CR setters. Caller must
    // own a safe standby/reconfigure window, just as for those setters.
    int16_t setLoRaModulationParams(float bw, uint8_t sf, uint8_t cr) {
      if (sf < 5 || sf > 12) return RADIOLIB_ERR_INVALID_SPREADING_FACTOR;
      if (cr < 4 || cr > 8) return RADIOLIB_ERR_INVALID_CODING_RATE;
      if (!isfinite(bw)) return RADIOLIB_ERR_INVALID_BANDWIDTH;
      static constexpr float bandwidths[] = {7.8f, 10.4f, 15.6f, 20.8f, 31.25f,
                                             41.7f, 62.5f, 125.0f, 250.0f, 500.0f};
      static constexpr uint8_t codes[] = {
        RADIOLIB_SX126X_LORA_BW_7_8, RADIOLIB_SX126X_LORA_BW_10_4,
        RADIOLIB_SX126X_LORA_BW_15_6, RADIOLIB_SX126X_LORA_BW_20_8,
        RADIOLIB_SX126X_LORA_BW_31_25, RADIOLIB_SX126X_LORA_BW_41_7,
        RADIOLIB_SX126X_LORA_BW_62_5, RADIOLIB_SX126X_LORA_BW_125_0,
        RADIOLIB_SX126X_LORA_BW_250_0, RADIOLIB_SX126X_LORA_BW_500_0};
      size_t index = 0;
      while (index < sizeof(bandwidths) / sizeof(bandwidths[0])
          && fabsf(bw - bandwidths[index]) >= 0.01f) ++index;
      if (index == sizeof(bandwidths) / sizeof(bandwidths[0])) {
        return RADIOLIB_ERR_INVALID_BANDWIDTH;
      }
      if (getPacketType() != RADIOLIB_SX126X_PACKET_TYPE_LORA) {
        return RADIOLIB_ERR_WRONG_MODEM;
      }

      const auto oldSf = spreadingFactor;
      const auto oldBw = bandwidth;
      const auto oldBwKhz = bandwidthKhz;
      const auto oldCr = codingRate;
      const auto oldLdro = ldrOptimize;
      spreadingFactor = sf;
      bandwidth = codes[index];
      bandwidthKhz = bw;
      codingRate = cr - 4;  // ordinary LoRa CR, not long-interleaving encoding
      const int16_t state = setModulationParams(sf, bandwidth, codingRate, ldrOptimize);
      if (state != RADIOLIB_ERR_NONE) {
        // The wrapper restores the physical tuple after a failed command.
        // Do not publish a new software tuple when hardware success is unknown.
        spreadingFactor = oldSf;
        bandwidth = oldBw;
        bandwidthKhz = oldBwKhz;
        codingRate = oldCr;
        ldrOptimize = oldLdro;
      }
      return state;
    }

    // Apply the measured TCXO delay on every initialization, including recovery.
    int16_t begin(float freq = 434.0, float bw = 125.0, uint8_t sf = 9, uint8_t cr = 7,
                  uint8_t syncWord = RADIOLIB_SX126X_SYNC_WORD_PRIVATE, int8_t power = 10,
                  uint16_t preambleLength = 8, float tcxoVoltage = 1.6,
                  bool useRegulatorLDO = false) {
      _profileSwitch.invalidate();
      // A held dual-profile oscillator must not be selected during reset or
      // setup, before DIO3/TCXO configuration has been restored. Restore the
      // software standby policy on both success and failure; only subsequent
      // standby/RX commands can actually turn the oscillator back on.
      const bool savedStandbyXosc = standbyXOSC;
      _coldStandby = true;
      _tcxoWakePending = false;
      standbyXOSC = false;
      int16_t state = SX1262::begin(freq, bw, sf, cr, syncWord, power, preambleLength,
                                    tcxoVoltage, useRegulatorLDO);
      if (state == RADIOLIB_ERR_NONE) state = applyMeshCoreTcxoDelay();
      standbyXOSC = savedStandbyXosc;
      return state;
    }

    // Read tcxoVoltage back from the chip object rather than from the argument:
    // begin() zeroes it when it falls back to an XTAL, and re-asserting a TCXO
    // supply on DIO3 for a board that has none would be worse than a long delay.
    int16_t applyMeshCoreTcxoDelay() {
      if (tcxoVoltage <= 0.0f) return RADIOLIB_ERR_NONE;
      int16_t state = setTCXO(tcxoVoltage, MC_TCXO_DELAY_US);
      RADIOLIB_ASSERT(state);
      state = calibrate(RADIOLIB_SX126X_CALIBRATE_ALL);
      RADIOLIB_ASSERT(state);
      delay(50);
      return RADIOLIB_ERR_NONE;
    }

    // MeshCore keeps the SX1262 in LoRa mode. Use RadioLib's cached modem
    // parameters instead of issuing GetPacketType while RX duty cycling may
    // have the chip asleep. A failed live query otherwise becomes an encoded
    // negative error in the unsigned time-on-air result and can stall TX.
    RadioLibTime_t getTimeOnAir(size_t len) override {
      uint8_t cr = this->codingRate;
      // RadioLib stores ordinary CR 4/5 through 4/8 as 0-4. Long-interleaving
      // CR values 0-4 and 5-7 map to the same time-on-air denominators.
      if (cr < 5) {
        cr += 4;
      } else if (cr == 7) {
        cr += 1;
      }

      DataRate_t data_rate = {};
      data_rate.lora.spreadingFactor = this->spreadingFactor;
      data_rate.lora.bandwidth = this->bandwidthKhz;
      data_rate.lora.codingRate = cr;

      PacketConfig_t packet_config = {};
      packet_config.lora.preambleLength = this->preambleLengthLoRa;
      packet_config.lora.crcEnabled = (bool)this->crcTypeLoRa;
      packet_config.lora.implicitHeader =
          this->headerType == RADIOLIB_SX126X_LORA_HEADER_IMPLICIT;
      packet_config.lora.ldrOptimize = (bool)this->ldrOptimize;

      return SX126x::calculateTimeOnAir(
          ModemType_t::RADIOLIB_MODEM_LORA, data_rate, packet_config, len);
    }

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
      if (status != RADIOLIB_ERR_NONE) {
#if defined(SX126X_ALLOW_RECOVERABLE_INIT_STATUS) && SX126X_ALLOW_RECOVERABLE_INIT_STATUS
        // Some SX1262 modules report a transient command status after the
        // chip has already answered its identity probe. Continue configuring
        // those devices; an absent chip or a hard command failure is fatal.
        if (status == RADIOLIB_ERR_CHIP_NOT_FOUND
            || status == RADIOLIB_ERR_SPI_CMD_FAILED) {
          mesh::usbLoggingPort().print("ERROR: radio init failed: ");
          mesh::usbLoggingPort().println(status);
          return false;
        }
        mesh::usbLoggingPort().print("WARN: recoverable radio init status: ");
        mesh::usbLoggingPort().println(status);
#else
        mesh::usbLoggingPort().print("ERROR: radio init failed: ");
        mesh::usbLoggingPort().println(status);
        return false;  // fail
#endif
      }

      // MeshCore configures the DIO3 TCXO delay and the RX duty-cycle
      // command adds another 1 ms for sleep/wake transitions. If begin()
      // fell back to a crystal, only the fixed 1 ms transition remains. Use
      // RadioLib's resolved oscillator mode: it only falls back after reading
      // the SX126x XOSC_START_ERR device flag, not for an arbitrary SPI error.
      _rxDutyCycleTransitionUs = this->tcxoDelay + 1000UL;
    
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

    int16_t startReceiveDutyCycle(uint32_t rxPeriod, uint32_t sleepPeriod,
                                  RadioLibIrqFlags_t irqFlags = RADIOLIB_IRQ_RX_DEFAULT_FLAGS,
                                  RadioLibIrqFlags_t irqMask = RADIOLIB_IRQ_RX_DEFAULT_MASK) {
      _profileSwitch.invalidate();
      _coldStandby = true;
      int16_t state = SX1262::startReceiveDutyCycle(rxPeriod, sleepPeriod, irqFlags, irqMask);
      if (state == RADIOLIB_ERR_NONE && !_rx_ps_rf_rx_disabled) {
        // RadioLib stages RX duty-cycle through standby, which leaves a
        // host-controlled RXEN switch in IDLE. Keep the receive path enabled
        // while the SX1262 alternates between its RX and sleep windows.
        this->mod->setRfSwitchState(Module::MODE_RX);
      }
      return state;
    }

    void setRxPowerSavingRfRxDisabled(bool disabled) {
      _rx_ps_rf_rx_disabled = disabled;
    }

    bool isRxPowerSavingRfRxDisabled() const {
      return _rx_ps_rf_rx_disabled;
    }

    // Select whether a host-controlled RXEN pin is asserted in receive mode.
    // Some FEM modules (notably the LilyGo T-Beam 1W XY16P35) expose their
    // external LNA supply on RXEN while DIO2 independently selects the RF
    // switch.  Swapping RadioLib's table keeps the requested state intact
    // across RX, CAD, TX, standby, and duty-cycle transitions; a one-shot
    // digitalWrite() would be overwritten on the next mode change.
    bool setExternalRxLnaEnabled(bool enabled) {
    #if defined(SX126X_RXEN)
      #if defined(SX126X_TXEN)
        static constexpr uint32_t tx_en = SX126X_TXEN;
      #else
        static constexpr uint32_t tx_en = RADIOLIB_NC;
      #endif

      static const uint32_t pins[Module::RFSWITCH_MAX_PINS] = {
        SX126X_RXEN, tx_en, RADIOLIB_NC, RADIOLIB_NC, RADIOLIB_NC,
      };
      static const Module::RfSwitchMode_t lna_disabled_table[] = {
        { Module::MODE_IDLE, { LOW, LOW } },
        { Module::MODE_RX,   { LOW, LOW } },
        { Module::MODE_TX,   { LOW, HIGH } },
        END_OF_MODE_TABLE,
      };

      if (enabled) {
        // Restore RadioLib's standard RXEN/TXEN behavior exactly.
        setRfSwitchPins(SX126X_RXEN, tx_en);
      } else {
        this->mod->setRfSwitchTable(pins, lna_disabled_table);
      }
      return true;
    #else
      (void)enabled;
      return false;
    #endif
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

      const RadioLibTime_t started = this->mod->hal->millis();
      while (isChipBusy()) {
        this->mod->hal->yield();
        if (this->mod->hal->millis() - started >= SX126X_TX_BUSY_TIMEOUT_MS) {
          this->stagedMode = RADIOLIB_RADIO_MODE_NONE;
          return RADIOLIB_ERR_SPI_CMD_TIMEOUT;
        }
      }

      this->stagedMode = RADIOLIB_RADIO_MODE_NONE;
      return RADIOLIB_ERR_NONE;
    }

    int16_t startReceive() override {
      if (_profileSwitch.canResumeFast()) {
        // Existing continuous RX already established buffer bases and the
        // MeshCore IRQ map (including preamble detection). No TX/CAD/sleep/
        // reset/staging may intervene without revoking that context.
        _profileSwitch.rxValid = false;  // fail closed on any command error
        int16_t rc = clearIrqStatus();
        if (rc == RADIOLIB_ERR_NONE) {
          rc = setPacketParams(preambleLengthLoRa, crcTypeLoRa, implicitLen,
                               headerType, invertIQEnabled);
        }
        if (rc == RADIOLIB_ERR_NONE) {
          rxTimeout = RADIOLIB_SX126X_RX_TIMEOUT_INF;
          getMod()->setRfSwitchState(Module::MODE_RX);
          rc = setRx(rxTimeout);  // original BUSY waits and status checks
        }
        stagedMode = RADIOLIB_RADIO_MODE_NONE;
        if (rc == RADIOLIB_ERR_NONE) ++_profileFastResumes;
        _coldStandby = rc != RADIOLIB_ERR_NONE;
        _profileSwitch.rxResult(rc, true);
        return rc;
      }
      // Make preamble detection visible to CAD while retaining RadioLib's
      // normal RX-complete and error events.
      const int16_t rc = SX1262::startReceive(
          RADIOLIB_SX126X_RX_TIMEOUT_INF,
          RADIOLIB_IRQ_RX_DEFAULT_FLAGS | (1UL << RADIOLIB_IRQ_PREAMBLE_DETECTED),
          RADIOLIB_IRQ_RX_DEFAULT_MASK, 0);
      const bool reusable = rc == RADIOLIB_ERR_NONE && _profileSwitch.enabled
          && getPacketType() == RADIOLIB_SX126X_PACKET_TYPE_LORA;
      _coldStandby = rc != RADIOLIB_ERR_NONE;
      _profileSwitch.rxResult(rc, reusable);
      return rc;
    }

    bool isReceiving() {
      if (isChipBusy()) return false;   // asleep, cannot be mid-receive

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
        // Another path consumed the header IRQ; reset only our local timer.
        _activityAt = 0;
        _headerSeen = false;
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

    bool canUseRxPowerSavingDutyCycle(uint32_t rx_us, uint32_t sleep_us) const {
      return !rxPowerSavingUsesContinuousFallback(rx_us, sleep_us)
          && canStartRxPowerSavingDutyCycle(
              rx_us, sleep_us, _rxDutyCycleTransitionUs);
    }

    void setPreambleMillis(uint32_t preambleMillis) {
      _preambleMillis = preambleMillis;
      MESH_DEBUG_PRINTLN("Set _preambleMillis=%u", _preambleMillis);
    }
    void setMaxPayloadMillis(uint32_t payloadMillis) {
      _maxPayloadMillis = payloadMillis;
      MESH_DEBUG_PRINTLN("Set _maxPayloadMillis=%u", _maxPayloadMillis);
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
