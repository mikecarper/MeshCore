#pragma once

#include "CustomLR1121.h"
#include "RadioLibWrappers.h"
#include "LR11x0Reset.h"

class CustomLR1121Wrapper : public RadioLibWrapper {
  using DeepInitCallback = bool (*)();
  DeepInitCallback _deep_init = nullptr;
public:
  uint8_t regulatorModes() const override { return static_cast<CustomLR1121*>(_radio)->regulatorModes(); }
  mesh::power::RegulatorMode defaultRegulatorMode() const override {
    return static_cast<CustomLR1121*>(_radio)->defaultRegulatorMode();
  }
  bool configureBootRegulator(mesh::power::RegulatorMode mode) override {
    return static_cast<CustomLR1121*>(_radio)->configureBootRegulator(mode);
  }
  mesh::power::RegulatorMode activeRegulatorMode() const override {
    return static_cast<CustomLR1121*>(_radio)->activeRegulatorMode();
  }
  ~CustomLR1121Wrapper() { unregisterInterruptAction(); }
  CustomLR1121Wrapper(CustomLR1121& radio, mesh::MainBoard& board) : RadioLibWrapper(radio, board) { }
  bool supportsMorseTx() const override { return true; }
  bool supportsMorseRx() const override { return MESH_ENABLE_MORSE_RX && supportsRadioDeepInit(); }
#if MESH_ENABLE_MESHTEXT_WIRE
  bool supportsMeshtext() const override { return true; }
#endif
  void setDeepInitCallback(DeepInitCallback callback) { _deep_init = callback; }

  void powerOff() {
    if (isCarrierWaveActive()) setCarrierWave(0, 0);
    _radio->standby();
    _radio->sleep();
  }

protected:
#if MESH_ENABLE_MESHTEXT_WIRE
  bool applyProtocol(mesh::RadioProfileProtocol protocol) override {
    if (protocol != mesh::RadioProfileProtocol::MeshCore
        && protocol != mesh::RadioProfileProtocol::Meshtext) return false;
    const uint8_t sync = protocol == mesh::RadioProfileProtocol::Meshtext ? 0x2b : 0x12;
    return static_cast<CustomLR1121*>(_radio)->setProfileProtocol(sync) == RADIOLIB_ERR_NONE;
  }
#endif
  int16_t startMorseReceiver() override {
    return static_cast<CustomLR1121*>(_radio)->LR1121::startReceive(
        RADIOLIB_LR11X0_RX_TIMEOUT_INF, 0, 0, 0);
  }
  float readMorseRssi() override {
    float rssi = NAN;
    return static_cast<LR11x0*>(_radio)->getRssiInst(&rssi) == RADIOLIB_ERR_NONE ? rssi : NAN;
  }
  float morseReceiverBandwidthKHz() const override { return 4.8f; }
  int16_t prepareMorseReceiver(float frequency_mhz) override {
    if (!supportsRadioDeepInit()) return RADIOLIB_ERR_UNSUPPORTED;
    if (!_board->prepareRadioFrequency(frequency_mhz)) return RADIOLIB_ERR_INVALID_FREQUENCY;
    _morse_rx_fsk_mode = true;
    if (!prepareRadioHardReset()) return RADIOLIB_ERR_CHIP_NOT_FOUND;
    const int16_t status = static_cast<CustomLR1121*>(_radio)->beginMorseReceive(frequency_mhz);
    const bool finished = _board->finishRadioHardReset();
    return status == RADIOLIB_ERR_NONE && !finished ? RADIOLIB_ERR_UNKNOWN : status;
  }
  int16_t prepareMorseCarrier(float frequency_mhz) override {
    if (!_board->prepareRadioFrequency(frequency_mhz)) return RADIOLIB_ERR_INVALID_FREQUENCY;
    auto* radio = static_cast<CustomLR1121*>(_radio);
    const int16_t status = radio->setFrequency(frequency_mhz);
    if (status != RADIOLIB_ERR_NONE) return status;
#ifdef LR1121_FORCE_HP_PA
    const bool force_hp = true;
#else
    const bool force_hp = false;
#endif
    // Preserve the Morse ramp while honoring HP-only wiring and the HF PA's
    // 13 dBm ceiling, just like the normal packet-power path.
    int8_t clipped = _dbm_valid ? _cur_dbm : LORA_TX_POWER;
    radio->checkOutputPower(clipped, &clipped, force_hp);
    return radio->setOutputPower(clipped, force_hp, 304);
  }
  bool restoreCarrierWaveModem() override {
    if (_morse_rx_fsk_mode) {
      const bool restored = restoreAfterDeepInit();
      if (restored) _morse_rx_fsk_mode = false;
      return restored;
    }
    auto* radio = static_cast<CustomLR1121*>(_radio);
    if (!_board->prepareRadioFrequency(_cur_freq)
        || radio->setFrequency(_cur_freq) != RADIOLIB_ERR_NONE) return false;
    return radio->setOutputPower(_dbm_valid ? _cur_dbm : LORA_TX_POWER) == RADIOLIB_ERR_NONE;
  }
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
  bool radioDeepInit() override {
    return _deep_init && prepareRadioHardReset() && _deep_init()
        && _board->finishRadioHardReset();
  }
  bool supportsRadioDeepInit() const override {
    return _deep_init && supportsRadioHardResetPath();
  }
  bool applyRxBoostedGainMode(bool en) override {
    return ((CustomLR1121 *)_radio)->setRxBoostedGainMode(en) == RADIOLIB_ERR_NONE;
  }
public:
  bool supportsRxBoostedGainMode() const override { return true; }
  bool getRxBoostedGainMode() const override {
    return ((CustomLR1121 *)_radio)->getRxBoostedGainMode();
  }
};
