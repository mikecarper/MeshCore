#pragma once

#include <RadioLib.h>
#include "RadioRegulatorState.h"
#include "MeshCore.h"
#include "RXPowerSaving.h"
#include "LR11x0MorseRfSwitch.h"
#include "../MorseRx.h"

class CustomLR1121 : public LR1121 {
  // Existing LR11xx recipes already require the fitted DC/DC network.
  mesh::power::RadioRegulatorState _regulator{mesh::power::REGULATOR_DCDC | mesh::power::REGULATOR_LDO,
      mesh::power::RegulatorMode::Dcdc};
  uint32_t _preambleMillis = 66;
  uint32_t _maxPayloadMillis = 3934;
  uint32_t _activityAt = 0;
  bool _headerSeen = false;
  bool _rx_boosted = false;
  bool _lfClockRestoreNeeded = false;
  LR11x0MorseRfSwitch _morse_rf_switch;
  int8_t _txPower = 0;
  bool _txPowerSet = false;

  public:
#define MC_LR_AGC_2021 0
#include "LRxxxxAGCChip.inc"
#undef MC_LR_AGC_2021

    CustomLR1121(Module *mod) : LR1121(mod) { }

    uint8_t regulatorModes() const { return _regulator.modes(); }
    mesh::power::RegulatorMode defaultRegulatorMode() const { return _regulator.defaultMode(); }
    bool configureBootRegulator(mesh::power::RegulatorMode mode) { return _regulator.configureBoot(mode); }
    mesh::power::RegulatorMode activeRegulatorMode() const { return _regulator.activeMode(); }

    int16_t setProfileProtocol(uint8_t syncWord) {
      int16_t status = LR1121::setSyncWord(syncWord);
      if (status == RADIOLIB_ERR_NONE) status = LR1121::setCRC(2);
      if (status == RADIOLIB_ERR_NONE) status = LR1121::explicitHeader();
      if (status == RADIOLIB_ERR_NONE) status = LR1121::invertIQ(false);
      return status;
    }

    void setRfSwitchTable(const uint32_t (&pins)[Module::RFSWITCH_MAX_PINS],
                          const Module::RfSwitchMode_t table[]) {
      _morse_rf_switch.capture(this->mod, pins, table);
      LR1121::setRfSwitchTable(pins, table);
    }

    int16_t beginMorseReceive(float frequency_mhz) {
      const auto regulator = _regulator.beginInitialization();
      int16_t status = LR1121::beginGFSK(frequency_mhz,
          mesh::MorseRx::ReceiverBitRateKbps, 0.6f, 4.8f, 2, 16, tcxoVoltage);
      if (status == RADIOLIB_ERR_NONE) status = regulator == mesh::power::RegulatorMode::Ldo
          ? setRegulatorLDO() : setRegulatorDCDC();
      if (status == RADIOLIB_ERR_NONE) status = _morse_rf_switch.restore(
          [this](uint8_t enable, const uint8_t* modes) {
            return setDioAsRfSwitch(enable, modes[0], modes[1], modes[2],
                modes[3], modes[4], modes[5], modes[6]);
          });
      if (status == RADIOLIB_ERR_NONE) status = LR1121::setRxBoostedGainMode(_rx_boosted);
      if (status == RADIOLIB_ERR_NONE) {
        syncWordLength = preambleDetLength = 0;
        status = LR1121::setCRC(0);
      }
      return _regulator.finishInitialization(status);
    }

    int16_t begin(float freq = 434.0, float bw = 125.0, uint8_t sf = 9, uint8_t cr = 7,
                  uint8_t syncWord = RADIOLIB_LR11X0_LORA_SYNC_WORD_PRIVATE, int8_t power = 10,
                  uint16_t preambleLength = 8, float tcxoVoltage = 1.6) {
      const auto regulator = _regulator.beginInitialization();
      int16_t state = LR1121::begin(freq, bw, sf, cr, syncWord, power, preambleLength,
                                    tcxoVoltage);
      // Native begin() resets to LDO; restore the boot choice on every reset.
      if (state == RADIOLIB_ERR_NONE) state = regulator == mesh::power::RegulatorMode::Ldo
          ? setRegulatorLDO() : setRegulatorDCDC();
      return _regulator.finishInitialization(state);
    }

