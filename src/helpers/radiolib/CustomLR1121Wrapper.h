#pragma once

#include "CustomLR1121.h"
#include "RadioLibWrappers.h"
#include "LR11x0Reset.h"

class CustomLR1121Wrapper : public RadioLibWrapper {
public:
  CustomLR1121Wrapper(CustomLR1121& radio, mesh::MainBoard& board) : RadioLibWrapper(radio, board) { }

  void powerOff() {
    _radio->standby();
    _radio->sleep();
  }

protected:
  bool applyParams(float freq, float bw, uint8_t sf, uint8_t cr) override {
    if (((CustomLR1121 *)_radio)->setFrequency(freq) != RADIOLIB_ERR_NONE
        || ((CustomLR1121 *)_radio)->setSpreadingFactor(sf) != RADIOLIB_ERR_NONE
        || ((CustomLR1121 *)_radio)->setBandwidth(bw) != RADIOLIB_ERR_NONE
        || ((CustomLR1121 *)_radio)->setCodingRate(cr) != RADIOLIB_ERR_NONE
        || !updatePreamble(sf, bw)) return false;
    PacketMillis pm = calcMaxPacketMillis(sf, bw, cr, preambleLengthForParams(sf, bw));
    ((CustomLR1121 *)_radio)->setPreambleMillis(pm.preambleMillis);
    ((CustomLR1121 *)_radio)->setMaxPayloadMillis(pm.payloadMillis);
    return true;
  }

public:
  bool setCodingRate(uint8_t cr) override {
    if (isCarrierWaveActive()) return false;
    return ((CustomLR1121 *)_radio)->setCodingRate(cr) == RADIOLIB_ERR_NONE;
  }

  void doResetAGC() override { lr11x0ResetAGC((LR11x0 *)_radio, ((CustomLR1121 *)_radio)->getFreqMHz(), getRxBoostedGainMode()); }
  bool isReceivingPacket() override {
    return ((CustomLR1121 *)_radio)->isReceiving();
  }
  float getCurrentRSSI() override {
    float rssi = -110;
    ((CustomLR1121 *)_radio)->getRssiInst(&rssi);
    return rssi;
  }

  void onSendFinished() override {
    RadioLibWrapper::onSendFinished();
    _radio->setPreambleLength(currentPreambleLength()); // overcomes weird issues with small and big pkts
  }

  uint32_t getEstAirtimeFor(int len_bytes) override {
    auto airtime = RadioLibWrapper::getEstAirtimeFor(len_bytes);
    return airtime < 200 ? 200 : airtime;   // at least 200 millis
  }

  uint8_t getSpreadingFactor() const override { return ((CustomLR1121 *)_radio)->getSpreadingFactor(); }

protected:
  bool applyRxBoostedGainMode(bool en) override {
    return ((CustomLR1121 *)_radio)->setRxBoostedGainMode(en) == RADIOLIB_ERR_NONE;
  }
public:
  bool supportsRxBoostedGainMode() const override { return true; }
  bool getRxBoostedGainMode() const override {
    return ((CustomLR1121 *)_radio)->getRxBoostedGainMode();
  }
};
