#pragma once

#include "CustomLR2021.h"
#include "LR2021SideDetectorConfig.h"
#include "LR2021ApplyStatus.h"
#if defined(MESH_W12_PRAM_EXPERIMENT) && MESH_W12_PRAM_EXPERIMENT
#include "W12PramScannerCodec.h"
#include "W12PramScannerBlob.h"
#include <helpers/esp32/PowerTestControls.h>
#endif
#include "PrimaryRadioApplyWait.h"
#include "RadioLibWrappers.h"

#ifndef USE_LR2021
#define USE_LR2021
#endif

#include "RadioPowerLimits.h"

#ifndef LR2021_RX_BOOST_LEVEL
#define LR2021_RX_BOOST_LEVEL 7
#endif

class CustomLR2021Wrapper : public RadioLibWrapper {
#if defined(MESH_W12_PRAM_EXPERIMENT) && MESH_W12_PRAM_EXPERIMENT
#include "W12PramScannerLab.inc"
#endif
  mesh::lr2021::ApplyStatus _apply_status;
  mesh::PrimaryRadioApplyWait _trim_apply_wait;
  uint16_t _trim_apply_key = UINT16_MAX;
  // Valid only between consecutive successful, uninterrupted scan hops.
  // A TX, CAD, ordinary RX restart, reset, or failed transaction revokes it.
  bool _scan_tuple_owned = false;
  bool _scan_retune = false;
  bool _scan_tuple_applied = false;

  void invalidateScanTuple() { _scan_tuple_owned = false; }

  void beginProfileRetune(bool continuous_rx) override {
    _scan_retune = continuous_rx && _profiles.enabled() && _params_valid
        && !_profile_refresh_required && !_profile_repair_required;
    _scan_tuple_applied = false;
    if (!_scan_retune) invalidateScanTuple();
  }

  void endProfileRetune(bool success) override {
    if (!success || !_scan_retune) invalidateScanTuple();
    else if (_scan_tuple_applied) _scan_tuple_owned = true;
    // A BUSY admission has not touched the modem and cannot establish ownership.
    _scan_retune = false;
    _scan_tuple_applied = false;
  }

  bool scanTargetMatches(float freq, float bw, uint8_t sf, uint8_t cr) const {
#if !defined(MESHNOLOGY_W12) || (defined(MESH_DISABLE_LR2021_SCAN_REUSE) && MESH_DISABLE_LR2021_SCAN_REUSE)
    // Other LR2021 boards retain the existing full retune until separately
    // qualified on their own RF and power hardware.
    (void)freq; (void)bw; (void)sf; (void)cr;
    return false;
#else
    if (!_scan_retune || _active_profile > 1) return false;
    const auto& current = _profiles.params(_active_profile);
    const auto& target = _profiles.params(_active_profile ^ 1);
    const float current_hardware_freq = static_cast<const CustomLR2021*>(_radio)->getFreqMHz();
    const float target_hardware_freq = hardwareFrequencyMHz(freq);
    if (_profile_generation != _profiles.generation[_active_profile]
        || current.protocol != mesh::RadioProfileProtocol::MeshCore
        || target.protocol != mesh::RadioProfileProtocol::MeshCore
        || _cur_freq != current.freq || _cur_bw != current.bw
        || _cur_sf != current.sf || _cur_cr != current.cr
        || current_hardware_freq != hardwareFrequencyMHz(_cur_freq)
        || mesh::lr2021::isHighBand(current_hardware_freq) != mesh::lr2021::isHighBand(target_hardware_freq)
        || fabsf(target_hardware_freq - current_hardware_freq) >= RADIOLIB_LR2021_CAL_IMG_FREQ_TRIG_MHZ
        || freq != target.freq || bw != target.bw || sf != target.sf || cr != target.cr)
      return false;
#if MESH_ENABLE_MESHTEXT_WIRE || MESH_ENABLE_ETHERNET_WIRE || MESH_ENABLE_HOST_KISS
    if (_physical_protocol != mesh::RadioProfileProtocol::MeshCore) return false;
#endif
#if MESH_ENABLE_TX_BEACONS
    if (isBeaconRadioOwned()) return false;
#endif
    return true;
#endif
  }
#if defined(MESH_ENABLE_LR2021_AUTO_XTRIM) && MESH_ENABLE_LR2021_AUTO_XTRIM
  mesh::PrimaryRadioApplyWait _frequency_apply_wait;
  int32_t _frequency_apply_key = INT32_MAX;
#endif

  float hardwareFrequencyMHz(float nominal_mhz) const {
#if defined(MESH_ENABLE_LR2021_AUTO_XTRIM) && MESH_ENABLE_LR2021_AUTO_XTRIM
    return mesh::lr2021::correctedFrequencyMHz(nominal_mhz,
        static_cast<const CustomLR2021*>(_radio)->frequencyCorrectionPpb());
#else
    return nominal_mhz;
#endif
  }