    // Airtime is a cached LoRa calculation. GetPacketType would wake a
    // sleeping duty-cycle receiver merely to estimate an outgoing packet.
    RadioLibTime_t getTimeOnAir(size_t len) override {
      return getToA(len, ModemType_t::RADIOLIB_MODEM_LORA);
    }

    int16_t recoverReceivePath() {
      _activityAt = 0;
      _headerSeen = false;
      int16_t state = standby();
      int16_t next = clearRxBuffer();
      if (state == RADIOLIB_ERR_NONE) state = next;
      next = clearIrqState(RADIOLIB_LR11X0_IRQ_ALL);
      if (state == RADIOLIB_ERR_NONE) state = next;
      return state;
    }

    size_t getPacketLength(bool update) override {
      // A header-error event can retain the previous packet's nonzero length.
      // Recover from the IRQ itself rather than delivering those stale bytes.
      if (getIrqStatus() & RADIOLIB_LR11X0_IRQ_HEADER_ERR) {
        MESH_DEBUG_PRINTLN("LR1121: header error, resetting RX path");
        recoverReceivePath();
        return 0;
      }
      return LR1121::getPacketLength(update);
    }

    using LR1121::setOutputPower;
    using LR1121::setFrequency;

    int16_t setOutputPower(int8_t power) override {
#ifdef LR1121_FORCE_HP_PA
      const bool force_hp = true;   // boards with only RFO_HP_LF wired: never select the LP PA
#else
      const bool force_hp = false;
#endif
      // clip to the active PA's range (HF PA tops out at 13 dBm) rather than fail and keep the other band's PA
      int8_t clipped = power;
      checkOutputPower(power, &clipped, force_hp);
      int16_t state = LR1121::setOutputPower(clipped, force_hp);
      if (state == RADIOLIB_ERR_NONE) {
        _txPower = power;
        _txPowerSet = true;
      }
      return state;
    }

    int16_t setFrequency(float freq) override {
      int16_t state = LR1121::setFrequency(freq);
      // PA selection depends on the band, and set radio / tempradio don't call setTxPower
      if (state == RADIOLIB_ERR_NONE && _txPowerSet) state = setOutputPower(_txPower);
      return state;
    }

    float getFreqMHz() const { return freqMHz; }
    uint32_t getTcxoDelay() const { return tcxoDelay; }

    bool needsLfClockRestore() const { return _lfClockRestoreNeeded; }
    int16_t restoreLfClock() {
      if (!_lfClockRestoreNeeded) return RADIOLIB_ERR_NONE;
      // Retain the LF RC clock: DIO11 can also be an IRQ/RF-switch signal.
      const int16_t state = configLfClock(
          RADIOLIB_LR11X0_LF_BUSY_RELEASE_DISABLED | RADIOLIB_LR11X0_LF_CLK_RC);
      if (state == RADIOLIB_ERR_NONE) _lfClockRestoreNeeded = false;
      return state;
    }

