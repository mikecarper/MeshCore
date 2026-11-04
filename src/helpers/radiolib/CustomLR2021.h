#pragma once

#include <RadioLib.h>
#include "RadioRegulatorState.h"
#include "MeshCore.h"
#include "LR2021Band.h"
#if defined(MESH_W12_PRAM_EXPERIMENT) && MESH_W12_PRAM_EXPERIMENT
#include "W12RxDutyTiming.h"
#include "W12PramLabTrace.h"
#endif
#include "LR2021CalibrationPolicy.h"
#if defined(MESH_ENABLE_LR2021_AUTO_XTRIM) && MESH_ENABLE_LR2021_AUTO_XTRIM
#include "LR2021PacketFrequency.h"
#include "LR2021FrequencyCorrection.h"
#endif
#include "../MorseRx.h"
#include <stdio.h>

#ifndef LR2021_TX_BUSY_TIMEOUT_MS
#define LR2021_TX_BUSY_TIMEOUT_MS 1000UL
#endif

// SIMO needs a fitted external inductor. Opt in only for verified board
// layouts; LR2021 also supports LDO-only designs without that circuitry.
#ifndef LR2021_USE_DCDC
#define LR2021_USE_DCDC 0
#endif

// Optional board-specific FEM settling; the RF table defines standby bias.
#ifndef LR2021_TX_PREARM_MS
#define LR2021_TX_PREARM_MS 0
#endif

class CustomLR2021 : public LR2021 {
#if defined(MESH_W12_PRAM_EXPERIMENT) && MESH_W12_PRAM_EXPERIMENT
  bool _lab_pram_scan_active = false;
#endif
#if LR2021_USE_DCDC
  mesh::power::RadioRegulatorState _regulator{mesh::power::REGULATOR_DCDC | mesh::power::REGULATOR_LDO,
      mesh::power::RegulatorMode::Dcdc};
#else
  // An LDO-only layout must not enable SIMO without its external inductor.
  mesh::power::RadioRegulatorState _regulator{mesh::power::REGULATOR_LDO, mesh::power::RegulatorMode::Ldo};
#endif
  uint32_t _preambleMillis = 66;
  uint32_t _maxPayloadMillis = 3934;
  uint32_t _activityAt = 0;
  bool _headerSeen = false;
  bool _rx_boosted = false;
  mesh::lr2021::XoscTrimSelection _xosc_trim;
  int32_t _last_frequency_error_hz = 0;
  float _last_frequency_error_mhz = 0;
  uint32_t _last_frequency_error_at = 0;
  bool _last_frequency_error_valid = false;
#if defined(MESH_ENABLE_LR2021_AUTO_XTRIM) && MESH_ENABLE_LR2021_AUTO_XTRIM
  // Unlike the retained CLI value, this validity belongs to the latest read.
  bool _packet_frequency_valid = false;
  int32_t _frequency_correction_ppb = 0;
  float _nominal_frequency_mhz = 0;
  float _last_packet_nominal_frequency_mhz = 0;
#endif
  uint32_t _rf_switch_pins[Module::RFSWITCH_MAX_PINS] = {};
  const Module::RfSwitchMode_t* _rf_switch_table = nullptr;

#if LR2021_TX_PREARM_MS > 0
  bool _tx_standby_armed = false;