  int16_t recordApply(mesh::lr2021::ApplyStatus::Stage stage, int16_t code) {
    if (code != RADIOLIB_ERR_NONE) {
      MESH_DEBUG_PRINTLN("LR2021 apply %s failed (%d)",
                        mesh::lr2021::ApplyStatus::name(stage), (int)code);
    }
    return _apply_status.record(stage, code);
  }

public:
#if defined(MESH_W12_PRAM_EXPERIMENT) && MESH_W12_PRAM_EXPERIMENT && \
    defined(MESH_W12_RXPS_EXPERIMENT) && MESH_W12_RXPS_EXPERIMENT
  // The autonomous single-profile duty cycle is a separate experiment from
  // the dual-profile PRAM scanner, and must not be enabled by it implicitly.
  static constexpr bool SupportsRxPowerSaving = true;
  bool supportsRxPowerSaving() const override { return true; }
  uint32_t rxPowerSavingTransitionUs() const override { return 1000; }
  void onReceiveProcessed() override {
    if (!_lab_scan_active && !_lab_uncertain) finishReceiveProcessing();
  }
  mesh::RadioParamApplyResult trySetRxPowerSaving(bool enabled, uint32_t rx_us, uint32_t sleep_us) {
    if (_lab_scan_active || _lab_uncertain || _lab_resume_pending)
      return mesh::RadioParamApplyResult::BUSY;
    return RadioLibWrapper::trySetRxPowerSaving(enabled, rx_us, sleep_us);
  }
  bool setRxPowerSaving(bool enabled, uint32_t rx_us, uint32_t sleep_us) override {
    return trySetRxPowerSaving(enabled, rx_us, sleep_us) == mesh::RadioParamApplyResult::APPLIED;
  }
#endif
  uint8_t regulatorModes() const override { return static_cast<CustomLR2021*>(_radio)->regulatorModes(); }
  mesh::power::RegulatorMode defaultRegulatorMode() const override {
    return static_cast<CustomLR2021*>(_radio)->defaultRegulatorMode();
  }
  bool configureBootRegulator(mesh::power::RegulatorMode mode) override {
    return static_cast<CustomLR2021*>(_radio)->configureBootRegulator(mode);
  }
  mesh::power::RegulatorMode activeRegulatorMode() const override {
#if defined(MESH_W12_PRAM_EXPERIMENT) && MESH_W12_PRAM_EXPERIMENT
    if (_lab_session_ldo) return mesh::power::RegulatorMode::Ldo;
#endif
    return static_cast<CustomLR2021*>(_radio)->activeRegulatorMode();
  }
  ~CustomLR2021Wrapper() { unregisterInterruptAction(); }
  CustomLR2021Wrapper(CustomLR2021& radio, mesh::MainBoard& board) : RadioLibWrapper(radio, board) { }
  bool supportsMorseTx() const override { return true; }
  bool supportsMorseRx() const override { return MESH_ENABLE_MORSE_RX && supportsRadioDeepInit(); }
#if defined(MESH_ENABLE_LR2021_AUTO_XTRIM) && MESH_ENABLE_LR2021_AUTO_XTRIM
  bool lastPacketFrequency(mesh::lr2021::PacketFrequencyObservation& out) const override {
#if defined(MESH_W12_PRAM_EXPERIMENT) && MESH_W12_PRAM_EXPERIMENT
    if (_lab_scan_active) { out={}; return false; } // ROM changes frequency without updating RadioLib cache
#endif
    return static_cast<const CustomLR2021*>(_radio)->lastPacketFrequency(out);
  }

  bool getFrequencyHzForAutoTrim(uint32_t& out) const {
    out = 0;
    if (!_params_valid) return false;
    const float nominal_mhz = static_cast<const CustomLR2021*>(_radio)->getNominalFreqMHz();
    if (nominal_mhz != _cur_freq) return false;
    out = static_cast<uint32_t>(nominal_mhz * 1000000.0f);
    return true;
  }

