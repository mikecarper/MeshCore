#pragma once
#include "SX126xReceiveMode.h"

#include "CustomLLCC68.h"
#include "RadioLibWrappers.h"
#include "SX126xReset.h"
#include "SX126xMorseRamp.h"
#include "SX126xMorseReceiver.h"

class CustomLLCC68Wrapper : public RadioLibWrapper {
protected:
  int8_t readReceiveMode() override { return sx126xReceiveMode((SX126x*)_radio); }

public:
  ~CustomLLCC68Wrapper() { unregisterInterruptAction(); }
  CustomLLCC68Wrapper(CustomLLCC68& radio, mesh::MainBoard& board) : RadioLibWrapper(radio, board) { }
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
    return static_cast<CustomLLCC68*>(_radio)->setProfileProtocol(sync) == RADIOLIB_ERR_NONE;
  }
#endif
  int16_t prepareMorseReceiver(float frequency_mhz) override {
    if (!_board->prepareRadioFrequency(frequency_mhz)) return RADIOLIB_ERR_INVALID_FREQUENCY;
    _morse_rx_fsk_mode = true;
    if (!prepareRadioHardReset()) return RADIOLIB_ERR_UNKNOWN;
    const int16_t status = prepareSX126xMorseReceiver(static_cast<CustomLLCC68*>(_radio), frequency_mhz);
    return status == RADIOLIB_ERR_NONE && !_board->finishRadioHardReset()
        ? RADIOLIB_ERR_UNKNOWN : status;
  }
  float readMorseRssi() override { return static_cast<CustomLLCC68*>(_radio)->getMorseRssi(); }
  float morseReceiverBandwidthKHz() const override { return 4.8f; }
  int16_t prepareMorseCarrier(float frequency_mhz) override {
    const int16_t status = RadioLibWrapper::prepareMorseCarrier(frequency_mhz);
    return status == RADIOLIB_ERR_NONE
        ? setSX126xMorseRamp(static_cast<CustomLLCC68*>(_radio)) : status;
  }
  int16_t leaveMorseCarrier() override {
    return leaveSX126xMorseCarrier(static_cast<CustomLLCC68*>(_radio));
  }
  bool restoreCarrierWaveModem() override {
    return restoreSX126xPacketRamp(static_cast<CustomLLCC68*>(_radio)) == RADIOLIB_ERR_NONE
        && RadioLibWrapper::restoreCarrierWaveModem();
  }
  bool applyParams(float freq, float bw, uint8_t sf, uint8_t cr) override {
    bool success = ((CustomLLCC68 *)_radio)->setFrequency(freq) == RADIOLIB_ERR_NONE
        && ((CustomLLCC68 *)_radio)->setSpreadingFactor(sf) == RADIOLIB_ERR_NONE
        && ((CustomLLCC68 *)_radio)->setBandwidth(bw) == RADIOLIB_ERR_NONE
        && ((CustomLLCC68 *)_radio)->setCodingRate(cr) == RADIOLIB_ERR_NONE
        && updatePreamble(sf, bw);
    if (!success) return false;

    PacketMillis pm = calcMaxPacketMillis(sf, bw, cr, preambleLengthForParams(sf, bw));
    ((CustomLLCC68 *)_radio)->setPreambleMillis(pm.preambleMillis);
    ((CustomLLCC68 *)_radio)->setMaxPayloadMillis(pm.payloadMillis);
    return true;
  }

public:
  bool setCodingRate(uint8_t cr) override {
    if (isCarrierWaveActive()) return false;
    return ((CustomLLCC68 *)_radio)->setCodingRate(cr) == RADIOLIB_ERR_NONE;
  }

  bool isReceivingPacket() override { 
    return ((CustomLLCC68 *)_radio)->isReceiving();
  }
  float getCurrentRSSI() override {
    return ((CustomLLCC68 *)_radio)->getRSSI(false);
  }
  float packetScore(float snr, int packet_len) override {
    int sf = ((CustomLLCC68 *)_radio)->spreadingFactor;
    return packetScoreInt(snr, sf, packet_len);
  }
  uint8_t getSpreadingFactor() const override { return ((CustomLLCC68 *)_radio)->spreadingFactor; }

  void doResetAGC() override { sx126xResetAGC((SX126x *)_radio, getRxBoostedGainMode()); }

protected:
  bool radioDeepInit() override {
    if (!prepareRadioHardReset()) return false;
    return ((CustomLLCC68 *)_radio)->std_init() && _board->finishRadioHardReset();
  }
  bool supportsRadioDeepInit() const override { return supportsRadioHardResetPath(); }

protected:
  bool applyRxBoostedGainMode(bool en) override {
    return ((CustomLLCC68 *)_radio)->setRxBoostedGainMode(en) == RADIOLIB_ERR_NONE;
  }
public:
  bool supportsRxBoostedGainMode() const override { return true; }
  bool getRxBoostedGainMode() const override {
    return ((CustomLLCC68 *)_radio)->getRxBoostedGainMode();
  }
};
