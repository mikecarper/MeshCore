#pragma once

#include "CustomSX1262.h"
#include "RadioLibWrappers.h"
#include "SX126xReset.h"

#ifndef USE_SX1262
#define USE_SX1262
#endif

class CustomSX1262Wrapper : public RadioLibWrapper {
public:
  CustomSX1262Wrapper(CustomSX1262& radio, mesh::MainBoard& board) : RadioLibWrapper(radio, board) { }

protected:
  bool applyParams(float freq, float bw, uint8_t sf, uint8_t cr) override {
    bool success = ((CustomSX1262 *)_radio)->setFrequency(freq) == RADIOLIB_ERR_NONE
        && ((CustomSX1262 *)_radio)->setSpreadingFactor(sf) == RADIOLIB_ERR_NONE
        && ((CustomSX1262 *)_radio)->setBandwidth(bw) == RADIOLIB_ERR_NONE
        && ((CustomSX1262 *)_radio)->setCodingRate(cr) == RADIOLIB_ERR_NONE
        && updatePreamble(sf, bw);
    if (!success) return false;

    PacketMillis pm = calcMaxPacketMillis(sf, bw, cr, preambleLengthForParams(sf, bw));
    ((CustomSX1262 *)_radio)->setPreambleMillis(pm.preambleMillis);
    ((CustomSX1262 *)_radio)->setMaxPayloadMillis(pm.payloadMillis);
    return true;
  }

public:
  bool setCodingRate(uint8_t cr) override {
    return ((CustomSX1262 *)_radio)->setCodingRate(cr) == RADIOLIB_ERR_NONE;
  }

  bool isReceivingPacket() override {
    return ((CustomSX1262 *)_radio)->isReceiving();
  }
  bool isChipBusy() override {
    return ((CustomSX1262 *)_radio)->isChipBusy();
  }
  float getCurrentRSSI() override {
    return ((CustomSX1262 *)_radio)->getRSSI(false);
  }

  float packetScore(float snr, int packet_len) override {
    int sf = ((CustomSX1262 *)_radio)->spreadingFactor;
    return packetScoreInt(snr, sf, packet_len);
  }
  uint8_t getSpreadingFactor() const override { return ((CustomSX1262 *)_radio)->spreadingFactor; }
  void powerOff() {
    ((CustomSX1262 *)_radio)->standby();
    ((CustomSX1262 *)_radio)->sleep(false);
  }

  bool supportsRxPowerSaving() const override { return true; }
  void onReceiveProcessed() override { finishReceiveProcessing(); }

  bool supportsRxPowerSavingRfRxDisable() const override {
  #if defined(SX126X_RXEN)
    return SX126X_RXEN != RADIOLIB_NC;
  #else
    return false;
  #endif
  }

  bool setRxPowerSavingRfRxDisabled(bool disabled) override {
    if (!supportsRxPowerSavingRfRxDisable()) return false;

    if (_rx_ps_armed) stopReceiveDutyCycle();
    ((CustomSX1262 *)_radio)->setRxPowerSavingRfRxDisabled(disabled);

    // Re-arm the configured receive mode immediately without disturbing an
    // unread RX interrupt or an in-flight transmission.
    return setRxPowerSaving(_rx_ps_enabled, _rx_ps_rx_us, _rx_ps_sleep_us);
  }

  bool isRxPowerSavingRfRxDisabled() const override {
    return ((CustomSX1262 *)_radio)->isRxPowerSavingRfRxDisabled();
  }