  bool configureFrequencyCorrection(int32_t ppb) {
    return static_cast<CustomLR2021*>(_radio)->configureFrequencyCorrection(ppb);
  }
  int32_t frequencyCorrectionPpb() const {
    return static_cast<const CustomLR2021*>(_radio)->frequencyCorrectionPpb();
  }
  void cancelFrequencyCorrectionApply() {
    _frequency_apply_wait.clear();
    _frequency_apply_key = INT32_MAX;
  }
  mesh::RadioParamApplyResult trySetFrequencyCorrection(int32_t ppb) {
    using Result = mesh::RadioParamApplyResult;
    auto* radio = static_cast<CustomLR2021*>(_radio);
    if (!supportsXoscTrim() || !_params_valid || !mesh::lr2021::validFrequencyCorrection(ppb)) {
      cancelFrequencyCorrectionApply();
      return Result::FAILED;
    }
    const int32_t previous = radio->frequencyCorrectionPpb();
    if (previous == ppb && !_profile_repair_required) {
      cancelFrequencyCorrectionApply();
      return Result::APPLIED;
    }
    if (_frequency_apply_key != ppb) {
      cancelFrequencyCorrectionApply();
      _frequency_apply_key = ppb;
    }
    _apply_status.begin();
#if MESH_ENABLE_CARRIER_TOOLS
    if (_morse_owned) { noteReconfigureAdmission("morse-owned", 0); return Result::BUSY; }
#endif
#if MESH_ENABLE_MORSE_RX
    if (_morse_rx_owned) { noteReconfigureAdmission("morse-owned", 0); return Result::BUSY; }
#endif
    const bool previous_preempt = _primary_receive_preempt;
    const auto& current = _profiles.params(_active_profile);
    _primary_receive_preempt = _frequency_apply_wait.mayAbandonReceive(
        millis(), _profiles.primary, false, current, currentPreambleLength(),
        profilePreamble(0), MAX_TRANS_UNIT);
    const uint8_t resume = beginReconfigure();
    _primary_receive_preempt = previous_preempt;
    if (resume > 1) {
      if (resume != 2) cancelFrequencyCorrectionApply();
      return resume == 2 ? Result::BUSY : Result::FAILED;
    }
    radio->configureFrequencyCorrection(ppb);
    bool restored = applyParams(_cur_freq, _cur_bw, _cur_sf, _cur_cr);
#if MESH_ENABLE_MESHTEXT_WIRE || MESH_ENABLE_ETHERNET_WIRE || MESH_ENABLE_HOST_KISS
    restored = restored && applyProtocol(_physical_protocol);
#endif
    if (_dbm_valid) {
      _dbm_applied = restored && applyCachedTxPower(_cur_dbm) == RADIOLIB_ERR_NONE;
      restored = _dbm_applied && restored;
    }
    if (_rx_boosted_gain_valid) {
      _rx_boosted_gain_applied = restored && applyRxBoostedGainMode(_cur_rx_boosted_gain);
      restored = _rx_boosted_gain_applied && restored;
    }
    if (restored) {
      recalibrateNoiseFloor();
      _profile_repair_required = false;
      endReconfigure(true);
      if (isInRecvMode()) {
        _apply_status.applied();
        cancelFrequencyCorrectionApply();
        return Result::APPLIED;
      }
    }
    radio->configureFrequencyCorrection(previous);
    const bool rollback = restoreAfterDeepInit();
    if (rollback) endReconfigure(true);
    if (!rollback || !isInRecvMode()) _profile_refresh_required = _profile_repair_required = true;
    cancelFrequencyCorrectionApply();
    return Result::FAILED;
  }
#endif
  void formatApplyStatus(char* reply, size_t capacity) const {
#if defined(MESH_ENABLE_LR2021_AUTO_XTRIM) && MESH_ENABLE_LR2021_AUTO_XTRIM
    const auto* radio = static_cast<const CustomLR2021*>(_radio);
    snprintf(reply, capacity, "stage=%s code=%d state=%u repair=%u freq=%.6f programmed=%.6f",
             mesh::lr2021::ApplyStatus::name(_apply_status.stage),
             (int)_apply_status.code, (unsigned)state, (unsigned)_profile_repair_required,
             (double)radio->getNominalFreqMHz(), (double)radio->getFreqMHz());
#else
    snprintf(reply, capacity, "stage=%s code=%d state=%u repair=%u freq=%.6f",
             mesh::lr2021::ApplyStatus::name(_apply_status.stage),
             (int)_apply_status.code, (unsigned)state,
             (unsigned)_profile_repair_required,
             (double)static_cast<const CustomLR2021*>(_radio)->getFreqMHz());
#endif
  }

  mesh::RadioParamApplyResult trySetParams(float freq, float bw, uint8_t sf, uint8_t cr,
                                         const uint32_t* timings = nullptr) override {
    _apply_status.begin();
    const auto result = RadioLibWrapper::trySetParams(freq, bw, sf, cr, timings);
    if (result == mesh::RadioParamApplyResult::APPLIED) _apply_status.applied();
    return result;
  }

  bool supportsXoscTrim() const {
    return static_cast<const CustomLR2021*>(_radio)->supportsXoscTrim();
  }
  bool configureBootXoscTrim(bool enabled, uint8_t a, uint8_t b) {
    return static_cast<CustomLR2021*>(_radio)->configureBootXoscTrim(enabled, a, b);
  }
  bool getXoscTrim(uint8_t& a, uint8_t& b) const {
    return static_cast<const CustomLR2021*>(_radio)->getXoscTrim(a, b);
  }
  void formatFrequencyError(char* reply, size_t capacity) const {
    static_cast<const CustomLR2021*>(_radio)->formatFrequencyError(reply, capacity);
  }
  void cancelXoscTrimApply() {
    _trim_apply_wait.clear();
    _trim_apply_key = UINT16_MAX;
  }

