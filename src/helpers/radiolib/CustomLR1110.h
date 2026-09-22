#pragma once

#include <RadioLib.h>
#include "MeshCore.h"
#include "RXPowerSaving.h"
#include "LR1110RxRecovery.h"
#include "LR1110ProfileSwitchState.h"

#ifndef MC_LR1110_FAST_PROFILE_SWITCH
// All MeshCore LR1110 targets use the checked FS-mode continuous-RX retune.
// Boards can opt out if hardware validation finds an incompatibility.
#define MC_LR1110_FAST_PROFILE_SWITCH 1
#endif

#ifndef LR11X0_TX_BUSY_TIMEOUT_MS
#define LR11X0_TX_BUSY_TIMEOUT_MS 1000UL
#endif

class CustomLR1110 : public LR1110 {
  uint32_t _preambleMillis = 66;
  uint32_t _maxPayloadMillis = 3934;
  uint32_t _activityAt = 0;
  bool _headerSeen = false;
  bool _rx_boosted = false;
  LR1110ProfileSwitchState _profileSwitch{MC_LR1110_FAST_PROFILE_SWITCH != 0};
  bool _profileStandbyWarm = false;
  bool _profilePacketDirty = false;
  uint32_t _profileFastResumes = 0;

  public:
    CustomLR1110(Module *mod) : LR1110(mod) { }

    void setProfileSwitchOptimization(bool enabled) {
      _profileSwitch.enabled = enabled;
      _profileSwitch.invalidate();
      _profilePacketDirty = false;
    }
    void setProfileStandbyWarm(bool enabled) {
      _profileStandbyWarm = enabled;
      if (!enabled) _profileSwitch.invalidate();
    }
    void beginProfileSwitch(bool continuousRx) {
      _profileSwitch.begin(continuousRx, _profileStandbyWarm);
    }
    void endProfileSwitch(bool success) { _profileSwitch.end(success); }
    bool profileSwitchFailed() const { return _profileSwitch.failed(); }
    uint32_t getOptimizedProfileSwitches() const { return _profileFastResumes; }
#ifdef MC_LR1110_HIL
    uint64_t fastClearCycles = 0, fastPacketCycles = 0, fastSetRxCycles = 0;
    uint8_t hilFastClearMode = 2;  // 0=always, 1=never, 2=checked (production)
    bool hilInjectRxTimeout = false;
    bool hilUseFsOnRetune = true;
    uint32_t hilPendingIrqSnapshots = 0;
#endif

    // Outside RX, no new packet IRQ can arise. Check the pending bits after
    // leaving RX and clear them only when present. RadioLib's plain
    // getIrqStatus() discards SPI errors, so use a checked transfer here.
    int16_t snapshotIrqAfterRxExit(uint32_t* irq) {
      uint8_t reply[6] = {};
      auto& statusWidth = mod->spiConfig.widths[RADIOLIB_MODULE_SPI_WIDTH_STATUS];
      const auto savedWidth = statusWidth;
      statusWidth = Module::BITS_0;
      const int16_t rc = mod->SPItransferStream(nullptr, 0, false, nullptr,
          reply, sizeof(reply), true);
      statusWidth = savedWidth;
      if (rc == RADIOLIB_ERR_NONE) {
        *irq = (uint32_t(reply[2]) << 24) | (uint32_t(reply[3]) << 16)
            | (uint32_t(reply[4]) << 8) | uint32_t(reply[5]);
      }
      return rc;
    }

