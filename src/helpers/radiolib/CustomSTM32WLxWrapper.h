#pragma once

#include "CustomSTM32WLx.h"
#include "RadioLibWrappers.h"
#include "SX126xReset.h"
#include <math.h>

class CustomSTM32WLxWrapper : public RadioLibWrapper {
public:
  CustomSTM32WLxWrapper(CustomSTM32WLx& radio, mesh::MainBoard& board) : RadioLibWrapper(radio, board) { }

protected:
  bool applyParams(float freq, float bw, uint8_t sf, uint8_t cr) override {
    return ((CustomSTM32WLx *)_radio)->setFrequency(freq) == RADIOLIB_ERR_NONE
        && ((CustomSTM32WLx *)_radio)->setSpreadingFactor(sf) == RADIOLIB_ERR_NONE
        && ((CustomSTM32WLx *)_radio)->setBandwidth(bw) == RADIOLIB_ERR_NONE
        && ((CustomSTM32WLx *)_radio)->setCodingRate(cr) == RADIOLIB_ERR_NONE
        && updatePreamble(sf);
  }

public:
  bool setCodingRate(uint8_t cr) override {
    return ((CustomSTM32WLx *)_radio)->setCodingRate(cr) == RADIOLIB_ERR_NONE;
  }

  bool isReceivingPacket() override { 
    return ((CustomSTM32WLx *)_radio)->isReceiving();
  }
  float getCurrentRSSI() override {
    return ((CustomSTM32WLx *)_radio)->getRSSI(false);
  }
  float packetScore(float snr, int packet_len) override {
    int sf = ((CustomSTM32WLx *)_radio)->spreadingFactor;
    return packetScoreInt(snr, sf, packet_len);
  }
  uint8_t getSpreadingFactor() const override { return ((CustomSTM32WLx *)_radio)->spreadingFactor; }

  void doResetAGC() override { sx126xResetAGC((SX126x *)_radio, getRxBoostedGainMode()); }
};