  mesh::RadioParamApplyResult trySetXoscTrim(bool enabled, uint8_t a, uint8_t b) {
    using Result = mesh::RadioParamApplyResult;
    using Stage = mesh::lr2021::ApplyStatus::Stage;
    if (!supportsXoscTrim() || (enabled && !mesh::lr2021::validXoscTrim(a, b))) {
      cancelXoscTrimApply();
      return Result::FAILED;
    }
    auto* radio = static_cast<CustomLR2021*>(_radio);
    uint8_t old_a = 0, old_b = 0;
    const bool was_enabled = radio->getXoscTrim(old_a, old_b);
    if (enabled == was_enabled && (!enabled || (a == old_a && b == old_b))
        && !_profile_repair_required) {
      cancelXoscTrimApply();
      return Result::APPLIED;
    }

    const uint16_t key = enabled ? 0x1000U | (uint16_t(a) << 6) | b : 0;
    if (_trim_apply_key != key) {
      cancelXoscTrimApply();
      _trim_apply_key = key;
    }

    _apply_status.begin();
#if MESH_ENABLE_CARRIER_TOOLS
    if (_morse_owned) {
      noteReconfigureAdmission("morse-owned", 0);
      return Result::BUSY;
    }
#endif
#if MESH_ENABLE_MORSE_RX
    if (_morse_rx_owned) {
      noteReconfigureAdmission("morse-owned", 0);
      return Result::BUSY;
    }
#endif
    const bool previous_preempt = _primary_receive_preempt;
    const auto& current = _profiles.params(_active_profile);
    _primary_receive_preempt = _trim_apply_wait.mayAbandonReceive(
        millis(), _profiles.primary, false, current, currentPreambleLength(),
        profilePreamble(0), MAX_TRANS_UNIT);
    const uint8_t resume = beginReconfigure();
    _primary_receive_preempt = previous_preempt;
    if (resume > 1) {
      if (resume != 2) cancelXoscTrimApply();
      return resume == 2 ? Result::BUSY : Result::FAILED;
    }

    bool restored = false;
    if (!enabled) {
      // No register read exposes the factory trim. A real reset is required
      // to restore that default rather than guessing a capacitor code.
      radio->clearXoscTrim();
      restored = restoreAfterDeepInit();
      if (!restored) recordApply(Stage::Restore, RADIOLIB_ERR_UNKNOWN);
    } else if (recordApply(Stage::Trim, radio->applyXoscTrim(a, b)) == RADIOLIB_ERR_NONE) {
      restored = !_params_valid || applyParams(_cur_freq, _cur_bw, _cur_sf, _cur_cr);
#if MESH_ENABLE_MESHTEXT_WIRE || MESH_ENABLE_ETHERNET_WIRE
      restored = restored && applyProtocol(_physical_protocol);
#endif
      if (_dbm_valid) {
        _dbm_applied = restored && applyCachedTxPower(_cur_dbm) == RADIOLIB_ERR_NONE;
        restored = _dbm_applied && restored;
      }
      if (_rx_boosted_gain_valid) {
        _rx_boosted_gain_applied = restored && applyRxBoostedGainMode(_cur_rx_boosted_gain);
        restored = _rx_boosted_gain_applied && restored;
      }
      if (restored) recalibrateNoiseFloor();
    }

    if (restored) {
      _profile_repair_required = false;
      // This is a live command, so prove that the corrected oscillator can
      // receive even if an earlier failed RX start left software idle.
      endReconfigure(true);
      if (isInRecvMode()) {
        _apply_status.applied();
        cancelXoscTrimApply();
        return Result::APPLIED;
      }
    }

    // A failed command must restore both the retained selection and the old
    // receiver. The deep reset also restores native defaults after a failed
    // first override and repairs partial register writes.
    radio->configureBootXoscTrim(was_enabled, old_a, old_b);
    const bool rollback = restoreAfterDeepInit();
    if (rollback) endReconfigure(true);
    if (!rollback || !isInRecvMode())
      _profile_refresh_required = _profile_repair_required = true;
    cancelXoscTrimApply();
    return Result::FAILED;
  }
#if MESH_ENABLE_MESHTEXT_WIRE
  bool supportsMeshtext() const override { return true; }
#endif
#if MESH_ENABLE_HOST_KISS
  bool supportsHostKiss() const override { return true; }
#endif

  void powerOff() {
    if (isCarrierWaveActive()) setCarrierWave(0, 0);
    _radio->standby(); _radio->sleep();
  }

protected:
  bool supportsBoundedPrimaryReconfigure() const override { return true; }

