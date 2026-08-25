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
  CustomLR2021Wrapper(CustomLR2021& radio, mesh::MainBoard& board) : RadioLibWrapper(radio, board) { }

  void powerOff() { _radio->standby(); _radio->sleep(); }

protected:
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
    const float frequency = _params_valid
        ? _cur_freq : ((CustomLR2021 *)_radio)->getFreqMHz();
    return _radio->setOutputPower(mesh::clampLoRaTxPower(dbm, frequency));
  }

public:
  bool setCodingRate(uint8_t cr) override {
    return ((CustomLR2021 *)_radio)->setCodingRate(cr) == RADIOLIB_ERR_NONE;
  }

  bool configSideDetectors(const uint8_t* sideDetSFs, uint8_t num, float bw) override {
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
    if (!mesh::lr2021::validateSideDetectorSFs(_sideDetSFs, _numSideDet, primary_sf, bw)) {
      return false;
    }
    LR2021LoRaSideDetector_t config[mesh::lr2021::MAX_SIDE_DETECTORS] = {};
    buildSideDetectorConfig(_sideDetSFs, _numSideDet, bw, config);
    return ((CustomLR2021 *)_radio)->setSideDetector(_numSideDet > 0 ? config : nullptr,
                                                     _numSideDet) == RADIOLIB_ERR_NONE;
  }

  int16_t performChannelScan() override {
    if (_numSideDet == 0) return RadioLibWrapper::performChannelScan();

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
          mesh::calculateCadScanTimeoutMillis(scan_sfs[i], bw));
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