  int16_t setTxStandbyMask(bool arm) {
    if (!_rf_switch_table || (!arm && !_tx_standby_armed)) return RADIOLIB_ERR_NONE;
    // Set the flag before writing: a failed SPI response may still have changed
    // a DIO. Cleanup must attempt every affected pin, not stop at the first error.
    _tx_standby_armed = true;
    int16_t result = RADIOLIB_ERR_NONE;
    for (size_t i = 0; i < Module::RFSWITCH_MAX_PINS; ++i) {
      const uint32_t pin = _rf_switch_pins[i];
      if (pin == RADIOLIB_NC || !(pin & RFSWITCH_PIN_FLAG)) continue;
      const uint8_t dio = RADIOLIB_LRXXXX_DIOx_VAL(pin);
      if (dio >= 7) {
        if (result == RADIOLIB_ERR_NONE) result = RADIOLIB_ERR_UNKNOWN;
        continue;
      }
      uint8_t config = 0;
      for (size_t j = 0; j < 8 && _rf_switch_table[j].mode != LR2021::MODE_END_OF_TABLE; ++j) {
        const uint8_t mode = _rf_switch_table[j].mode;
        if (mode > 0 && mode <= 8
            && _rf_switch_table[j].values[i] == this->mod->hal->GpioLevelHigh)
          config |= 1UL << (mode - 1);
      }
      const uint8_t standby_bit = 1U << (LR2021::MODE_STBY - 1);
      const uint8_t tx_bit = 1U << (LR2021::MODE_TX - 1);
      if (!(config & tx_bit) || (config & standby_bit)) continue;
      const int16_t status = setDioRfSwitchConfig(dio + 5, arm ? config | standby_bit : config);
      if (result == RADIOLIB_ERR_NONE) result = status;
    }
    if (!arm && result == RADIOLIB_ERR_NONE) _tx_standby_armed = false;
    return result;
  }

  int16_t prepareTxRfSwitch() {
    // stageMode(TX) does not enter standby. Stop RX before settling the LF PA.
    // A warm standby table (W12 PR #3579) retains its original DIO masks;
    // other tables temporarily add the TX bias only for this preparation.
    int16_t status = standby();
    if (status == RADIOLIB_ERR_NONE && !mesh::lr2021::isHighBand(freqMHz)) {
      status = setTxStandbyMask(true);
      // Reissue the mode after programming its mask: do not rely on a mask
      // write changing an already-selected mode's outputs immediately. Bypass
      // our cleanup override here so this one intentional warm-up stays armed.
      if (status == RADIOLIB_ERR_NONE) status = LR2021::standby();
      if (status == RADIOLIB_ERR_NONE) this->mod->hal->delay(LR2021_TX_PREARM_MS);
    }
    return status;
  }