  void noteReconfigureAdmission(const char* reason, int16_t status) override {
    using Stage = mesh::lr2021::ApplyStatus::Stage;
    _apply_status.begin();
    Stage stage = Stage::Admitted;
    if (strcmp(reason, "pending-irq") == 0) stage = Stage::Interrupt;
    else if (strcmp(reason, "transmitting") == 0) stage = Stage::Transmitting;
    else if (strcmp(reason, "chip-busy") == 0) stage = Stage::Busy;
    else if (strcmp(reason, "receiving") == 0) stage = Stage::Receiving;
    else if (strcmp(reason, "beacon-owned") == 0) stage = Stage::BeaconOwned;
    else if (strcmp(reason, "carrier-owned") == 0) stage = Stage::CarrierOwned;
    else if (strcmp(reason, "morse-owned") == 0) stage = Stage::MorseOwned;
    else if (strcmp(reason, "standby") == 0) stage = Stage::Standby;
    _apply_status.stage = stage;
    recordApply(stage, status);
  }

#if MESH_ENABLE_MESHTEXT_WIRE || MESH_ENABLE_HOST_KISS
  bool applyProtocol(mesh::RadioProfileProtocol protocol) override {
    if (protocol != mesh::RadioProfileProtocol::MeshCore
        && protocol != mesh::RadioProfileProtocol::Meshtext
#if MESH_ENABLE_HOST_KISS
        && protocol != mesh::RadioProfileProtocol::Kiss
#endif
        ) return false;
    const uint8_t sync = protocol == mesh::RadioProfileProtocol::Meshtext ? 0x2b : 0x12;
    return recordApply(mesh::lr2021::ApplyStatus::Stage::Protocol,
        static_cast<CustomLR2021*>(_radio)->setProfileProtocol(sync)) == RADIOLIB_ERR_NONE;
  }
#endif
  int16_t startMorseReceiver() override {
    return static_cast<CustomLR2021*>(_radio)->LR2021::startReceive(
        RADIOLIB_LR2021_RX_TIMEOUT_INF, 0, 0, 0);
  }
  float readMorseRssi() override {
    float rssi = NAN;
    return static_cast<LR2021*>(_radio)->getRssiInst(&rssi) == RADIOLIB_ERR_NONE ? rssi : NAN;
  }
  float morseReceiverBandwidthKHz() const override { return 4.8f; }
  int16_t prepareMorseReceiver(float frequency_mhz) override {
    if (!supportsRadioDeepInit()) return RADIOLIB_ERR_UNSUPPORTED;
    _morse_rx_fsk_mode = true;
    // W12's rail preparation checks DIO routing over SPI. Recover a failed
    // transceiver before asking it to quiesce controls for a band change.
    if (!prepareRadioHardReset()) return RADIOLIB_ERR_CHIP_NOT_FOUND;
    if (!_board->prepareRadioFrequency(hardwareFrequencyMHz(frequency_mhz))) return RADIOLIB_ERR_INVALID_FREQUENCY;
    const int16_t status = static_cast<CustomLR2021*>(_radio)->beginMorseReceive(frequency_mhz);
    const bool finished = _board->finishRadioHardReset();
    return status == RADIOLIB_ERR_NONE && !finished ? RADIOLIB_ERR_UNKNOWN : status;
  }
  bool validateMorseFrequency(float frequency_mhz) const override {
    return (frequency_mhz >= 150.0f && frequency_mhz <= 1090.0f)
        || (frequency_mhz >= 1900.0f && frequency_mhz <= 2500.0f);
  }
  int16_t prepareMorseCarrier(float frequency_mhz) override {
    const float hardware_frequency_mhz = hardwareFrequencyMHz(frequency_mhz);
    if (!_board->prepareRadioFrequency(hardware_frequency_mhz)) return RADIOLIB_ERR_INVALID_FREQUENCY;
    auto* radio = static_cast<CustomLR2021*>(_radio);
    const int16_t status = radio->setFrequency(frequency_mhz);
    // Re-evaluate the PA and board power limit at the Morse frequency, not
    // at the cached packet profile, when crossing LF/HF bands.
    return status == RADIOLIB_ERR_NONE ? radio->setOutputPower(
        mesh::clampLoRaTxPower(_dbm_valid ? _cur_dbm : LORA_TX_POWER, hardware_frequency_mhz), 304) : status;
  }
  int16_t enterMorseCarrier() override {
    return static_cast<CustomLR2021*>(_radio)->transmitMorseCarrier();
  }
  bool restoreCarrierWaveModem() override {
    if (_morse_rx_fsk_mode) {
      const bool restored = restoreAfterDeepInit();
      if (restored) _morse_rx_fsk_mode = false;
      return restored;
    }
    auto* radio = static_cast<CustomLR2021*>(_radio);
    const float hardware_frequency_mhz = hardwareFrequencyMHz(_cur_freq);
    if (!_board->prepareRadioFrequency(hardware_frequency_mhz)
        || radio->setFrequency(_cur_freq) != RADIOLIB_ERR_NONE) return false;
    return radio->setOutputPower(mesh::clampLoRaTxPower(
        _dbm_valid ? _cur_dbm : LORA_TX_POWER, hardware_frequency_mhz)) == RADIOLIB_ERR_NONE;
  }
  bool applyParams(float freq, float bw, uint8_t sf, uint8_t cr) override {
    using Stage = mesh::lr2021::ApplyStatus::Stage;
    CustomLR2021* radio = (CustomLR2021 *)_radio;
    const bool scan_target = scanTargetMatches(freq, bw, sf, cr);
    const bool reuse_tuple = scan_target && _scan_tuple_owned;
    if (!scan_target) invalidateScanTuple();
    const float hardware_frequency_mhz = hardwareFrequencyMHz(freq);
    if (!_board->prepareRadioFrequency(hardware_frequency_mhz)) {
      recordApply(Stage::FrequencyRail, RADIOLIB_ERR_UNKNOWN);
      return false;
    }
    const int8_t requested_power = _dbm_valid ? _cur_dbm : LORA_TX_POWER;
    const int8_t effective_power = mesh::clampLoRaTxPower(requested_power, hardware_frequency_mhz);
    // LR2021 selects its LF/HF PA from the cached frequency. Reapply power
    // immediately after every live retune so a band crossing cannot retain the
    // previous PA configuration.
    if (recordApply(Stage::Frequency, radio->setFrequency(freq)) != RADIOLIB_ERR_NONE
        || recordApply(Stage::Power, radio->setOutputPower(effective_power)) != RADIOLIB_ERR_NONE
        || ((!reuse_tuple || sf != _cur_sf)
            && recordApply(Stage::SpreadingFactor, radio->setSpreadingFactor(sf)) != RADIOLIB_ERR_NONE)
        || recordApply(Stage::Bandwidth, radio->setBandwidth(bw)) != RADIOLIB_ERR_NONE
        || ((!reuse_tuple || cr != _cur_cr)
            && recordApply(Stage::CodingRate, radio->setCodingRate(cr)) != RADIOLIB_ERR_NONE))
      return false;
    if (recordApply(Stage::Preamble,
        radio->setPreambleLength(preambleLengthForParams(sf, bw))) != RADIOLIB_ERR_NONE)
      return false;
    if (!applySideDetectorConfig(sf, bw)) return false;

    PacketMillis pm = calcMaxPacketMillis(sf, bw, cr, preambleLengthForParams(sf, bw));
    ((CustomLR2021 *)_radio)->setPreambleMillis(pm.preambleMillis);
    ((CustomLR2021 *)_radio)->setMaxPayloadMillis(pm.payloadMillis);
    _scan_tuple_applied = scan_target;
    return true;
  }

