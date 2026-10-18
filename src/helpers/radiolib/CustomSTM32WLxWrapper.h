#pragma once
#include "SX126xReceiveMode.h"

#include "CustomSTM32WLx.h"
#include "RadioLibWrappers.h"
#include "SX126xReset.h"
#if MESH_ENABLE_CARRIER_TOOLS
#include "SX126xMorseRamp.h"
#endif
#if MESH_ENABLE_MORSE_RX
#include "SX126xMorseReceiver.h"
#endif
#include <math.h>

class CustomSTM32WLxWrapper : public RadioLibWrapper {
  using DeepInitCallback = bool (*)();
  DeepInitCallback _deep_init = nullptr;
protected:
  int8_t readReceiveMode() override { return sx126xReceiveMode((SX126x*)_radio); }

public:
  ~CustomSTM32WLxWrapper() { unregisterInterruptAction(); }
  CustomSTM32WLxWrapper(CustomSTM32WLx& radio, mesh::MainBoard& board) : RadioLibWrapper(radio, board) { }
#if MESH_ENABLE_CARRIER_TOOLS
  bool supportsMorseTx() const override { return true; }
#endif
#if MESH_ENABLE_MESHTEXT_WIRE
  bool supportsMeshtext() const override { return true; }
#endif
#if MESH_ENABLE_MORSE_RX
  bool supportsMorseRx() const override { return supportsRadioDeepInit(); }
#endif
  void setDeepInitCallback(DeepInitCallback callback) { _deep_init = callback; }

protected:
#if MESH_ENABLE_MESHTEXT_WIRE
  bool applyProtocol(mesh::RadioProfileProtocol protocol) override {
    uint8_t sync;
    if (protocol == mesh::RadioProfileProtocol::MeshCore) sync = 0x12;
    else if (protocol == mesh::RadioProfileProtocol::Meshtext) sync = 0x2b;
    else return false;
    return static_cast<CustomSTM32WLx*>(_radio)->setProfileProtocol(sync) == RADIOLIB_ERR_NONE;
  }
#endif
#if MESH_ENABLE_MORSE_RX
  float morseReceiverBandwidthKHz() const override { return 4.8f; }
  int16_t prepareMorseReceiver(float frequency_mhz) override {
    if (!supportsRadioDeepInit()) return RADIOLIB_ERR_UNSUPPORTED;
    if (!_board->prepareRadioFrequency(frequency_mhz)) return RADIOLIB_ERR_INVALID_FREQUENCY;
    _morse_rx_fsk_mode = true;
    return prepareSX126xMorseReceiver(static_cast<CustomSTM32WLx*>(_radio), frequency_mhz);
  }
#endif
  bool radioDeepInit() override {
    // STM32WLx begin() owns the integrated radio reset. Reuse target.cpp's
    // complete RF switch/TCXO setup rather than pretending there is an NRST.
    return _deep_init && _deep_init();
  }
  bool supportsRadioDeepInit() const override { return _deep_init != nullptr; }
#if MESH_ENABLE_MORSE_RX
  float readMorseRssi() override { return static_cast<CustomSTM32WLx*>(_radio)->getMorseRssi(); }
#endif
#if MESH_ENABLE_CARRIER_TOOLS
  int16_t prepareMorseCarrier(float frequency_mhz) override {
    const int16_t status = RadioLibWrapper::prepareMorseCarrier(frequency_mhz);
    return status == RADIOLIB_ERR_NONE
        ? setSX126xMorseRamp(static_cast<CustomSTM32WLx*>(_radio)) : status;
  }
  int16_t leaveMorseCarrier() override {
    return leaveSX126xMorseCarrier(static_cast<CustomSTM32WLx*>(_radio));
  }
#endif
#if MESH_ENABLE_CARRIER_TOOLS || MESH_ENABLE_MORSE_RX
  bool restoreCarrierWaveModem() override {
#if MESH_ENABLE_MORSE_RX
    if (_morse_rx_fsk_mode) {
      const bool restored = restoreAfterDeepInit();
      if (restored) _morse_rx_fsk_mode = false;
      return restored;
    }
#endif
#if MESH_ENABLE_CARRIER_TOOLS
    return restoreSX126xPacketRamp(static_cast<CustomSTM32WLx*>(_radio)) == RADIOLIB_ERR_NONE
        && RadioLibWrapper::restoreCarrierWaveModem();
#else
    return RadioLibWrapper::restoreCarrierWaveModem();
#endif
  }
#endif
  bool applyParams(float freq, float bw, uint8_t sf, uint8_t cr) override {
    return ((CustomSTM32WLx *)_radio)->setFrequency(freq) == RADIOLIB_ERR_NONE
        && ((CustomSTM32WLx *)_radio)->setSpreadingFactor(sf) == RADIOLIB_ERR_NONE
        && ((CustomSTM32WLx *)_radio)->setBandwidth(bw) == RADIOLIB_ERR_NONE
        && ((CustomSTM32WLx *)_radio)->setCodingRate(cr) == RADIOLIB_ERR_NONE
        && updatePreamble(sf, bw);
  }

public:
  bool setCodingRate(uint8_t cr) override {
#if MESH_ENABLE_TX_BEACONS
    if (isBeaconRadioOwned()) return false;
#endif
    if (isCarrierWaveActive()) return false;
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