    int16_t standby() override {
      const bool warm = _profileSwitch.active && _profileSwitch.rxValid;
      if (!warm) _profileSwitch.invalidate();
      // RadioLib 7.7.1 defines STANDBY_XOSC as 0, the same as STANDBY_RC.
      // The LR1110 command's XOSC selector is 1 (Semtech UM §2.1.1).
      bool useFs = warm;
#ifdef MC_LR1110_HIL
      useFs = useFs && hilUseFsOnRetune;
#endif
      int16_t rc;
      if (useFs) {
        mod->setRfSwitchState(Module::MODE_IDLE);
        rc = setFs();
      } else if (warm) {
        rc = LR1110::standby(0x01, false);
      } else {
        rc = LR1110::standby();
      }
      _profileSwitch.standbyResult(rc);
      return rc;
    }
    int16_t standby(uint8_t mode) override {
      _profileSwitch.invalidate();
      return LR1110::standby(mode);
    }
    int16_t sleep() override {
      _profileSwitch.invalidate();
      return LR1110::sleep();
    }
    int16_t reset() {
      _profileSwitch.invalidate();
      return LR1110::reset();
    }
    int16_t stageMode(RadioModeType_t mode, RadioModeConfig_t* cfg) override {
      _profileSwitch.invalidate();
      return LR1110::stageMode(mode, cfg);
    }
    int16_t setPreambleLength(size_t symbols) override {
      if (_profileSwitch.canResumeFast()) {
        // Keep the acknowledged RX packet tuple until its preamble changes.
        if (preambleLengthLoRa != symbols) _profilePacketDirty = true;
        preambleLengthLoRa = symbols;
        return RADIOLIB_ERR_NONE;
      }
      _profileSwitch.invalidate();
      _profilePacketDirty = false;
      return LR1110::setPreambleLength(symbols);
    }
    int16_t setFrequency(float freq) {
      if (_profileSwitch.canResumeFast() && freq == freqMHz) return RADIOLIB_ERR_NONE;
      if (!_profileSwitch.canResumeFast()) _profileSwitch.invalidate();
      const int16_t rc = LR1110::setFrequency(freq);
      if (rc != RADIOLIB_ERR_NONE) _profileSwitch.invalidate();
      return rc;
    }
    int16_t setBandwidth(float bw, bool high = false) {
      _profileSwitch.modulationValid = false;
      return LR1110::setBandwidth(bw, high);
    }
    int16_t setSpreadingFactor(uint8_t sf, bool legacy = false) {
      _profileSwitch.modulationValid = false;
      return LR1110::setSpreadingFactor(sf, legacy);
    }
    int16_t setCodingRate(uint8_t cr, bool longInterleave = false) {
      _profileSwitch.modulationValid = false;
      return LR1110::setCodingRate(cr, longInterleave);
    }

    // A single acknowledged SetModulationParams replaces three RadioLib
    // setters. Keep RadioLib's cached tuple and automatic LDRO calculation.
    int16_t setLoRaModulationParams(float bw, uint8_t sf, uint8_t cr) {
      if (sf < 5 || sf > 12) return RADIOLIB_ERR_INVALID_SPREADING_FACTOR;
      if (cr < 4 || cr > 8) return RADIOLIB_ERR_INVALID_CODING_RATE;
      uint8_t bwCode;
      if (fabsf(bw - 62.5f) < 0.01f) bwCode = RADIOLIB_LR11X0_LORA_BW_62_5;
      else if (fabsf(bw - 125.0f) < 0.01f) bwCode = RADIOLIB_LR11X0_LORA_BW_125_0;
      else if (fabsf(bw - 250.0f) < 0.01f) bwCode = RADIOLIB_LR11X0_LORA_BW_250_0;
      else if (fabsf(bw - 500.0f) < 0.01f) bwCode = RADIOLIB_LR11X0_LORA_BW_500_0;
      else return RADIOLIB_ERR_INVALID_BANDWIDTH;
      if (!_profileSwitch.canResumeFast()) {
        uint8_t type = RADIOLIB_LR11X0_PACKET_TYPE_NONE;
        const int16_t rc = getPacketType(&type);
        if (rc != RADIOLIB_ERR_NONE) return rc;
        if (type != RADIOLIB_LR11X0_PACKET_TYPE_LORA) return RADIOLIB_ERR_WRONG_MODEM;
      }
      const uint8_t effectiveLdro = ldroAuto
          ? (float(uint32_t(1) << sf) / bw >= 16.0f
                ? RADIOLIB_LR11X0_LORA_LDRO_ENABLED : RADIOLIB_LR11X0_LORA_LDRO_DISABLED)
          : ldrOptimize;
      const bool unchanged = _profileSwitch.matchesModulation(sf, bwCode, cr - 4, effectiveLdro);
      const auto oldSf = spreadingFactor, oldBw = bandwidth, oldCr = codingRate, oldLdro = ldrOptimize;
      const auto oldBwKhz = bandwidthKhz;
      spreadingFactor = sf;
      bandwidth = bwCode;
      bandwidthKhz = bw;
      codingRate = cr - 4;
      if (unchanged) {
        ldrOptimize = effectiveLdro;
        return RADIOLIB_ERR_NONE;
      }
      const int16_t rc = setModulationParamsLoRa(sf, bwCode, codingRate, effectiveLdro);
      _profileSwitch.modulationResult(rc, sf, bwCode, codingRate, ldrOptimize);
      if (rc != RADIOLIB_ERR_NONE) {
        spreadingFactor = oldSf;
        bandwidth = oldBw;
        bandwidthKhz = oldBwKhz;
        codingRate = oldCr;
        ldrOptimize = oldLdro;
      }
      return rc;
    }

