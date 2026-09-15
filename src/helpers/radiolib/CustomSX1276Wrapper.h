#pragma once

#include "CustomSX1276.h"
#include "RadioLibWrappers.h"

#ifndef USE_SX1276
#define USE_SX1276
#endif

class CustomSX1276Wrapper : public RadioLibWrapper {
public:
  CustomSX1276Wrapper(CustomSX1276& radio, mesh::MainBoard& board) : RadioLibWrapper(radio, board) { }
  bool supportsCarrierWave() const override { return supportsRadioDeepInit(); }

  void powerOff() {
    if (isCarrierWaveActive()) setCarrierWave(0, 0);
    _radio->standby(); _radio->sleep();
  }

protected:
  bool applyParams(float freq, float bw, uint8_t sf, uint8_t cr) override {
    return ((CustomSX1276 *)_radio)->setFrequency(freq) == RADIOLIB_ERR_NONE
        && ((CustomSX1276 *)_radio)->setSpreadingFactor(sf) == RADIOLIB_ERR_NONE
        && ((CustomSX1276 *)_radio)->setBandwidth(bw) == RADIOLIB_ERR_NONE
        && ((CustomSX1276 *)_radio)->setCodingRate(cr) == RADIOLIB_ERR_NONE
        && updatePreamble(sf, bw);
  }

public:
  bool setCodingRate(uint8_t cr) override {
    if (isCarrierWaveActive()) return false;
    return ((CustomSX1276 *)_radio)->setCodingRate(cr) == RADIOLIB_ERR_NONE;
  }

  bool isReceivingPacket() override { 
    return ((CustomSX1276 *)_radio)->isReceiving();
  }
  float getCurrentRSSI() override {
    return ((CustomSX1276 *)_radio)->getRSSI(false);
  }
  float packetScore(float snr, int packet_len) override {
    int sf = ((CustomSX1276 *)_radio)->spreadingFactor;
    return packetScoreInt(snr, sf, packet_len);
  }
  uint8_t getSpreadingFactor() const override { return ((CustomSX1276 *)_radio)->spreadingFactor; }

protected:
  int16_t enterCarrierWave() override {
    auto* radio = static_cast<CustomSX1276*>(_radio);
    // Direct TX needs FSK. Zero deviation prevents a floating DIO2 data input
    // from moving the carrier. Start at the PA_BOOST floor, while still idle;
    // user-facing power may be an external PA level, not chip drive power.
    int16_t status = radio->beginFSK(_cur_freq, 4.8, 0.0, 125.0, 2, 16, false);
    if (status != RADIOLIB_ERR_NONE) return status;
#ifdef SX127X_CURRENT_LIMIT
    status = radio->setCurrentLimit(SX127X_CURRENT_LIMIT);
    if (status != RADIOLIB_ERR_NONE) return status;
#endif
    status = applyCachedTxPower(_dbm_valid ? _cur_dbm : LORA_TX_POWER);
    if (status != RADIOLIB_ERR_NONE) return status;
    return radio->transmitDirect();
  }

  bool restoreCarrierWaveModem() override {
    // Restores LoRa, runtime profile/preamble, IRQ mapping and PA calibration.
    // Shared cleanup retains the stop/retry guard if any restoration fails.
    return restoreAfterDeepInit();
  }

  int16_t performChannelScan() override {
    return ((CustomSX1276 *)_radio)->tryScanChannel(cadScanTimeoutMillis(), *_board);
  }

  bool radioDeepInit() override {
    if (!prepareRadioHardReset()) return false;
    return ((CustomSX1276 *)_radio)->std_init() && _board->finishRadioHardReset();
  }
  bool supportsRadioDeepInit() const override { return supportsRadioHardResetPath(); }
};