  int16_t finishTxRfSwitch(int16_t status) {
    // Only change masks while transmitting. SetDioFunction (used by the full
    // reset-restoration helper) is restricted to Standby RC by the LR2021.
    // TX bits remain HIGH: disarming standby does not interrupt the packet/CW.
    const int16_t restored = setTxStandbyMask(false);
    if (status != RADIOLIB_ERR_NONE || restored != RADIOLIB_ERR_NONE) {
      const int16_t stopped = standby();
      // Request the wrapper's hard-reset recovery if the PA state is uncertain.
      if (restored != RADIOLIB_ERR_NONE || stopped != RADIOLIB_ERR_NONE)
        return RADIOLIB_ERR_SPI_CMD_TIMEOUT;
    }
    return status;
  }
#endif

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
#if LR2021_TX_PREARM_MS > 0
    _tx_standby_armed = false;
#endif
    // Some DIO implementations latch a new mask only on a mode transition.
    // Reset restoration must physically select the restored standby outputs.
    return LR2021::standby();
  }

  uint8_t rfSwitchRxMode() const {
    return mesh::lr2021::isHighBand(freqMHz) ? LR2021::MODE_RX_HF : LR2021::MODE_RX;
  }

  uint8_t rfSwitchTxMode() const {
    return mesh::lr2021::isHighBand(freqMHz) ? LR2021::MODE_TX_HF : LR2021::MODE_TX;
  }

  public:
    CustomLR2021(Module *mod) : LR2021(mod) { irqDioNum = LR2021_IRQ_DIO; }

    uint8_t regulatorModes() const { return _regulator.modes(); }
    mesh::power::RegulatorMode defaultRegulatorMode() const { return _regulator.defaultMode(); }
    bool configureBootRegulator(mesh::power::RegulatorMode mode) { return _regulator.configureBoot(mode); }
    mesh::power::RegulatorMode activeRegulatorMode() const { return _regulator.activeMode(); }

    bool supportsXoscTrim() const {
    #ifdef LR2021_TCXO_VOLTAGE
      return LR2021_TCXO_VOLTAGE <= 0.0f;
    #else
      // std_init() otherwise selects the legacy 1.6 V TCXO default.
      return false;
    #endif
    }

    bool configureBootXoscTrim(bool enabled, uint8_t a, uint8_t b) {
      if (!supportsXoscTrim() || (enabled && !mesh::lr2021::validXoscTrim(a, b))) return false;
      _xosc_trim.enabled = enabled;
      _xosc_trim.a = enabled ? a : 0;
      _xosc_trim.b = enabled ? b : 0;
      return true;
    }

    bool getXoscTrim(uint8_t& a, uint8_t& b) const {
      if (!_xosc_trim.enabled) return false;
      a = _xosc_trim.a;
      b = _xosc_trim.b;
      return true;
    }

    // Selection only. The wrapper restores native defaults through a real
    // reset before claiming that clearing an override changed the hardware.
    void clearXoscTrim() { _xosc_trim = mesh::lr2021::XoscTrimSelection{}; }

    int16_t applyXoscTrim(uint8_t a, uint8_t b) {
      if (!supportsXoscTrim()) return RADIOLIB_ERR_UNSUPPORTED;
      if (!mesh::lr2021::validXoscTrim(a, b)) return RADIOLIB_ERR_UNKNOWN;
      // The new capacitor codes take effect when XOSC restarts. Stop it first;
      // beginReconfigure() owns packet/IRQ safety and the subsequent RX restart.
      int16_t status = LR2021::standby(RADIOLIB_LR2021_STANDBY_RC);
      if (status == RADIOLIB_ERR_NONE) {
        uint8_t bytes[] = {a, b};
        // The third command byte is optional. Omitting it preserves the
        // board's additional oscillator start delay (datasheet 6.11.4).
        status = SPIcommand(RADIOLIB_LR2021_CMD_SET_XOSC_CP_TRIM, true, bytes, sizeof(bytes));
      }
      if (status == RADIOLIB_ERR_NONE) configureBootXoscTrim(true, a, b);
      return status;
    }

    int16_t restoreXoscTrim() {
      return _xosc_trim.enabled ? applyXoscTrim(_xosc_trim.a, _xosc_trim.b) : RADIOLIB_ERR_NONE;
    }

    int16_t readPacketFrequencyErrorHz(int32_t& value) {
      uint8_t modem = RADIOLIB_LR2021_PACKET_TYPE_NONE;
      int16_t status = getPacketType(&modem);
      if (status != RADIOLIB_ERR_NONE) return status;
      if (modem != RADIOLIB_LR2021_PACKET_TYPE_LORA) return RADIOLIB_ERR_UNSUPPORTED;
      // Datasheet 2.2 extends the earlier six-byte status with signed Hz.
      // Read before FIFO consumption/CAD/new RX can replace packet metadata.
      uint8_t bytes[9] = {};
      status = SPIcommand(RADIOLIB_LR2021_CMD_GET_LORA_PACKET_STATUS, false, bytes, sizeof(bytes));
      if (status == RADIOLIB_ERR_NONE) value = mesh::lr2021::decodeFrequencyErrorHz(bytes + 6);
      return status;
    }

    void formatFrequencyError(char* reply, size_t capacity) const {
      if (!_last_frequency_error_valid) {
        snprintf(reply, capacity, "> unavailable; no valid LoRa packet frequency measurement");
        return;
      }
      const uint32_t age_seconds = uint32_t(this->mod->hal->millis() - _last_frequency_error_at) / 1000;
#if defined(MESH_ENABLE_LR2021_AUTO_XTRIM) && MESH_ENABLE_LR2021_AUTO_XTRIM
      snprintf(reply, capacity, "> %+ld Hz; freq=%.6f MHz; age=%lus; programmed=%.6f MHz",
          static_cast<long>(_last_frequency_error_hz), static_cast<double>(_last_packet_nominal_frequency_mhz),
          static_cast<unsigned long>(age_seconds), static_cast<double>(_last_frequency_error_mhz));
#else
      snprintf(reply, capacity, "> %+ld Hz; freq=%.6f MHz; age=%lus",
          static_cast<long>(_last_frequency_error_hz), static_cast<double>(_last_frequency_error_mhz),
          static_cast<unsigned long>(age_seconds));
#endif
    }