    // Apply the measured TCXO delay on every initialization, including recovery.
    int16_t begin(float freq = 434.0, float bw = 125.0, uint8_t sf = 9, uint8_t cr = 7,
                  uint8_t syncWord = RADIOLIB_LR11X0_LORA_SYNC_WORD_PRIVATE, int8_t power = 10,
                  uint16_t preambleLength = 8, float tcxoVoltage = 1.6) {
      _profileSwitch.invalidate();
      _profilePacketDirty = false;
      int16_t state = LR1110::begin(freq, bw, sf, cr, syncWord, power, preambleLength,
                                    tcxoVoltage);
      if (state == RADIOLIB_ERR_NONE) state = applyMeshCoreTcxoDelay();
      // RadioLib begin() defaults to LDO; use the LR1110 DC/DC regulator.
      if (state == RADIOLIB_ERR_NONE) state = setRegulatorDCDC();
      return state;
    }

    int16_t applyMeshCoreTcxoDelay() {
      if (tcxoVoltage <= 0.0f) return RADIOLIB_ERR_NONE;
      int16_t state = setTCXO(tcxoVoltage, MC_TCXO_DELAY_US);
      RADIOLIB_ASSERT(state);
      state = calibrate(0x3F);
      RADIOLIB_ASSERT(state);          // all blocks; setTCXO moved the gating window
      delay(50);
      return RADIOLIB_ERR_NONE;
    }

    // MeshCore keeps the LR1110 in LoRa mode. Calculate from RadioLib's cached
    // parameters so an airtime query never issues GetPacketType while RX duty
    // cycling has the chip asleep. RadioLib's LR11x0 implementation ignores a
    // failed modem query and can otherwise return an encoded negative error as
    // a multi-million-millisecond airtime.
    RadioLibTime_t getTimeOnAir(size_t len) override {
      return getToA(len, ModemType_t::RADIOLIB_MODEM_LORA);
    }

    // RadioLib waits without a deadline for BUSY to fall after SetTx. Bound
    // that wait so a failed LR1110 transition can reach the wrapper's hard
    // recovery path instead of hanging the firmware indefinitely.
    int16_t launchMode() override {
      if (this->stagedMode != RADIOLIB_RADIO_MODE_TX) {
        return LR1110::launchMode();
      }

      this->mod->setRfSwitchState(this->txMode);
      int16_t state = this->setTx(RADIOLIB_LR11X0_TX_TIMEOUT_NONE);
      if (state != RADIOLIB_ERR_NONE) {
        this->stagedMode = RADIOLIB_RADIO_MODE_NONE;
        return state;
      }

      const uint32_t started = this->mod->hal->millis();
      while (isChipBusy()) {
        this->mod->hal->yield();
        if (this->mod->hal->millis() - started >= LR11X0_TX_BUSY_TIMEOUT_MS) {
          this->stagedMode = RADIOLIB_RADIO_MODE_NONE;
          return RADIOLIB_ERR_SPI_CMD_TIMEOUT;
        }
      }

      this->stagedMode = RADIOLIB_RADIO_MODE_NONE;
      return RADIOLIB_ERR_NONE;
    }

    int16_t recoverReceivePath() {
      _activityAt = 0;
      _headerSeen = false;

      // Standby is the operation known to reset the LR1110's unreported
      // four-byte RX-buffer displacement. Clear stale bytes and IRQ state too,
      // then let RadioLibWrapper re-arm receive mode.
      int16_t state = standby();
      int16_t next = clearRxBuffer();
      if (state == RADIOLIB_ERR_NONE) state = next;
      next = clearIrqState(RADIOLIB_LR11X0_IRQ_ALL);
      if (state == RADIOLIB_ERR_NONE) state = next;
      return state;
    }

    size_t getPacketLength(bool update) override {
      // GetRxBufferStatus can report either zero or a stale/nonzero length for
      // a header-error event. Recover based on the IRQ itself, never its length.
      if (getIrqStatus() & RADIOLIB_LR11X0_IRQ_HEADER_ERR) {
        MESH_DEBUG_PRINTLN("LR1110: header error, resetting RX path");
        recoverReceivePath();
        return 0;
      }
      return LR1110::getPacketLength(update);
    }

