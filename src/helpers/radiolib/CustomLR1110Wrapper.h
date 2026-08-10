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
  CustomLR1110Wrapper(CustomLR1110& radio, mesh::MainBoard& board)
      : RadioLibWrapper(radio, board), _deep_init(NULL) { }

  void setDeepInitCallback(DeepInitCallback callback) { _deep_init = callback; }

  void powerOff() { _radio->standby(); _radio->sleep(); }

protected:
  bool applyParams(float freq, float bw, uint8_t sf, uint8_t cr) override {
    bool success = ((CustomLR1110 *)_radio)->setFrequency(freq) == RADIOLIB_ERR_NONE
        && ((CustomLR1110 *)_radio)->setSpreadingFactor(sf) == RADIOLIB_ERR_NONE
        && ((CustomLR1110 *)_radio)->setBandwidth(bw) == RADIOLIB_ERR_NONE
        && ((CustomLR1110 *)_radio)->setCodingRate(cr) == RADIOLIB_ERR_NONE
        && updatePreamble(sf);
    if (!success) return false;

    PacketMillis pm = calcMaxPacketMillis(sf, bw, cr, preambleLengthForSF(sf));
    ((CustomLR1110 *)_radio)->setPreambleMillis(pm.preambleMillis);
    ((CustomLR1110 *)_radio)->setMaxPayloadMillis(pm.payloadMillis);
    return true;
  }

public:
  bool setCodingRate(uint8_t cr) override {
    return ((CustomLR1110 *)_radio)->setCodingRate(cr) == RADIOLIB_ERR_NONE;
  }

  void doResetAGC() override { lr11x0ResetAGC((LR11x0 *)_radio, ((CustomLR1110 *)_radio)->getFreqMHz()); }
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
    return airtime < 200 ? 200 : airtime;   // at least 200 millis
  }

  void onSendFinished() override {
    RadioLibWrapper::onSendFinished();
    _radio->setPreambleLength(preambleLengthForSF(getSpreadingFactor())); // overcomes weird issues with small and big pkts
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
    if (_rx_ps_armed) {
      // stop the previous duty-cycle sequence with an explicit standby before
      // reconfiguring the radio
      stopReceiveDutyCycle();
    }
    if (!_rx_ps_enabled || _nf_calib_active) {
      // plain continuous RX: powersaving off, or a periodic noise-floor
      // calibration window is in progress
      return _radio->startReceive();
    }

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

    MESH_DEBUG_PRINTLN("CustomLR1110Wrapper: error: startReceiveDutyCycle(%d), falling back to continuous RX", err);
    return _radio->startReceive();
  }

  bool radioDeepInit() override {
    return _deep_init != NULL && _deep_init();
  }
  bool supportsRadioDeepInit() const override { return _deep_init != NULL; }

public:
  uint8_t getSpreadingFactor() const override { return ((CustomLR1110 *)_radio)->getSpreadingFactor(); }
  
protected:
  bool applyRxBoostedGainMode(bool en) override {
    return ((CustomLR1110 *)_radio)->setRxBoostedGainMode(en) == RADIOLIB_ERR_NONE;
  }
public:
  bool getRxBoostedGainMode() const override {
    return ((CustomLR1110 *)_radio)->getRxBoostedGainMode();
  }
};