  // Reconfigure through the normal bounded radio transition so RXEN is never
  // asserted while a transmission or unread packet owns the radio.  The
  // selected table is then honored by every RadioLib mode transition.
  bool setExternalRxLnaEnabled(bool enabled) {
  #if defined(SX126X_RXEN)
    if (SX126X_RXEN == RADIOLIB_NC) return false;

    uint8_t resume_rx = beginReconfigure();
    if (resume_rx > 1) return false;

    bool success = ((CustomSX1262 *)_radio)->setExternalRxLnaEnabled(enabled);
    endReconfigure(resume_rx);
    return success;
  #else
    (void)enabled;
    return false;
  #endif
  }

protected:
  int startReceiveMode() override {
    // Do not abort a frame that started while a calibration transition was
    // being scheduled. recvRaw() will move it to continuous RX afterward.
    if (_nf_calib_active && _rx_ps_armed && isPacketPendingOrReceiving()) {
      return RADIOLIB_ERR_NONE;
    }
    if (_rx_ps_armed) {
      // leaving duty-cycle mode (after RxDone or a reconfig): stop the
      // sequencer and the still-running RTC, or its pending event can
      // silently abort the RX we are about to start
      stopReceiveDutyCycle();
    }
    if (!_rx_ps_enabled || _nf_calib_active) {
      // plain continuous RX: powersaving off, or a periodic noise-floor
      // calibration window is in progress
      if (!_rx_ps_enabled) _rx_ps_continuous_fallback = false;
      return _radio->startReceive();
    }

    if (!((CustomSX1262 *)_radio)->canUseRxPowerSavingDutyCycle(
            _rx_ps_rx_us, _rx_ps_sleep_us)) {
      _rx_ps_continuous_fallback = true;
      return _radio->startReceive();
    }

    _rx_ps_continuous_fallback = false;

    const RadioLibIrqFlags_t irqFlags = RADIOLIB_IRQ_RX_DEFAULT_FLAGS;
    const RadioLibIrqFlags_t irqMask =
        (1UL << RADIOLIB_IRQ_RX_DONE) |
        (1UL << RADIOLIB_IRQ_TIMEOUT) |
        (1UL << RADIOLIB_IRQ_CRC_ERR) |
        (1UL << RADIOLIB_IRQ_HEADER_ERR);

    int err = ((CustomSX1262 *)_radio)->startReceiveDutyCycle(_rx_ps_rx_us, _rx_ps_sleep_us, irqFlags, irqMask);
    if (err == RADIOLIB_ERR_NONE) {
      _rx_ps_armed = true;
      return err;
    }

    _rx_ps_continuous_fallback = true;
    MESH_DEBUG_PRINTLN("CustomSX1262Wrapper: error: startReceiveDutyCycle(%d), falling back to continuous RX", err);
    return _radio->startReceive();
  }

  void stopReceiveDutyCycle() override {
    _radio->standby();   // also wakes the chip if it is in the sleep window
    ((CustomSX1262 *)_radio)->stopRTC();
    _rx_ps_armed = false;
  }

  bool radioDeepInit() override {
    // std_init() re-runs RadioLib begin(), which starts with a hardware reset
    // via NRST - the only way out of a hard-locked chip (BUSY stuck high).
    bool success = ((CustomSX1262 *)_radio)->std_init();
#if defined(TBEAM_1W)
    // std_init() restores RadioLib's default RXEN table. Reapply the T-Beam
    // 1W's persisted external-LNA selection before receive mode is re-armed.
    if (success && _board->canControlLoRaFemLna()) {
      success = ((CustomSX1262 *)_radio)->setExternalRxLnaEnabled(
          _board->isLoRaFemLnaEnabled());
    }
#endif
    return success;
  }
  bool supportsRadioDeepInit() const override { return true; }

protected:
  bool applyRxBoostedGainMode(bool en) override {
    return ((CustomSX1262 *)_radio)->setRxBoostedGainMode(en) == RADIOLIB_ERR_NONE;
  }
public:
  bool supportsRxBoostedGainMode() const override { return true; }
  bool getRxBoostedGainMode() const override {
    return ((CustomSX1262 *)_radio)->getRxBoostedGainMode();
  }

  void doResetAGC() override { sx126xResetAGC((SX126x *)_radio, getRxBoostedGainMode()); }
};