    int16_t readData(uint8_t* data, size_t len) override {
      int16_t state = LR1110::readData(data, len);
      if (state != RADIOLIB_ERR_NONE) return state;

      // Defense in depth: if the trigger IRQ was missed, never pass the known
      // shifted/truncated representation up to Dispatcher where it can look
      // like a valid transport flood and be repeated by the mesh.
      if (mesh::hasLR1110RxBufferShiftSignature(data, len)) {
        MESH_DEBUG_PRINTLN("LR1110: four-byte RX shift detected, dropping packet");
        recoverReceivePath();
        return RADIOLIB_ERR_CRC_MISMATCH;
      }
      return RADIOLIB_ERR_NONE;
    }

    float getFreqMHz() const { return freqMHz; }

    int16_t startReceiveDutyCycle(uint32_t rxPeriod, uint32_t sleepPeriod,
                                  RadioLibIrqFlags_t irqFlags = RADIOLIB_IRQ_RX_DEFAULT_FLAGS,
                                  RadioLibIrqFlags_t irqMask = RADIOLIB_IRQ_RX_DEFAULT_MASK) {
      uint32_t symbolPeriod = (uint32_t)(((1000.0f * (float)(1UL << this->spreadingFactor)) /
                                          this->bandwidthKhz) + 0.999f);
      uint32_t transitionTime = this->tcxoDelay + 1000;
      if (sleepPeriod <= transitionTime) {
        return RADIOLIB_ERR_INVALID_SLEEP_PERIOD;
      }
      uint32_t programmedSleepPeriod = sleepPeriod - transitionTime;

      // PreambleDetected restarts the timeout at 2*rx + sleep. LR1110 testing
      // established 78 ms RX / 26.851 ms sleep as the production minimum at
      // SF8, BW 62.5 kHz. Preamble + 11 symbols plus 1 ms preserves that margin.
      uint64_t requiredExtendedPeriod =
          ((uint64_t)this->preambleLengthLoRa + 11ULL) * symbolPeriod + 1000ULL;
      uint64_t extendedPeriod = 2ULL * rxPeriod + programmedSleepPeriod;
      if (extendedPeriod < requiredExtendedPeriod) {
        rxPeriod = (uint32_t)((requiredExtendedPeriod - programmedSleepPeriod + 1ULL) / 2ULL);
      }

      uint32_t rxPeriodRaw = (uint32_t)(((uint64_t)rxPeriod * 32768UL) / 1000000UL);
      uint32_t sleepPeriodRaw =
          (uint32_t)(((uint64_t)programmedSleepPeriod * 32768UL) / 1000000UL);

      if ((rxPeriodRaw & 0xFF000000) || (rxPeriodRaw == 0)) {
        return RADIOLIB_ERR_INVALID_RX_PERIOD;
      }

      if ((sleepPeriodRaw & 0xFF000000) || (sleepPeriodRaw == 0)) {
        return RADIOLIB_ERR_INVALID_SLEEP_PERIOD;
      }

      // Semtech requires Standby RC and an explicitly configured RTC source
      // before SetRxDutyCycle. RadioLib does neither in its LoRa RXPS path.
      int16_t state = standby(RADIOLIB_LR11X0_STANDBY_RC);
      RADIOLIB_ASSERT(state);
      state = configLfClock(
          RADIOLIB_LR11X0_LF_CLK_RC | RADIOLIB_LR11X0_LF_BUSY_RELEASE_ENABLED);
      RADIOLIB_ASSERT(state);

      RadioModeConfig_t cfg = {
        .receive = {
          .timeout = RADIOLIB_LR11X0_RX_TIMEOUT_INF,
          .irqFlags = irqFlags,
          .irqMask = irqMask,
          .len = 0,
        }
      };
      state = this->stageMode(RADIOLIB_RADIO_MODE_RX, &cfg);
      RADIOLIB_ASSERT(state);

      // Send the already converted values. RadioLib 7.7.1 converts them again
      // with 32-bit arithmetic, which overflows for periods above about 131 ms.
      return this->setRxDutyCycle(rxPeriodRaw, sleepPeriodRaw, RADIOLIB_LR11X0_RX_DUTY_CYCLE_MODE_RX);
    }

    int16_t setRxBoostedGainMode(bool en) {
      _rx_boosted = en;
      return LR1110::setRxBoostedGainMode(en);
    }

    bool getRxBoostedGainMode() const { return _rx_boosted; }