  int16_t applyCachedTxPower(int8_t dbm) override {
    // A TX-only excursion deliberately preserves the normal wrapper cache.
    // Clamp for the physically selected band, not that normal RX frequency.
    const float frequency = ((CustomLR2021 *)_radio)->getFreqMHz();
    return recordApply(mesh::lr2021::ApplyStatus::Stage::Power,
        _radio->setOutputPower(mesh::clampLoRaTxPower(dbm, frequency)));
  }

public:
  bool startSendRaw(const uint8_t* bytes, int len) override {
#if defined(MESH_W12_PRAM_EXPERIMENT) && MESH_W12_PRAM_EXPERIMENT
    if (_lab_scan_active || _lab_uncertain) return false;
#endif
    invalidateScanTuple();
    return RadioLibWrapper::startSendRaw(bytes, len);
  }

  bool setCodingRate(uint8_t cr) override {
#if defined(MESH_W12_PRAM_EXPERIMENT) && MESH_W12_PRAM_EXPERIMENT
    if (_lab_scan_active || _lab_uncertain) return false;
#endif
    invalidateScanTuple();
    if (isCarrierWaveActive()) return false;
    return ((CustomLR2021 *)_radio)->setCodingRate(cr) == RADIOLIB_ERR_NONE;
  }

  bool configSideDetectors(const uint8_t* sideDetSFs, uint8_t num, float bw) override {
    return tryConfigSideDetectors(sideDetSFs, num, bw) == mesh::RadioParamApplyResult::APPLIED;
  }

  mesh::RadioParamApplyResult tryConfigSideDetectors(const uint8_t* sideDetSFs, uint8_t num, float bw) override {
    invalidateScanTuple();
    using Result = mesh::RadioParamApplyResult;
    // While radio2 is active, retain valid primary detectors in RAM without
    // arming them on either profile. The public CLI still disallows changes.
    const bool stage_primary = _profiles.enabled() && num != 0;
    const uint8_t primary_sf = stage_primary ? _profiles.primary.sf : getSpreadingFactor();
    float active_bw = stage_primary ? _profiles.primary.bw
        : (_params_valid ? _cur_bw : ((CustomLR2021 *)_radio)->getBandwidthKhz());
    if (active_bw <= 0.0f) active_bw = bw;
    if (!mesh::lr2021::validateSideDetectorSFs(sideDetSFs, num, primary_sf, active_bw)) {
      return Result::FAILED;
    }

    if (stage_primary) {
      for (uint8_t i = 0; i < mesh::lr2021::MAX_SIDE_DETECTORS; i++) {
        _sideDetSFs[i] = i < num ? sideDetSFs[i] : 0;
      }
      _numSideDet = num;
      // A profile can be disabled before the scanner first runs. Force the
      // next profile application to install the newly cached detectors.
      _profile_refresh_required = true;
      return Result::APPLIED;
    }

    LR2021LoRaSideDetector_t tmp[mesh::lr2021::MAX_SIDE_DETECTORS] = {};
    buildSideDetectorConfig(sideDetSFs, num, active_bw, tmp);

    uint8_t resume_rx = beginReconfigure();
    if (resume_rx > 1) return resume_rx == 2 ? Result::BUSY : Result::FAILED;

    int16_t status = ((CustomLR2021 *)_radio)->setSideDetector(num > 0 ? tmp : nullptr, num);
    MESH_DEBUG_PRINTLN("setSideDetector() returned %d", status);

    if (status == RADIOLIB_ERR_NONE) {
      for (uint8_t i = 0; i < mesh::lr2021::MAX_SIDE_DETECTORS; i++) {
        _sideDetSFs[i] = i < num ? sideDetSFs[i] : 0;
      }
      _numSideDet = num;
    } else {
      // Setting side detectors writes the detector bytes and sync words in two
      // separate commands. If the second command fails, restore the cached
      // configuration so a rejected CLI change cannot leave partial hardware state.
      bool restored = applySideDetectorConfig(primary_sf, active_bw);
      if (!restored) restored = restoreAfterDeepInit();
      if (!restored) {
        MESH_DEBUG_PRINTLN("LR2021: failed to restore side detectors after config error");
      }
    }

    endReconfigure(resume_rx);
    return status == RADIOLIB_ERR_NONE ? Result::APPLIED : Result::FAILED;
  }

protected:
  static void buildSideDetectorConfig(const uint8_t* sideDetSFs, size_t num, float bw,
                                      LR2021LoRaSideDetector_t* config) {
    for (size_t i = 0; i < num; i++) {
      config[i].sf = sideDetSFs[i];
      config[i].ldro = mesh::lr2021::sideDetectorLDRO(sideDetSFs[i], bw);
      config[i].invertIQ = false;
      config[i].syncWord = RADIOLIB_LR2021_LORA_SYNC_WORD_PRIVATE;
    }
  }