#if defined(MESH_ENABLE_LR2021_AUTO_XTRIM) && MESH_ENABLE_LR2021_AUTO_XTRIM
    bool lastPacketFrequency(mesh::lr2021::PacketFrequencyObservation& out) const {
      out = {};
      if (!_packet_frequency_valid) return false;
      out.offset_hz = _last_frequency_error_hz;
      // Match RadioLib's MHz-to-Hz conversion for SetRfFrequency.
      out.frequency_hz = static_cast<uint32_t>(_last_packet_nominal_frequency_mhz * 1000000.0f);
      out.captured_at_ms = _last_frequency_error_at;
      out.valid = true;
      return true;
    }
#endif

#if LR2021_TX_PREARM_MS > 0
    using LR2021::standby;
    int16_t standby() override {
      const bool was_armed = _tx_standby_armed;
      const int16_t status = LR2021::standby();
      const int16_t restored = setTxStandbyMask(false);
      if (restored != RADIOLIB_ERR_NONE) return RADIOLIB_ERR_SPI_CMD_TIMEOUT;
      // Also latch the normal mask after a dirty/prearmed-state cleanup.
      if (was_armed && (status != RADIOLIB_ERR_NONE || LR2021::standby() != RADIOLIB_ERR_NONE)) {
        // A mask write alone does not prove the PA turned off. Keep retrying
        // cleanup (or hard reset) until a normal standby is confirmed.
        _tx_standby_armed = true;
        return RADIOLIB_ERR_SPI_CMD_TIMEOUT;
      }
      return status;
    }

    using LR2021::sleep;
    int16_t sleep() override {
      const int16_t status = standby();
      if (status != RADIOLIB_ERR_NONE) return status;
      return LR2021::sleep();
    }
