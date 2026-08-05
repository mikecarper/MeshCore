#pragma once

#include "CustomLR2021.h"
#include "RadioLibWrappers.h"

#ifndef USE_LR2021
#define USE_LR2021
#endif

#ifndef LR2021_RX_BOOST_LEVEL
#define LR2021_RX_BOOST_LEVEL 7
#endif

class CustomLR2021Wrapper : public RadioLibWrapper {
public:
  CustomLR2021Wrapper(CustomLR2021& radio, mesh::MainBoard& board) : RadioLibWrapper(radio, board) { }

  void powerOff() { _radio->standby(); _radio->sleep(); }

protected:
  bool applyParams(float freq, float bw, uint8_t sf, uint8_t cr) override {
    return ((CustomLR2021 *)_radio)->setFrequency(freq) == RADIOLIB_ERR_NONE
        && ((CustomLR2021 *)_radio)->setSpreadingFactor(sf) == RADIOLIB_ERR_NONE
        && ((CustomLR2021 *)_radio)->setBandwidth(bw) == RADIOLIB_ERR_NONE
        && ((CustomLR2021 *)_radio)->setCodingRate(cr) == RADIOLIB_ERR_NONE
        && updatePreamble(sf)
        && applySideDetectorConfig();
  }

public:
  bool setCodingRate(uint8_t cr) override {
    return ((CustomLR2021 *)_radio)->setCodingRate(cr) == RADIOLIB_ERR_NONE;
  }

  bool configSideDetectors(const uint8_t* sideDetSFs, uint8_t num, float bw) override {
    if (num > 3 || (num > 0 && sideDetSFs == nullptr)) return false;

    LR2021LoRaSideDetector_t tmp[3];
    uint8_t sf = getSpreadingFactor();

    if (sf >= 10 && num > 1) { return false; }  // only 1 side detector allowed when primary SF >= 10
    for (int i = 0; i < num; i++) {
      if (sideDetSFs[i] > 12 || sideDetSFs[i] < 5) { return false; }  // must be valid SF
      if (sideDetSFs[i] <= sf) { return false; }  // must be greater than the primary SF
      if (sideDetSFs[i] > sf + 4) { return false; }  // span must not be > 4

      tmp[i].sf = sideDetSFs[i];
      float tSym = calcTsym(tmp[i].sf, bw);
      if (tSym >= 16.0f) {
        tmp[i].ldro = true;
      } else {
        tmp[i].ldro = false;
      }
      tmp[i].invertIQ = false;
      tmp[i].syncWord = RADIOLIB_LR2021_LORA_SYNC_WORD_PRIVATE;
    }

    uint8_t resume_rx = beginReconfigure();
    if (resume_rx > 1) return false;

    int16_t status = ((CustomLR2021 *)_radio)->setSideDetector(tmp, num);
    MESH_DEBUG_PRINTLN("setSideDetector() returned %d", status);

    if (status == RADIOLIB_ERR_NONE) {
      for (int i = 0; i < num; i++) { _sideDet[i] = tmp[i]; }
      _numSideDet = num;
    }

    endReconfigure(resume_rx);
    return status == RADIOLIB_ERR_NONE;
  }

protected:
  bool applySideDetectorConfig() {
    return ((CustomLR2021 *)_radio)->setSideDetector(_sideDet, _numSideDet)
        == RADIOLIB_ERR_NONE;
  }

  float calcTsym(uint8_t sf, float bw) {
    return (float)(uint32_t(1) << sf) / bw;
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
    _radio->setPreambleLength(preambleLengthForSF(getSpreadingFactor())); // overcomes weird issues with small and big pkts
  }

  uint8_t getSpreadingFactor() const override { return ((CustomLR2021 *)_radio)->getSpreadingFactor(); }

  void doResetAGC() override { }

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
    return ((CustomLR2021 *)_radio)->std_init();
  }
  bool supportsRadioDeepInit() const override { return true; }

  bool applyRxBoostedGainMode(bool en) override {
    return ((CustomLR2021 *)_radio)->setRxBoostedGainMode(en ? LR2021_RX_BOOST_LEVEL : 0)
        == RADIOLIB_ERR_NONE;
  }

  LR2021LoRaSideDetector_t _sideDet[3];
  size_t _numSideDet = 0;

};