  bool applySideDetectorConfig(uint8_t primary_sf, float bw) {
    using Stage = mesh::lr2021::ApplyStatus::Stage;
#if MESH_ENABLE_TX_BEACONS
    // Saved RX side detectors are not part of an independent TX-only tuple.
    // Disable them without changing their configuration, then restore the
    // normal set when the owner's receive-restoration transaction runs.
    if (isBeaconRadioOwned() && !_beacon_restoring) {
      return recordApply(Stage::SideDetectors,
          ((CustomLR2021 *)_radio)->setSideDetector(nullptr, 0)) == RADIOLIB_ERR_NONE;
    }
#endif
    if (_profiles.enabled()) {
      return recordApply(Stage::SideDetectors,
          ((CustomLR2021 *)_radio)->setSideDetector(nullptr, 0)) == RADIOLIB_ERR_NONE;
    }
    if (!mesh::lr2021::validateSideDetectorSFs(_sideDetSFs, _numSideDet, primary_sf, bw)) {
      // Extra SFs belong to the saved receive tuple. A temporary primary can
      // make that list illegal; suspend it without discarding the saved set.
      // Applying the normal tuple enables the same detectors again.
      if (_profiles.primary_temporary) {
        return recordApply(Stage::SideDetectors,
            ((CustomLR2021 *)_radio)->setSideDetector(nullptr, 0)) == RADIOLIB_ERR_NONE;
      }
      recordApply(Stage::SideDetectors, RADIOLIB_ERR_INVALID_SIDE_DETECT);
      return false;
    }
    LR2021LoRaSideDetector_t config[mesh::lr2021::MAX_SIDE_DETECTORS] = {};
    buildSideDetectorConfig(_sideDetSFs, _numSideDet, bw, config);
    return recordApply(Stage::SideDetectors,
        ((CustomLR2021 *)_radio)->setSideDetector(_numSideDet > 0 ? config : nullptr,
                                                _numSideDet)) == RADIOLIB_ERR_NONE;
  }

  int16_t performChannelScan() override {
    invalidateScanTuple();
    if (_numSideDet == 0 || _profiles.enabled()) return RadioLibWrapper::performChannelScan();

    CustomLR2021* radio = (CustomLR2021 *)_radio;
    const uint8_t rx_primary_sf = getSpreadingFactor();
    float bw = _params_valid ? _cur_bw : radio->getBandwidthKhz();
    if (bw <= 0.0f) bw = static_cast<float>(LORA_BW);
    // Incompatible saved detectors are suspended for TempRadio. CAD must scan
    // only the running primary, not the inactive detector list in the cache.
    if (_profiles.primary_temporary && !mesh::lr2021::validateSideDetectorSFs(
        _sideDetSFs, _numSideDet, rx_primary_sf, bw)) {
      return RadioLibWrapper::performChannelScan();
    }

    int16_t status = radio->standby();
    if (status != RADIOLIB_ERR_NONE) return status;

    // RadioLib's LR2021 multi-SF CAD support is unfinished and CAD requires
    // the inverse primary/side-SF ordering from RX. Scan every configured SF
    // through the supported primary detector instead, then restore RX atomically.
    status = radio->setSideDetector(nullptr, 0);
    if (status != RADIOLIB_ERR_NONE) {
      bool restored = applySideDetectorConfig(rx_primary_sf, bw);
      if (!restored) restored = restoreAfterDeepInit();
      return restored ? status : RADIOLIB_ERR_UNKNOWN;
    }

    uint8_t scan_sfs[mesh::lr2021::STORED_SIDE_DETECTOR_BYTES] = {};
    scan_sfs[0] = rx_primary_sf;
    for (size_t i = 0; i < _numSideDet; i++) scan_sfs[i + 1] = _sideDetSFs[i];

    int16_t scan_result = RADIOLIB_CHANNEL_FREE;
    for (size_t i = 0; i <= _numSideDet; i++) {
      if (i > 0) {
        status = radio->standby();
        if (status != RADIOLIB_ERR_NONE) {
          scan_result = status;
          break;
        }
      }
      status = radio->setSpreadingFactor(scan_sfs[i]);
      if (status != RADIOLIB_ERR_NONE) {
        scan_result = status;
        break;
      }
      scan_result = performChannelScanWithTimeout(
          cadScanTimeoutMillis(scan_sfs[i], bw));
      // Each individual CAD can finish inside the helper's one-second service
      // cadence while the combined multi-SF pass still exceeds it.
      _board->serviceWatchdog();
      if (scan_result != RADIOLIB_CHANNEL_FREE) break;
    }

    bool restored = radio->standby() == RADIOLIB_ERR_NONE
        && radio->setSpreadingFactor(rx_primary_sf) == RADIOLIB_ERR_NONE
        && applySideDetectorConfig(rx_primary_sf, bw);
    if (!restored && !restoreAfterDeepInit()) {
      MESH_DEBUG_PRINTLN("LR2021: failed to restore RX side detectors after CAD");
      return RADIOLIB_ERR_UNKNOWN;
    }
    return scan_result;
  }

public:
  bool isReceivingPacket() override {
    return ((CustomLR2021 *)_radio)->isReceiving();
  }