    int16_t startReceiveDutyCycle(uint32_t rxPeriod, uint32_t sleepPeriod,
                                  RadioLibIrqFlags_t irqFlags = RADIOLIB_IRQ_RX_DEFAULT_FLAGS,
                                  RadioLibIrqFlags_t irqMask = RADIOLIB_IRQ_RX_DEFAULT_MASK,
                                  uint32_t* effectiveRxPeriod = nullptr) {
      // Reject zero/sub-tick inputs before the preamble guard can enlarge
      // them: one 32.768 kHz RTC tick requires at least 31 microseconds.
      if (rxPeriod < 31UL) return RADIOLIB_ERR_INVALID_RX_PERIOD;
      const uint32_t symbolPeriod = (uint32_t)(((1000.0f * (float)(1UL << spreadingFactor)) /
                                               bandwidthKhz) + 0.999f);
      const uint32_t transitionTime = tcxoDelay + 1000UL;
      if (sleepPeriod <= transitionTime) return RADIOLIB_ERR_INVALID_SLEEP_PERIOD;
      const uint32_t programmedSleepPeriod = sleepPeriod - transitionTime;

      // Use the existing LR11x0 extended-preamble guard. Preamble detection
      // extends the receive timeout to 2*rx + programmed sleep.
      const uint64_t requiredExtendedPeriod =
          ((uint64_t)preambleLengthLoRa + 11ULL) * symbolPeriod + 1000ULL;
      if (2ULL * rxPeriod + programmedSleepPeriod < requiredExtendedPeriod) {
        rxPeriod = (uint32_t)((requiredExtendedPeriod - programmedSleepPeriod + 1ULL) / 2ULL);
      }
      if (effectiveRxPeriod) *effectiveRxPeriod = rxPeriod;

      // LR11xx uses 32.768 kHz RTC ticks. Native RadioLib 7.7.1 overflows
      // its 32-bit multiplication for periods above roughly 131 ms.
      const uint32_t rxPeriodRaw = (uint32_t)(((uint64_t)rxPeriod * 32768UL) / 1000000UL);
      const uint32_t sleepPeriodRaw =
          (uint32_t)(((uint64_t)programmedSleepPeriod * 32768UL) / 1000000UL);
      if ((rxPeriodRaw & 0xFF000000) || rxPeriodRaw == 0) return RADIOLIB_ERR_INVALID_RX_PERIOD;
      if ((sleepPeriodRaw & 0xFF000000) || sleepPeriodRaw == 0) return RADIOLIB_ERR_INVALID_SLEEP_PERIOD;

      // SetRxDutyCycle requires Standby RC and a configured RTC source.
      int16_t state = standby(RADIOLIB_LR11X0_STANDBY_RC);
      RADIOLIB_ASSERT(state);
      _lfClockRestoreNeeded = true;
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
      state = stageMode(RADIOLIB_RADIO_MODE_RX, &cfg);
      RADIOLIB_ASSERT(state);
      // stageMode prepares packet/IRQ state; launch the RX RF path too.
      mod->setRfSwitchState(Module::MODE_RX);
      state = setRxDutyCycle(rxPeriodRaw, sleepPeriodRaw, RADIOLIB_LR11X0_RX_DUTY_CYCLE_MODE_RX);
      if (state == RADIOLIB_ERR_NONE) {
        _activityAt = 0;
        _headerSeen = false;
      }
      return state;
    }

    int16_t setRxBoostedGainMode(bool en) {
      _rx_boosted = en;
      return LR1121::setRxBoostedGainMode(en);
    }

    bool getRxBoostedGainMode() const { return _rx_boosted; }

    int16_t startReceive() override {
      // include the PREAMBLE_DETECTED irq bit in reported flags.
      const int16_t status = LR1121::startReceive(RADIOLIB_LR11X0_RX_TIMEOUT_INF,
          RADIOLIB_IRQ_RX_DEFAULT_FLAGS | (1UL << RADIOLIB_IRQ_PREAMBLE_DETECTED),
          RADIOLIB_IRQ_RX_DEFAULT_MASK | (1UL << RADIOLIB_IRQ_HEADER_ERR), 0);
      if (status == RADIOLIB_ERR_NONE) {
        _activityAt = 0;
        _headerSeen = false;
      }
      return status;
    }

    bool isChipBusy() {
      const uint32_t busy = mod->getGpio();
      return busy != RADIOLIB_NC && mod->hal->digitalRead(busy);
    }

    bool isReceiving() {
      if (isChipBusy()) return false;
      uint32_t irq = getIrqStatus();
      bool preamble = irq & RADIOLIB_LR11X0_IRQ_PREAMBLE_DETECTED;      // bit 4
      bool header   = irq & RADIOLIB_LR11X0_IRQ_SYNC_WORD_HEADER_VALID; // bit 5
      bool hdrErr   = irq & RADIOLIB_LR11X0_IRQ_HEADER_ERR;             // bit 6
      uint32_t now  = millis();
      if (hdrErr) {
        // The receive wrapper owns recovery. Clearing this during polling
        // would hide the error before stale RX-buffer length is rejected.
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
