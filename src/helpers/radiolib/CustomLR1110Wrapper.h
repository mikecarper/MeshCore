#pragma once

#include "CustomLR1110.h"
#include "RadioLibWrappers.h"
#include "LR11x0Reset.h"

#ifndef USE_LR1110
#define USE_LR1110
#endif

class CustomLR1110Wrapper : public RadioLibWrapper {
  using DeepInitCallback = bool (*)();
  DeepInitCallback _deep_init;

public:
  ~CustomLR1110Wrapper() { unregisterInterruptAction(); }
  CustomLR1110Wrapper(CustomLR1110& radio, mesh::MainBoard& board)
      : RadioLibWrapper(radio, board), _deep_init(NULL) { }
  bool supportsCarrierWave() const override { return true; }
  bool supportsMorseRx() const override { return MESH_ENABLE_MORSE_RX && supportsRadioDeepInit(); }
#if MESH_ENABLE_MESHTEXT_WIRE
  bool supportsMeshtext() const override { return true; }
#endif

  void setDeepInitCallback(DeepInitCallback callback) { _deep_init = callback; }

protected:
#if MESH_ENABLE_MESHTEXT_WIRE
  bool applyProtocol(mesh::RadioProfileProtocol protocol) override {
    if (protocol != mesh::RadioProfileProtocol::MeshCore
        && protocol != mesh::RadioProfileProtocol::Meshtext) return false;
    const uint8_t sync = protocol == mesh::RadioProfileProtocol::Meshtext ? 0x2b : 0x12;
    return static_cast<CustomLR1110*>(_radio)->setProfileProtocol(sync) == RADIOLIB_ERR_NONE;
  }
#endif
  int16_t startMorseReceiver() override {
    return static_cast<CustomLR1110*>(_radio)->LR1110::startReceive(
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
    _morse_rx_fsk_mode = true;  // any partial setup must rebuild LoRa on exit
    if (!prepareRadioHardReset()) return RADIOLIB_ERR_CHIP_NOT_FOUND;
    const int16_t status = static_cast<CustomLR1110*>(_radio)->beginMorseReceive(frequency_mhz);
    const bool finished = _board->finishRadioHardReset();
    return status == RADIOLIB_ERR_NONE && !finished ? RADIOLIB_ERR_UNKNOWN : status;
  }
  int16_t prepareMorseCarrier(float frequency_mhz) override {
    if (!_board->prepareRadioFrequency(frequency_mhz)) return RADIOLIB_ERR_INVALID_FREQUENCY;
    auto* radio = static_cast<CustomLR1110*>(_radio);
    const int16_t status = radio->setFrequency(frequency_mhz);
    // The public power API preserves PA selection/calibration and converts
    // the 304 us ramp to the LR11xx's offset encoding.
    return status == RADIOLIB_ERR_NONE
        ? radio->setOutputPower(_dbm_valid ? _cur_dbm : LORA_TX_POWER, false, 304) : status;
  }
  bool restoreCarrierWaveModem() override {
    if (_morse_rx_fsk_mode) {
      const bool restored = restoreAfterDeepInit();
      if (restored) _morse_rx_fsk_mode = false;
      return restored;
    }
    return static_cast<CustomLR1110*>(_radio)->setOutputPower(
        _dbm_valid ? _cur_dbm : LORA_TX_POWER) == RADIOLIB_ERR_NONE;
  }
  void beginProfileRetune(bool continuousRx) override {
    static_cast<CustomLR1110*>(_radio)->beginProfileSwitch(continuousRx);
  }
  void endProfileRetune(bool success) override {
    static_cast<CustomLR1110*>(_radio)->endProfileSwitch(success);
  }
  void setProfileStandbyWarm(bool enabled) override {
    static_cast<CustomLR1110*>(_radio)->setProfileStandbyWarm(enabled);
  }

public:

  void powerOff() {
    if (isCarrierWaveActive()) setCarrierWave(0, 0);
    _radio->standby(); _radio->sleep();
  }

protected:
  bool applyParams(float freq, float bw, uint8_t sf, uint8_t cr) override {
    if (static_cast<CustomLR1110*>(_radio)->profileSwitchFailed()) return false;
#if MC_LR1110_FAST_PROFILE_SWITCH
    bool success = ((CustomLR1110 *)_radio)->setFrequency(freq) == RADIOLIB_ERR_NONE
        && ((CustomLR1110 *)_radio)->setLoRaModulationParams(bw, sf, cr) == RADIOLIB_ERR_NONE
        && updatePreamble(sf, bw);
#else
    bool success = ((CustomLR1110 *)_radio)->setFrequency(freq) == RADIOLIB_ERR_NONE
        && ((CustomLR1110 *)_radio)->setSpreadingFactor(sf) == RADIOLIB_ERR_NONE
        && ((CustomLR1110 *)_radio)->setBandwidth(bw) == RADIOLIB_ERR_NONE
        && ((CustomLR1110 *)_radio)->setCodingRate(cr) == RADIOLIB_ERR_NONE
        && updatePreamble(sf, bw);
#endif
    if (!success) return false;

    PacketMillis pm = calcMaxPacketMillis(sf, bw, cr, preambleLengthForParams(sf, bw));
    ((CustomLR1110 *)_radio)->setPreambleMillis(pm.preambleMillis);
    ((CustomLR1110 *)_radio)->setMaxPayloadMillis(pm.payloadMillis);
    return true;
  }

public:
  bool setCodingRate(uint8_t cr) override {
    if (isCarrierWaveActive()) return false;
    return ((CustomLR1110 *)_radio)->setCodingRate(cr) == RADIOLIB_ERR_NONE;
  }

  bool isReceivingPacket() override {
    return ((CustomLR1110 *)_radio)->isReceiving();
  }
  bool isChipBusy() override {
    return ((CustomLR1110 *)_radio)->isChipBusy();
  }
  float getCurrentRSSI() override {
    float rssi = -110;
    ((CustomLR1110 *)_radio)->getRssiInst(&rssi);
    return rssi;
  }

  uint32_t getEstAirtimeFor(int len_bytes) override {
    auto airtime = RadioLibWrapper::getEstAirtimeFor(len_bytes);
    if (airtime == 0) return 0;
    return airtime < 200 ? 200 : airtime;   // at least 200 millis
  }

  void onSendFinished() override {
    RadioLibWrapper::onSendFinished();
    _radio->setPreambleLength(currentPreambleLength()); // overcomes weird issues with small and big pkts
  }

  bool supportsRxPowerSaving() const override { return true; }
  void onReceiveProcessed() override { finishReceiveProcessing(); }

protected:
  bool isPacketReady() override {
    // Header errors are recovery events, not packets. Handle them before the
    // generic duty-cycle readiness check can discard the IRQ or a stale buffer
    // length can be mistaken for a newly received frame.
    if (_radio->checkIrq(RADIOLIB_IRQ_HEADER_ERR) > 0) {
      MESH_DEBUG_PRINTLN("CustomLR1110Wrapper: recovering from header error");
      int16_t err = ((CustomLR1110 *)_radio)->recoverReceivePath();
      if (err != RADIOLIB_ERR_NONE) {
        MESH_DEBUG_PRINTLN("CustomLR1110Wrapper: RX recovery error (%d)", err);
      }
      _rx_ps_armed = false;
      _rx_hold_continuous = false;
      n_recv_errors++;
      return false;
    }
    return RadioLibWrapper::isPacketReady();
  }

  int startReceiveMode() override {
    // Do not abort a frame that started while a calibration transition was
    // being scheduled. recvRaw() will move it to continuous RX afterward.
    if (_nf_calib_active && _rx_ps_armed && isPacketPendingOrReceiving()) {
      return RADIOLIB_ERR_NONE;
    }
    if (_rx_ps_armed) {
      // stop the previous duty-cycle sequence with an explicit standby before
      // reconfiguring the radio
      stopReceiveDutyCycle();
    }
    if (!_rx_ps_enabled || _nf_calib_active) {
      // plain continuous RX: powersaving off, or a periodic noise-floor
      // calibration window is in progress
      if (!_rx_ps_enabled) _rx_ps_continuous_fallback = false;
      return _radio->startReceive();
    }

    if (rxPowerSavingUsesContinuousFallback(_rx_ps_rx_us, _rx_ps_sleep_us)) {
      _rx_ps_continuous_fallback = true;
      return _radio->startReceive();
    }

    _rx_ps_continuous_fallback = false;

    const RadioLibIrqFlags_t irqFlags = RADIOLIB_IRQ_RX_DEFAULT_FLAGS;
    // route RX timeout (false preamble detect) and error IRQs to the IRQ pin
    // as well, so the wrapper re-arms RX instead of waiting forever
    const RadioLibIrqFlags_t irqMask =
        (1UL << RADIOLIB_IRQ_RX_DONE) |
        (1UL << RADIOLIB_IRQ_TIMEOUT) |
        (1UL << RADIOLIB_IRQ_CRC_ERR) |
        (1UL << RADIOLIB_IRQ_HEADER_ERR);

    int err = ((CustomLR1110 *)_radio)->startReceiveDutyCycle(_rx_ps_rx_us, _rx_ps_sleep_us, irqFlags, irqMask);
    if (err == RADIOLIB_ERR_NONE) {
      _rx_ps_armed = true;
      return err;
    }

    _rx_ps_continuous_fallback = true;
    MESH_DEBUG_PRINTLN("CustomLR1110Wrapper: error: startReceiveDutyCycle(%d), falling back to continuous RX", err);
    return _radio->startReceive();
  }

  bool radioDeepInit() override {
    return _deep_init != NULL && prepareRadioHardReset() && _deep_init()
        && _board->finishRadioHardReset();
  }
  bool supportsRadioDeepInit() const override {
    return _deep_init != NULL && supportsRadioHardResetPath();
  }

public:
  uint8_t getSpreadingFactor() const override { return ((CustomLR1110 *)_radio)->getSpreadingFactor(); }
  
protected:
  bool applyRxBoostedGainMode(bool en) override {
    return ((CustomLR1110 *)_radio)->setRxBoostedGainMode(en) == RADIOLIB_ERR_NONE;
  }
public:
  bool supportsRxBoostedGainMode() const override { return true; }
  bool getRxBoostedGainMode() const override {
    return ((CustomLR1110 *)_radio)->getRxBoostedGainMode();
  }

  void doResetAGC() override { lr11x0ResetAGC((LR11x0 *)_radio, ((CustomLR1110 *)_radio)->getFreqMHz(), getRxBoostedGainMode()); }
};
