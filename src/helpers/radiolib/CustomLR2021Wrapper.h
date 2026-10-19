#pragma once

#include "CustomLR2021.h"
#include "LR2021SideDetectorConfig.h"
#include "RadioLibWrappers.h"

#ifndef USE_LR2021
#define USE_LR2021
#endif

#include "RadioPowerLimits.h"

#ifndef LR2021_RX_BOOST_LEVEL
#define LR2021_RX_BOOST_LEVEL 7
#endif

class CustomLR2021Wrapper : public RadioLibWrapper {
public:
  uint8_t regulatorModes() const override { return static_cast<CustomLR2021*>(_radio)->regulatorModes(); }
  mesh::power::RegulatorMode defaultRegulatorMode() const override {
    return static_cast<CustomLR2021*>(_radio)->defaultRegulatorMode();
  }
  bool configureBootRegulator(mesh::power::RegulatorMode mode) override {
    return static_cast<CustomLR2021*>(_radio)->configureBootRegulator(mode);
  }
  mesh::power::RegulatorMode activeRegulatorMode() const override {
    return static_cast<CustomLR2021*>(_radio)->activeRegulatorMode();
  }
  ~CustomLR2021Wrapper() { unregisterInterruptAction(); }
  CustomLR2021Wrapper(CustomLR2021& radio, mesh::MainBoard& board) : RadioLibWrapper(radio, board) { }
  bool supportsMorseTx() const override { return true; }
  bool supportsMorseRx() const override { return MESH_ENABLE_MORSE_RX && supportsRadioDeepInit(); }
#if MESH_ENABLE_MESHTEXT_WIRE
  bool supportsMeshtext() const override { return true; }
#endif

  void powerOff() {
    if (isCarrierWaveActive()) setCarrierWave(0, 0);
    _radio->standby(); _radio->sleep();
  }

protected:
#if MESH_ENABLE_MESHTEXT_WIRE
  bool applyProtocol(mesh::RadioProfileProtocol protocol) override {
    if (protocol != mesh::RadioProfileProtocol::MeshCore
        && protocol != mesh::RadioProfileProtocol::Meshtext) return false;
    const uint8_t sync = protocol == mesh::RadioProfileProtocol::Meshtext ? 0x2b : 0x12;
    return static_cast<CustomLR2021*>(_radio)->setProfileProtocol(sync) == RADIOLIB_ERR_NONE;
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
    if (!_board->prepareRadioFrequency(frequency_mhz)) return RADIOLIB_ERR_INVALID_FREQUENCY;
    _morse_rx_fsk_mode = true;
    if (!prepareRadioHardReset()) return RADIOLIB_ERR_CHIP_NOT_FOUND;
    const int16_t status = static_cast<CustomLR2021*>(_radio)->beginMorseReceive(frequency_mhz);
    const bool finished = _board->finishRadioHardReset();
    return status == RADIOLIB_ERR_NONE && !finished ? RADIOLIB_ERR_UNKNOWN : status;
  }
  bool validateMorseFrequency(float frequency_mhz) const override {
    return (frequency_mhz >= 150.0f && frequency_mhz <= 1090.0f)
        || (frequency_mhz >= 1900.0f && frequency_mhz <= 2500.0f);
  }
  int16_t prepareMorseCarrier(float frequency_mhz) override {
    if (!_board->prepareRadioFrequency(frequency_mhz)) return RADIOLIB_ERR_INVALID_FREQUENCY;
    auto* radio = static_cast<CustomLR2021*>(_radio);
    const int16_t status = radio->setFrequency(frequency_mhz);
    // Re-evaluate the PA and board power limit at the Morse frequency, not
    // at the cached packet profile, when crossing LF/HF bands.
    return status == RADIOLIB_ERR_NONE ? radio->setOutputPower(
        mesh::clampLoRaTxPower(_dbm_valid ? _cur_dbm : LORA_TX_POWER, frequency_mhz), 304) : status;
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
    if (!_board->prepareRadioFrequency(_cur_freq)
        || radio->setFrequency(_cur_freq) != RADIOLIB_ERR_NONE) return false;
    return radio->setOutputPower(mesh::clampLoRaTxPower(
        _dbm_valid ? _cur_dbm : LORA_TX_POWER, _cur_freq)) == RADIOLIB_ERR_NONE;
  }
  bool applyParams(float freq, float bw, uint8_t sf, uint8_t cr) override {
    CustomLR2021* radio = (CustomLR2021 *)_radio;
    if (!_board->prepareRadioFrequency(freq)) return false;
    const int8_t requested_power = _dbm_valid ? _cur_dbm : LORA_TX_POWER;
    const int8_t effective_power = mesh::clampLoRaTxPower(requested_power, freq);
    // LR2021 selects its LF/HF PA from the cached frequency. Reapply power
    // immediately after every live retune so a band crossing cannot retain the
    // previous PA configuration.
    bool success = radio->setFrequency(freq) == RADIOLIB_ERR_NONE
        && radio->setOutputPower(effective_power) == RADIOLIB_ERR_NONE
        && radio->setSpreadingFactor(sf) == RADIOLIB_ERR_NONE
        && radio->setBandwidth(bw) == RADIOLIB_ERR_NONE
        && radio->setCodingRate(cr) == RADIOLIB_ERR_NONE
        && updatePreamble(sf, bw)
        && applySideDetectorConfig(sf, bw);
    if (!success) return false;

    PacketMillis pm = calcMaxPacketMillis(sf, bw, cr, preambleLengthForParams(sf, bw));
    ((CustomLR2021 *)_radio)->setPreambleMillis(pm.preambleMillis);
    ((CustomLR2021 *)_radio)->setMaxPayloadMillis(pm.payloadMillis);
    return true;
  }