  bool isChipBusy() override {
    return ((CustomLR2021 *)_radio)->isChipBusy();
  }

  float getCurrentRSSI() override {
    float rssi = NAN;
    return ((CustomLR2021 *)_radio)->getRssiInst(&rssi) == RADIOLIB_ERR_NONE ? rssi : NAN;
  }

  void onSendFinished() override {
    RadioLibWrapper::onSendFinished();
    _radio->setPreambleLength(currentPreambleLength()); // overcomes weird issues with small and big pkts
  }

  uint8_t getSpreadingFactor() const override { return ((CustomLR2021 *)_radio)->getSpreadingFactor(); }

  void doResetAGC() override { }

  bool supportsRxBoostedGainMode() const override { return true; }
  bool getRxBoostedGainMode() const override {
    return ((CustomLR2021 *)_radio)->getRxBoostedGainMode();
  }

protected:
  int startReceiveMode() override {
    if (!_scan_retune) invalidateScanTuple();
    // LR2021 must leave CAD/RX before restarting with side detectors, or it
    // can reject startReceive() with RADIOLIB_ERR_SPI_CMD_INVALID (-706).
    const int16_t standby = recordApply(mesh::lr2021::ApplyStatus::Stage::Standby,
                                       _radio->standby());
    if (standby != RADIOLIB_ERR_NONE) return standby;
#if defined(MESH_W12_PRAM_EXPERIMENT) && MESH_W12_PRAM_EXPERIMENT && \
    defined(MESH_W12_RXPS_EXPERIMENT) && MESH_W12_RXPS_EXPERIMENT
    return recordApply(mesh::lr2021::ApplyStatus::Stage::Receive, startReceivePowerSaving());
#else
    return recordApply(mesh::lr2021::ApplyStatus::Stage::Receive, _radio->startReceive());
#endif
  }

#if defined(MESH_W12_PRAM_EXPERIMENT) && MESH_W12_PRAM_EXPERIMENT && \
    defined(MESH_W12_RXPS_EXPERIMENT) && MESH_W12_RXPS_EXPERIMENT
  int16_t armDutyCycle(RadioLibIrqFlags_t flags, RadioLibIrqFlags_t mask,
                       uint32_t* effective_rx_us, uint32_t* effective_sleep_us) override {
    mesh::w12pram::spiTrace().reset();
    _lab_busy_high=_lab_busy_low=_lab_busy_edges=0;
    _lab_busy_seen=false;
    *effective_rx_us = _rx_ps_rx_us;
    *effective_sleep_us = _rx_ps_sleep_us;
    return static_cast<CustomLR2021*>(_radio)->labStartRxDutyCycle(
        _rx_ps_rx_us, _rx_ps_sleep_us, flags, mask);
  }
  int16_t stopDutyCycleHardware() override {
    mesh::w12pram::spiTrace().active=false;
    return _radio->standby();
  }
#endif

  bool radioDeepInit() override {
    invalidateScanTuple();
    if (!prepareRadioHardReset()) return false;
    if (!_board->prepareRadioFrequency(LORA_FREQ)) return false;
    return ((CustomLR2021 *)_radio)->std_init() && _board->finishRadioHardReset();
  }
  bool supportsRadioDeepInit() const override { return supportsRadioHardResetPath(); }

  bool applyRxBoostedGainMode(bool en) override {
    invalidateScanTuple();
    return recordApply(mesh::lr2021::ApplyStatus::Stage::Gain,
        ((CustomLR2021 *)_radio)->setRxBoostedGainMode(en ? LR2021_RX_BOOST_LEVEL : 0))
        == RADIOLIB_ERR_NONE;
  }

  uint8_t _sideDetSFs[mesh::lr2021::MAX_SIDE_DETECTORS] = {};
  size_t _numSideDet = 0;

};