    int16_t startReceive() override {
      if (_profileSwitch.canResumeFast()) {
        _profileSwitch.rxValid = false;  // fail closed until the RX command ACKs
#ifdef MC_LR1110_HIL
        uint32_t segment = DWT->CYCCNT;
#endif
        int16_t rc = RADIOLIB_ERR_NONE;
#ifdef MC_LR1110_HIL
        if (hilInjectRxTimeout) {
          hilInjectRxTimeout = false;
          rc = setRx(1);
          if (rc == RADIOLIB_ERR_NONE) delayMicroseconds(150);
        }
        if (rc == RADIOLIB_ERR_NONE && hilFastClearMode == 0)
          rc = clearIrqState(RADIOLIB_LR11X0_IRQ_ALL);
        else if (rc == RADIOLIB_ERR_NONE && hilFastClearMode == 2)
#else
        if (rc == RADIOLIB_ERR_NONE)
#endif
        {
          uint32_t pending = 0;
          rc = snapshotIrqAfterRxExit(&pending);
#ifdef MC_LR1110_HIL
          if (pending) ++hilPendingIrqSnapshots;
#endif
          if (rc == RADIOLIB_ERR_NONE && pending)
            rc = clearIrqState(RADIOLIB_LR11X0_IRQ_ALL);
        }
#ifdef MC_LR1110_HIL
        fastClearCycles += DWT->CYCCNT - segment;
        segment = DWT->CYCCNT;
#endif
        if (rc == RADIOLIB_ERR_NONE && _profilePacketDirty) {
          rc = setPacketParamsLoRa(preambleLengthLoRa, headerType,
              RADIOLIB_LR11X0_MAX_PACKET_LENGTH, crcTypeLoRa, invertIQEnabled);
        }
#ifdef MC_LR1110_HIL
        fastPacketCycles += DWT->CYCCNT - segment;
        segment = DWT->CYCCNT;
#endif
        if (rc == RADIOLIB_ERR_NONE) {
          rxTimeout = RADIOLIB_LR11X0_RX_TIMEOUT_INF;
          mod->setRfSwitchState(Module::MODE_RX);
          rc = setRx(rxTimeout);
        }
#ifdef MC_LR1110_HIL
        fastSetRxCycles += DWT->CYCCNT - segment;
#endif
        stagedMode = RADIOLIB_RADIO_MODE_NONE;
        if (rc == RADIOLIB_ERR_NONE) ++_profileFastResumes;
        if (rc == RADIOLIB_ERR_NONE) _profilePacketDirty = false;
        _profileSwitch.rxResult(rc, true);
        return rc;
      }
      // Keep preamble detection visible to CAD and route header errors to DIO1
      // so the wrapper can reset the RX path before another packet is read.
      const int16_t rc = LR1110::startReceive(
          RADIOLIB_LR11X0_RX_TIMEOUT_INF,
          RADIOLIB_IRQ_RX_DEFAULT_FLAGS | (1UL << RADIOLIB_IRQ_PREAMBLE_DETECTED),
          RADIOLIB_IRQ_RX_DEFAULT_MASK | (1UL << RADIOLIB_IRQ_HEADER_ERR), 0);
      _profileSwitch.rxResult(rc, true);
      if (rc == RADIOLIB_ERR_NONE) _profilePacketDirty = false;
      return rc;
    }

    // BUSY high means the chip is asleep (RX duty-cycle sleep window) or mid
    // command; any SPI access would stall until the chip's next listen window.
    bool isChipBusy() {
      uint32_t busy = this->mod->getGpio();
      return busy != RADIOLIB_NC && this->mod->hal->digitalRead(busy);
    }

    bool isReceiving() {
      if (isChipBusy()) return false;   // asleep, cannot be mid-receive

      uint32_t irq = getIrqStatus();
      bool preamble = irq & RADIOLIB_LR11X0_IRQ_PREAMBLE_DETECTED;      // bit 4
      bool header   = irq & RADIOLIB_LR11X0_IRQ_SYNC_WORD_HEADER_VALID; // bit 5
      bool hdrErr   = irq & RADIOLIB_LR11X0_IRQ_HEADER_ERR;             // bit 6
      uint32_t now  = millis();
      if (hdrErr) {
        // Do not clear this here. The receive wrapper owns header-error
        // recovery and must see the IRQ in order to enter standby. Clearing it
        // from this polling path recreates the four-byte RX-buffer shift bug.
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
          clearIrqState(RADIOLIB_LR11X0_IRQ_PREAMBLE_DETECTED | RADIOLIB_LR11X0_IRQ_SYNC_WORD_HEADER_VALID | RADIOLIB_LR11X0_IRQ_HEADER_ERR);
          _activityAt = 0; _headerSeen = false;
          return false;
        }
        return true;
      }
      if (preamble) {
        if (_activityAt == 0) _activityAt = now;
        if (now - _activityAt > _preambleMillis) {
          clearIrqState(RADIOLIB_LR11X0_IRQ_PREAMBLE_DETECTED);
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