#endif

    int16_t setProfileProtocol(uint8_t syncWord) {
      int16_t status = LR2021::setSyncWord(syncWord);
      if (status == RADIOLIB_ERR_NONE) status = LR2021::setCRC(2);
      if (status == RADIOLIB_ERR_NONE) status = LR2021::explicitHeader();
      if (status == RADIOLIB_ERR_NONE) status = LR2021::invertIQ(false);
      return status;
    }

    int16_t beginMorseReceive(float frequency_mhz) {
      const auto regulator = _regulator.beginInitialization();
#if defined(MESH_ENABLE_LR2021_AUTO_XTRIM) && MESH_ENABLE_LR2021_AUTO_XTRIM
      const float hardware_frequency_mhz = mesh::lr2021::correctedFrequencyMHz(frequency_mhz, _frequency_correction_ppb);
#else
      const float hardware_frequency_mhz = frequency_mhz;
#endif
      const uint8_t gain = mesh::lr2021::isHighBand(hardware_frequency_mhz) ? gainModeHf : gainModeLf;
      int16_t status = LR2021::beginGFSK(frequency_mhz,
          mesh::MorseRx::ReceiverBitRateKbps, 0.6f, 4.8f, 2, 16, tcxoVoltage);
      if (status == RADIOLIB_ERR_NONE) status = restoreXoscTrim();
      // Native initialization resets to LDO. SIMO is allowed only on verified
      // layouts; an explicit LDO choice on those boards is checked as well.
      if (status == RADIOLIB_ERR_NONE && regulator == mesh::power::RegulatorMode::Dcdc)
        status = setRegulatorDCDC();
    #if LR2021_USE_DCDC
      else if (status == RADIOLIB_ERR_NONE) status = setRegulatorLDO();
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
      return _regulator.finishInitialization(status);
    }

    bool std_init(SPIClass* spi = NULL) {
      const auto regulator = _regulator.beginInitialization();
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
        _regulator.finishInitialization(status);
        return false;  // fail
      }

      // No override means no trim command, preserving the native chip default.
      // Reapply an explicit selection after recovery and temporary Morse mode.
      status = restoreXoscTrim();
      if (status != RADIOLIB_ERR_NONE) {
        _regulator.finishInitialization(status);
        return false;
      }

      // Also reached when restoring LoRa after Morse RX or hard recovery.
      if (regulator == mesh::power::RegulatorMode::Dcdc) status = setRegulatorDCDC();
    #if LR2021_USE_DCDC
      else status = setRegulatorLDO();
    #endif
      if (_regulator.finishInitialization(status) != RADIOLIB_ERR_NONE) return false;

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

    int16_t replaceRfSwitchTable(const uint32_t (&pins)[Module::RFSWITCH_MAX_PINS],
                                 const Module::RfSwitchMode_t table[]) {
      // Used by W12 supply transitions. Keep the old rail powered until a
      // checked cold standby has confirmed every control output is low.
      int16_t status = LR2021::standby();
      if (status != RADIOLIB_ERR_NONE) return status;
      for (size_t i = 0; i < Module::RFSWITCH_MAX_PINS; ++i) _rf_switch_pins[i] = pins[i];
      _rf_switch_table = table;
      return restoreRfSwitchTable();
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
#if defined(MESH_ENABLE_LR2021_AUTO_XTRIM) && MESH_ENABLE_LR2021_AUTO_XTRIM
    float getNominalFreqMHz() const { return _nominal_frequency_mhz; }
    int32_t frequencyCorrectionPpb() const { return _frequency_correction_ppb; }
    bool configureFrequencyCorrection(int32_t ppb) {
      if (!mesh::lr2021::validFrequencyCorrection(ppb)) return false;
      _frequency_correction_ppb = ppb;
      return true;
    }
    using LR2021::setFrequency;
    int16_t setFrequency(float nominal_mhz) override {
      const float actual_mhz = mesh::lr2021::correctedFrequencyMHz(nominal_mhz, _frequency_correction_ppb);
      const int16_t result = LR2021::setFrequency(actual_mhz);
      if (result == RADIOLIB_ERR_NONE) _nominal_frequency_mhz = nominal_mhz;
      return result;
    }
#endif

    int16_t transmitMorseCarrier() {
      // RadioLib's generic direct-TX path always selects MODE_TX. Use the
      // band-aware routing also used by our normal packet transmit path.
    #if LR2021_TX_PREARM_MS > 0
      const int16_t prepared = prepareTxRfSwitch();
      if (prepared != RADIOLIB_ERR_NONE) return finishTxRfSwitch(prepared);
    #endif
      this->mod->setRfSwitchState(rfSwitchTxMode());
      const int16_t status = this->setTxTestMode(RADIOLIB_LR2021_TX_TEST_MODE_CW);
    #if LR2021_TX_PREARM_MS > 0
      return finishTxRfSwitch(status);
    #else
      return status;
    #endif
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
      #if LR2021_TX_PREARM_MS > 0
        state = setTxStandbyMask(false);
        if (state != RADIOLIB_ERR_NONE) {
          this->stagedMode = RADIOLIB_RADIO_MODE_NONE;
          return RADIOLIB_ERR_SPI_CMD_TIMEOUT;
        }
      #endif
        this->mod->setRfSwitchState(rfSwitchRxMode());
        state = this->setRx(this->rxTimeout);
      } else if (transmitting) {
      #if LR2021_TX_PREARM_MS > 0
        state = prepareTxRfSwitch();
        if (state != RADIOLIB_ERR_NONE) {
          this->stagedMode = RADIOLIB_RADIO_MODE_NONE;
          return finishTxRfSwitch(state);
        }
      #endif
        this->mod->setRfSwitchState(rfSwitchTxMode());
        state = this->setTx(RADIOLIB_LR2021_TX_TIMEOUT_NONE);
      } else {
        return RADIOLIB_ERR_UNSUPPORTED;
      }

      if (state != RADIOLIB_ERR_NONE) {
        this->stagedMode = RADIOLIB_RADIO_MODE_NONE;
      #if LR2021_TX_PREARM_MS > 0
        if (transmitting) return finishTxRfSwitch(state);
      #endif
        return state;
      }

      if (transmitting) {
        const RadioLibTime_t started = this->mod->hal->millis();
        while (this->mod->hal->digitalRead(this->mod->getGpio())) {
          this->mod->hal->yield();
          if (this->mod->hal->millis() - started >= LR2021_TX_BUSY_TIMEOUT_MS) {
            this->stagedMode = RADIOLIB_RADIO_MODE_NONE;
          #if LR2021_TX_PREARM_MS > 0
            return finishTxRfSwitch(RADIOLIB_ERR_SPI_CMD_TIMEOUT);
          #else
            return RADIOLIB_ERR_SPI_CMD_TIMEOUT;
          #endif
          }
        }
      }

      this->stagedMode = RADIOLIB_RADIO_MODE_NONE;
    #if LR2021_TX_PREARM_MS > 0
      if (transmitting) return finishTxRfSwitch(RADIOLIB_ERR_NONE);
    #endif
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

#if defined(MESH_W12_PRAM_EXPERIMENT) && MESH_W12_PRAM_EXPERIMENT
    int16_t labStartRxDutyCycle(uint32_t rx_us, uint32_t sleep_us,
                                RadioLibIrqFlags_t flags, RadioLibIrqFlags_t mask) {
      mesh::w12rxps::Timing t;
      // W12 uses a crystal, so the board's wake compensation is 1 ms.
      if (!mesh::w12rxps::encode(rx_us, sleep_us, 1000, t))
        return RADIOLIB_ERR_INVALID_RX_PERIOD;
      RadioModeConfig_t cfg = {};
      cfg.receive.timeout = RADIOLIB_LR2021_RX_TIMEOUT_INF;
      cfg.receive.irqFlags = flags;
      cfg.receive.irqMask = mask;
      const int16_t staged = stageMode(RADIOLIB_RADIO_MODE_RX, &cfg);
      if (staged != RADIOLIB_ERR_NONE) return staged;
      uint8_t bytes[] = {uint8_t(t.rx_ticks >> 16), uint8_t(t.rx_ticks >> 8), uint8_t(t.rx_ticks),
                         uint8_t(t.cycle_ticks >> 16), uint8_t(t.cycle_ticks >> 8), uint8_t(t.cycle_ticks), 0};
      // Include the arm command and RadioLib's immediate status verification:
      // a subsequent NSS pulse may wake/cancel the duty-cycle state machine.
      mesh::w12pram::spiTrace().active=true;
      const int16_t rc=SPIcommand(RADIOLIB_LR2021_CMD_SET_RX_DUTY_CYCLE, true, bytes, sizeof(bytes));
      if (rc!=RADIOLIB_ERR_NONE) mesh::w12pram::spiTrace().active=false;
      return rc;
    }
    int16_t labSpiCommand(uint16_t op,bool write,uint8_t* data,size_t len,const uint8_t* out=nullptr,size_t outlen=0) {
      return SPIcommand(op,write,data,len,out,outlen);
    }
    void labSetPramScan(bool active) {
      _lab_pram_scan_active=active;
      if (active) {
        _last_frequency_error_valid=false;
#if defined(MESH_ENABLE_LR2021_AUTO_XTRIM) && MESH_ENABLE_LR2021_AUTO_XTRIM
        _packet_frequency_valid=false;
#endif
      }
    }
#endif

    int16_t readData(uint8_t* data, size_t len) override {
#if defined(MESH_ENABLE_LR2021_AUTO_XTRIM) && MESH_ENABLE_LR2021_AUTO_XTRIM
      _packet_frequency_valid = false;
#endif
      // PR #3512 (d208bfef): reject continuous-RX FIFO races. The wrapper
      // stops RX before this snapshot; reject errors/mismatches before copying.
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
      int32_t frequency_error_hz = 0;
#if defined(MESH_W12_PRAM_EXPERIMENT) && MESH_W12_PRAM_EXPERIMENT
      const bool measured = !_lab_pram_scan_active && readPacketFrequencyErrorHz(frequency_error_hz) == RADIOLIB_ERR_NONE;
#else
      const bool measured = readPacketFrequencyErrorHz(frequency_error_hz) == RADIOLIB_ERR_NONE;
#endif
      const float measured_frequency = freqMHz;
      const int16_t result = LR2021::readData(data, len);
      if (result == RADIOLIB_ERR_NONE && measured) {
        _last_frequency_error_hz = frequency_error_hz;
        _last_frequency_error_mhz = measured_frequency;
        _last_frequency_error_at = this->mod->hal->millis();
        _last_frequency_error_valid = true;
#if defined(MESH_ENABLE_LR2021_AUTO_XTRIM) && MESH_ENABLE_LR2021_AUTO_XTRIM
        _packet_frequency_valid = true;
        _last_packet_nominal_frequency_mhz = _nominal_frequency_mhz;
#endif
      }
      // A bad CRC/read leaves the last valid sample and its original age.
      return result;
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