  int16_t applyCachedTxPower(int8_t dbm) override {
    // A TX-only excursion deliberately preserves the normal wrapper cache.
    // Clamp for the physically selected band, not that normal RX frequency.
    const float frequency = ((CustomLR2021 *)_radio)->getFreqMHz();
    return _radio->setOutputPower(mesh::clampLoRaTxPower(dbm, frequency));
  }

public:
  bool setCodingRate(uint8_t cr) override {
    if (isCarrierWaveActive()) return false;
    return ((CustomLR2021 *)_radio)->setCodingRate(cr) == RADIOLIB_ERR_NONE;
  }

  bool configSideDetectors(const uint8_t* sideDetSFs, uint8_t num, float bw) override {
    if (_profiles.enabled()) return false;
    const uint8_t primary_sf = getSpreadingFactor();
    float active_bw = _params_valid ? _cur_bw : ((CustomLR2021 *)_radio)->getBandwidthKhz();
    if (active_bw <= 0.0f) active_bw = bw;
    if (!mesh::lr2021::validateSideDetectorSFs(sideDetSFs, num, primary_sf, active_bw)) {
      return false;
    }

    LR2021LoRaSideDetector_t tmp[mesh::lr2021::MAX_SIDE_DETECTORS] = {};
    buildSideDetectorConfig(sideDetSFs, num, active_bw, tmp);

    uint8_t resume_rx = beginReconfigure();
    if (resume_rx > 1) return false;

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
    return status == RADIOLIB_ERR_NONE;
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
#if MESH_ENABLE_TX_BEACONS
    // Saved RX side detectors are not part of an independent TX-only tuple.
    // Disable them without changing their configuration, then restore the
    // normal set when the owner's receive-restoration transaction runs.
    if (isBeaconRadioOwned() && !_beacon_restoring) {
      return ((CustomLR2021 *)_radio)->setSideDetector(nullptr, 0) == RADIOLIB_ERR_NONE;
    }
#endif
    if (_profiles.enabled()) {
      return ((CustomLR2021 *)_radio)->setSideDetector(nullptr, 0) == RADIOLIB_ERR_NONE;
    }
    if (!mesh::lr2021::validateSideDetectorSFs(_sideDetSFs, _numSideDet, primary_sf, bw)) {
      return false;
    }
    LR2021LoRaSideDetector_t config[mesh::lr2021::MAX_SIDE_DETECTORS] = {};
    buildSideDetectorConfig(_sideDetSFs, _numSideDet, bw, config);
    return ((CustomLR2021 *)_radio)->setSideDetector(_numSideDet > 0 ? config : nullptr,
                                                     _numSideDet) == RADIOLIB_ERR_NONE;
  }

  int16_t performChannelScan() override {
    if (_numSideDet == 0 || _profiles.enabled()) return RadioLibWrapper::performChannelScan();

    CustomLR2021* radio = (CustomLR2021 *)_radio;
    const uint8_t rx_primary_sf = getSpreadingFactor();
    float bw = _params_valid ? _cur_bw : radio->getBandwidthKhz();
    if (bw <= 0.0f) bw = static_cast<float>(LORA_BW);

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
    float rssi = -110;
    ((CustomLR2021 *)_radio)->getRssiInst(&rssi);
    return rssi;
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
    // LR2021 must leave CAD/RX before restarting with side detectors, or it
    // can reject startReceive() with RADIOLIB_ERR_SPI_CMD_INVALID (-706).
    _radio->standby();
    return _radio->startReceive();
  }

  bool radioDeepInit() override {
    if (!prepareRadioHardReset()) return false;
    if (!_board->prepareRadioFrequency(LORA_FREQ)) return false;
    return ((CustomLR2021 *)_radio)->std_init() && _board->finishRadioHardReset();
  }
  bool supportsRadioDeepInit() const override { return supportsRadioHardResetPath(); }

  bool applyRxBoostedGainMode(bool en) override {
    return ((CustomLR2021 *)_radio)->setRxBoostedGainMode(en ? LR2021_RX_BOOST_LEVEL : 0)
        == RADIOLIB_ERR_NONE;
  }

  uint8_t _sideDetSFs[mesh::lr2021::MAX_SIDE_DETECTORS] = {};
  size_t _numSideDet = 0;

};
