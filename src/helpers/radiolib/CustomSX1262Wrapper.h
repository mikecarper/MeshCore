#pragma once
#include "SX126xReceiveMode.h"

#include "CustomSX1262.h"
#include "RadioLibWrappers.h"
#include "ProfileSwitchTiming.h"
#include "SX126xReset.h"
#include "SX126xMorseRamp.h"
#include "SX126xMorseReceiver.h"
#include "RxpsPulseTiming.h"

#ifndef USE_SX1262
#define USE_SX1262
#endif

class CustomSX1262Wrapper : public RadioLibWrapper {
  bool _profile_standby_held = false;
  bool _saved_standby_xosc = false;

protected:
  uint8_t getDriverSwitchFeatures() const override {
    return static_cast<const CustomSX1262*>(_radio)->getProfileSwitchFeatures();
  }
  uint8_t driverSwitchCapabilities() const override {
    return static_cast<const CustomSX1262*>(_radio)->profileSwitchCapabilities();
  }
  void setDriverSwitchFeatures(uint8_t features) override {
    static_cast<CustomSX1262*>(_radio)->setProfileSwitchFeatures(features);
  }
#if MESH_WARM_RX_REFRESH
  uint32_t defaultAGCPulseSleepUs() const override {
    const uint32_t tcxo=static_cast<const CustomSX1262*>(_radio)->getTcxoDelay();
    if(tcxo>UINT32_MAX-1000UL) return 0;
    return mesh::rxpspulse::bufferedSleepRequestUs(
        tcxo+1000UL,64000UL,0xFFFFFFUL);
  }
#endif
  int8_t readReceiveMode() override {
#if MESH_WARM_RX_REFRESH
    if(_rxps_pulse.owns()) return -1;
#endif
    return sx126xReceiveMode((SX126x*)_radio);
  }
  void beginProfileRetune(bool continuousRx) override {
    static_cast<CustomSX1262*>(_radio)->beginProfileSwitch(continuousRx);
  }
  void endProfileRetune(bool success) override {
    static_cast<CustomSX1262*>(_radio)->endProfileSwitch(success);
  }
#if (defined(MC_NRF_RADIO_PROFILE_PRIORITY) && MC_NRF_RADIO_PROFILE_PRIORITY) \
    || (defined(MC_NRF_RADIO_PROFILE_USB_IRQ_GUARD) && MC_NRF_RADIO_PROFILE_USB_IRQ_GUARD)
  bool ownsWarmProfileRetune() const override {
    return static_cast<const CustomSX1262*>(_radio)->hilOwnsWarmProfileRetune();
  }
#endif

  void setProfileStandbyWarm(bool enabled) override {
    if (enabled == _profile_standby_held) return;
    auto* radio = static_cast<CustomSX1262*>(_radio);
    if (enabled) {
      _saved_standby_xosc = radio->standbyXOSC;
      radio->standbyXOSC = true;
    } else {
      radio->standbyXOSC = _saved_standby_xosc;
    }
    _profile_standby_held = enabled;
  }

public:
#if MESH_WARM_RX_REFRESH
  bool supportsWarmRxRefresh() const override { return true; }
#endif
  static constexpr bool SupportsRxPowerSaving = true;
  uint32_t rxPowerSavingTransitionUs() const override {
    return static_cast<CustomSX1262*>(_radio)->getTcxoDelay() + 1000UL;
  }
  uint8_t regulatorModes() const override { return static_cast<CustomSX1262*>(_radio)->regulatorModes(); }
  mesh::power::RegulatorMode defaultRegulatorMode() const override {
    return static_cast<CustomSX1262*>(_radio)->defaultRegulatorMode();
  }
  bool configureBootRegulator(mesh::power::RegulatorMode mode) override {
    return static_cast<CustomSX1262*>(_radio)->configureBootRegulator(mode);
  }
  mesh::power::RegulatorMode activeRegulatorMode() const override {
    return static_cast<CustomSX1262*>(_radio)->activeRegulatorMode();
  }
  ~CustomSX1262Wrapper() { unregisterInterruptAction(); }
  CustomSX1262Wrapper(CustomSX1262& radio, mesh::MainBoard& board) : RadioLibWrapper(radio, board) { }
#if MESH_ENABLE_HOST_KISS
  bool supportsHostKiss() const override { return true; }
#endif
  bool supportsCarrierWave() const override { return true; }
  bool supportsMorseRx() const override { return MESH_ENABLE_MORSE_RX && supportsRadioDeepInit(); }
#if MESH_ENABLE_MESHTEXT_WIRE
  bool supportsMeshtext() const override { return true; }
#endif
#if MESH_ENABLE_ETHERNET_WIRE
  bool supportsEthernetTunnel() const override { return true; }
#endif

protected:
  float morseReceiverBandwidthKHz() const override { return 4.8f; }
  int16_t prepareMorseReceiver(float frequency_mhz) override {
    if (!_board->prepareRadioFrequency(frequency_mhz)) return RADIOLIB_ERR_INVALID_FREQUENCY;
    _morse_rx_fsk_mode = true; // partial reset/configuration also needs full restoration
    if (!prepareRadioHardReset()) return RADIOLIB_ERR_UNKNOWN;
    auto* radio = static_cast<CustomSX1262*>(_radio);
    radio->invalidateMorseModemCache();
    const int16_t status = prepareSX126xMorseReceiver(radio, frequency_mhz);
    return status == RADIOLIB_ERR_NONE && !_board->finishRadioHardReset()
        ? RADIOLIB_ERR_UNKNOWN : status;
  }
  float readMorseRssi() override { return static_cast<CustomSX1262*>(_radio)->getMorseRssi(); }
  int16_t prepareMorseCarrier(float frequency_mhz) override {
    const int16_t status = RadioLibWrapper::prepareMorseCarrier(frequency_mhz);
    return status == RADIOLIB_ERR_NONE
        ? setSX126xMorseRamp(static_cast<CustomSX1262*>(_radio)) : status;
  }
  int16_t leaveMorseCarrier() override {
    return leaveSX126xMorseCarrier(static_cast<CustomSX1262*>(_radio));
  }
  bool restoreCarrierWaveModem() override {
    return restoreSX126xPacketRamp(static_cast<CustomSX1262*>(_radio)) == RADIOLIB_ERR_NONE
        && RadioLibWrapper::restoreCarrierWaveModem();
  }
#if MESH_ENABLE_MESHTEXT_WIRE || MESH_ENABLE_ETHERNET_WIRE || MESH_ENABLE_HOST_KISS
  bool applyProtocol(mesh::RadioProfileProtocol protocol) override {
    uint8_t sync = 0x12;
    if (protocol == mesh::RadioProfileProtocol::Meshtext) {
      if (!supportsMeshtext()) return false;
      sync = 0x2b;
#if MESH_ENABLE_ETHERNET_WIRE
    } else if (protocol == mesh::RadioProfileProtocol::Ethernet) {
      if (!supportsEthernetTunnel()) return false;
      sync = 0x16;
#endif
#if MESH_ENABLE_HOST_KISS
    } else if (protocol == mesh::RadioProfileProtocol::Kiss) {
      if (!supportsHostKiss()) return false;
#endif
    } else if (protocol != mesh::RadioProfileProtocol::MeshCore) return false;
    // No alternate wire implementation means no modem write is needed.
    // Ethernet-only builds must still restore the MeshCore syncword.
#if !MESH_ENABLE_ETHERNET_WIRE && !MESH_ENABLE_HOST_KISS
    if (!supportsMeshtext()) return protocol == mesh::RadioProfileProtocol::MeshCore;
#endif
    auto* radio = static_cast<CustomSX1262*>(_radio);
    // Stage shared packet invariants only in a proven owned RX hop; normal
    // initialization/rollback still repairs the complete RadioLib state.
    return radio->setProfileProtocol(sync) == RADIOLIB_ERR_NONE;
  }
#endif
  int16_t applyCachedTxPower(int8_t dbm) override {
#ifdef SX126X_PA_RAMP_TIME
    // External-PA boards such as the T-Beam 1W require a slower PA ramp. Use
    // the same ramp for runtime power changes and post-reset restoration;
    // SX1262::setOutputPower() would otherwise restore RadioLib's default.
    return ((CustomSX1262 *)_radio)->setTxParams(
        dbm, static_cast<uint8_t>(SX126X_PA_RAMP_TIME));
#else
    return RadioLibWrapper::applyCachedTxPower(dbm);
#endif
  }

  bool applyProfileModulation(float bw, uint8_t sf, uint8_t cr) {
    auto* radio = static_cast<CustomSX1262*>(_radio);
    if (getDriverSwitchFeatures() & mesh::RadioSwitchTuning::Batch)
      return radio->setLoRaModulationParams(bw, sf, cr) == RADIOLIB_ERR_NONE;
    return radio->setSpreadingFactor(sf) == RADIOLIB_ERR_NONE
        && radio->setBandwidth(bw) == RADIOLIB_ERR_NONE
        && radio->setCodingRate(cr) == RADIOLIB_ERR_NONE;
  }

  bool applyParams(float freq, float bw, uint8_t sf, uint8_t cr) override {
    if (static_cast<CustomSX1262*>(_radio)->profileSwitchFailed()) return false;
#if MESH_HIL_PROFILE_TIMING
    bool success = ((CustomSX1262 *)_radio)->setFrequency(freq) == RADIOLIB_ERR_NONE;
    mesh::profiletiming::mark(mesh::profiletiming::Frequency, mesh::profiletiming::readClock());
    if (success) {
      success = applyProfileModulation(bw, sf, cr);
      mesh::profiletiming::mark(mesh::profiletiming::Modulation, mesh::profiletiming::readClock());
    }
    if (success) {
      success = updatePreamble(sf, bw);
      mesh::profiletiming::mark(mesh::profiletiming::Preamble, mesh::profiletiming::readClock());
    }
#else
    bool success = ((CustomSX1262 *)_radio)->setFrequency(freq) == RADIOLIB_ERR_NONE
        && applyProfileModulation(bw, sf, cr)
        && updatePreamble(sf, bw);
#endif
    if (!success) return false;

    PacketMillis pm = calcMaxPacketMillis(sf, bw, cr, preambleLengthForParams(sf, bw));
    ((CustomSX1262 *)_radio)->setPreambleMillis(pm.preambleMillis);
    ((CustomSX1262 *)_radio)->setMaxPayloadMillis(pm.payloadMillis);
#if MESH_HIL_PROFILE_TIMING
    mesh::profiletiming::mark(mesh::profiletiming::GuardMath, mesh::profiletiming::readClock());
#endif
    return true;
  }

public:
  bool setCodingRate(uint8_t cr) override {
#if MESH_WARM_RX_REFRESH
    if(_rxps_pulse.pending) return false;
#endif
#if MESH_ENABLE_TX_BEACONS
    if (isBeaconRadioOwned()) return false;
#endif
    if (isCarrierWaveActive()) return false;
    return ((CustomSX1262 *)_radio)->setCodingRate(cr) == RADIOLIB_ERR_NONE;
  }

  bool isReceivingPacket() override {
#if MESH_WARM_RX_REFRESH
    if(_rxps_pulse.owns()) return true;
#endif
    if (_rx_ps_armed && isChipBusy()) return false;
    return ((CustomSX1262 *)_radio)->isReceiving();
  }
  bool isChipBusy() override {
    return ((CustomSX1262 *)_radio)->isChipBusy();
  }
  float getCurrentRSSI() override {
#if MESH_WARM_RX_REFRESH
    if(_rxps_pulse.owns()) return NAN;
#endif
    return ((CustomSX1262 *)_radio)->getRSSI(false);
  }

  float packetScore(float snr, int packet_len) override {
    int sf = ((CustomSX1262 *)_radio)->spreadingFactor;
    return packetScoreInt(snr, sf, packet_len);
  }
  uint8_t getSpreadingFactor() const override { return ((CustomSX1262 *)_radio)->spreadingFactor; }
  void powerOff() {
#if MESH_WARM_RX_REFRESH
    if(_rxps_pulse.pending) return;
#endif
    if (isCarrierWaveActive()) setCarrierWave(0, 0);
    if (_rx_ps_armed) stopReceiveDutyCycle();
    ((CustomSX1262 *)_radio)->standby();
    ((CustomSX1262 *)_radio)->sleep(false);
  }

  bool supportsRxPowerSaving() const override { return SupportsRxPowerSaving; }
  void onReceiveProcessed() override { finishReceiveProcessing(); }

  bool supportsRxPowerSavingRfRxDisable() const override {
  #if defined(SX126X_RXEN)
    return SX126X_RXEN != RADIOLIB_NC;
  #else
    return false;
  #endif
  }

  bool setRxPowerSavingRfRxDisabled(bool disabled) override {
    return trySetRxPowerSavingRfRxDisabled(disabled) == mesh::RadioParamApplyResult::APPLIED;
  }

  mesh::RadioParamApplyResult trySetRxPowerSavingRfRxDisabled(bool disabled) override {
    using Result = mesh::RadioParamApplyResult;
    if (!supportsRxPowerSavingRfRxDisable()) return Result::FAILED;
#if MESH_ENABLE_TX_BEACONS
    if (isBeaconRadioOwned()) return Result::BUSY;
#endif
    if (isCarrierWaveActive()) return Result::BUSY;
    if (isRxPowerSavingRfRxDisabled() == disabled) return Result::APPLIED;

    // Do not stop duty-cycle RX or change its policy before admission. A BUSY
    // sleep window, TX, or unread RX completion still owns the current mode.
    const uint8_t resume_rx = beginReconfigure();
    if (resume_rx > 1) return resume_rx == 2 ? Result::BUSY : Result::FAILED;

    const bool previous = isRxPowerSavingRfRxDisabled();
    ((CustomSX1262 *)_radio)->setRxPowerSavingRfRxDisabled(disabled);
    endReconfigure(resume_rx);
    if (resume_rx && !isInRecvMode()) {
      // startRecv already retained its bounded retry/backoff. That retry must
      // use the old RXEN policy; never report a failed RX restoration as OK.
      ((CustomSX1262 *)_radio)->setRxPowerSavingRfRxDisabled(previous);
      return Result::FAILED;
    }
    return Result::APPLIED;
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
#if MESH_WARM_RX_REFRESH
  static bool pulseActivity(uint16_t irq) {
    return irq & (RADIOLIB_SX126X_IRQ_RX_DONE | RADIOLIB_SX126X_IRQ_PREAMBLE_DETECTED
        | RADIOLIB_SX126X_IRQ_HEADER_VALID | RADIOLIB_SX126X_IRQ_SYNC_WORD_VALID);
  }
  void pulseFailure(int16_t rc,RxpsPulse::Step step) {
    _rxps_pulse.noteError(rc,step);_rxps_pulse.recovering(static_cast<uint32_t>(micros()));
  }
  bool startAGCPulse() override {
    auto* radio=static_cast<CustomSX1262*>(_radio);
    auto* mod=radio->getMod();
    const uint32_t transition=rxPowerSavingTransitionUs();
    // Re-resolve after recovery too: an oscillator fallback must not retain
    // another board's or an earlier oscillator mode's absolute duration.
    const uint32_t sleep=getAGCPulseSleep();
    if(!_rxps_pulse.configure(sleep,transition)) {
      _rxps_pulse.admitted(static_cast<uint32_t>(micros()));
      pulseFailure(RADIOLIB_ERR_INVALID_SLEEP_PERIOD,RxpsPulse::Step::Stage);return true;
    }
    uint16_t irq=0;
    auto probe=mesh::rxpspulse::readIrq(mod,irq);++_rxps_pulse.irq_reads;
    if(!probe.ok()) {
      _rxps_pulse.admitted(static_cast<uint32_t>(micros()));_rxps_pulse_retries=0;
      pulseFailure(probe.error,RxpsPulse::Step::Irq);return true;
    }
    if(pulseActivity(irq) || hasPendingRadioInterrupt()) {
      // No command was admitted; do not stop or acknowledge a packet owner.
      _rxps_pulse.deferred();return false;
    }
    if(_cur_sf<5 || _cur_sf>12 || !isfinite(_cur_bw) || _cur_bw<=0 || sleep<=transition) {
      _rxps_pulse.admitted(static_cast<uint32_t>(micros()));
      pulseFailure(RADIOLIB_ERR_INVALID_RX_PERIOD,RxpsPulse::Step::Stage);return true;
    }
    const float symbol_us=(float(uint32_t(1)<<_cur_sf)*1000.0f)/_cur_bw;
    const float encoded_sleep=float(((uint64_t(sleep-transition)*8)/125))*15.625f;
    const float sync=_cur_sf<=6?RX_POWERSAVING_SYNC_SYMBOLS_LOW_SF:RX_POWERSAVING_SYNC_SYMBOLS;
    const float required=(currentPreambleLength()+sync+RX_POWERSAVING_HEADER_SYMBOLS+RX_POWERSAVING_TIMER_SLACK_SYMBOLS)*symbol_us;
    const float needed=(required-encoded_sleep)*0.5f;
    if(currentPreambleLength()<8 || !isfinite(required) || !isfinite(needed) || needed>3000000.0f) {
      _rxps_pulse.admitted(static_cast<uint32_t>(micros()));
      pulseFailure(RADIOLIB_ERR_INVALID_RX_PERIOD,RxpsPulse::Step::Stage);return true;
    }
    const uint32_t rx=snapRxPowerSavingUpToTick(ceilRxPowerSavingValue(needed>symbol_us?needed:symbol_us));
    mesh::rxpspulse::Periods periods;
    const auto valid=mesh::rxpspulse::encodePeriods(rx,sleep,transition,periods);
    _rxps_pulse.admitted(static_cast<uint32_t>(micros()));_rxps_pulse_retries=0;
    // Reject unusable geometry once per scheduled attempt, staying awake.
    if(_cur_sf<5 || _cur_sf>12 || !isfinite(symbol_us) || !valid.ok()
        || rx>3000000UL) {
      pulseFailure(RADIOLIB_ERR_INVALID_RX_PERIOD,RxpsPulse::Step::Stage);return true;
    }
    _rxps_pulse.rx_us=rx;_rxps_pulse.rx_ticks=periods.rxTicks;_rxps_pulse.sleep_ticks=periods.sleepTicks;
    radio->invalidateMorseModemCache();
    mod->setRfSwitchState(Module::MODE_IDLE);
    auto result=mesh::rxpspulse::standbyRc(mod);
    if(!result.ok()) { pulseFailure(result.error,RxpsPulse::Step::Standby);return true; }
    probe=mesh::rxpspulse::readIrq(mod,irq);++_rxps_pulse.irq_reads;
    if(!probe.ok()) { pulseFailure(probe.error,RxpsPulse::Step::Irq);return true; }
    if(irq&RADIOLIB_SX126X_IRQ_RX_DONE || hasPendingRadioInterrupt()) {
      // RX may complete while standby returns. Preserve FIFO and its IRQ.
      ++_rxps_pulse.aborts;_rxps_pulse.packet=true;_rxps_pulse.phase=RxpsPulse::Phase::Stop;return true;
    }
    if(pulseActivity(irq)) {
      // Preamble/header appearing between the preprobe and standby belongs
      // to an interrupted partial frame, not an empty maintenance window.
      ++_rxps_pulse.aborts;_rxps_pulse.cancelRequest();_rxps_pulse.phase=RxpsPulse::Phase::Stop;return true;
    }
    int16_t rc=radio->stagePulseReceive();
    if(rc) { pulseFailure(rc,RxpsPulse::Step::Stage);return true; }
    mod->setRfSwitchState(Module::MODE_RX);
    result=mesh::rxpspulse::armDutyCycle(mod,periods);
    if(!result.ok()) { pulseFailure(result.error,RxpsPulse::Step::Arm);return true; }
    // A preamble can extend the window and become a maximum-sized packet.
    // Preserve that owner before declaring a missing natural cycle. These are
    // cached PHY airtime bounds, with no SPI or NSS during the lease.
    const uint64_t budget=2ULL*rx+sleep+radio->pulsePacketBudgetUs()+2000000ULL;
    _rxps_pulse.armed(static_cast<uint32_t>(micros()),uint32_t(budget<60000000ULL?budget:60000000ULL));
    return true;
  }
  int16_t pulseContinuous() {
    auto* radio=static_cast<CustomSX1262*>(_radio);
    int16_t rc=radio->enterPulseContinuous();
    if(rc) { _rxps_pulse.noteError(rc,RxpsPulse::Step::SetRx);return rc; }
    rc=radio->verifyPulseReceive();
    if(rc) _rxps_pulse.noteError(rc,RxpsPulse::Step::Mode);
    else ++_rxps_pulse.verified;
    return rc;
  }
  void retireFailedPulse(uint32_t now) {
    // A failed repair does not revoke an unchanged-channel completed FIFO.
    const bool packet=hasPendingRadioInterrupt();
    _rxps_pulse.abandon(now);state=packet?1:0;
    _profile_refresh_required=_profile_repair_required=!packet;
    _profile_retry_at=packet?0:millis()+250UL;
  }
  void serviceAGCPulse() override {
    auto* radio=static_cast<CustomSX1262*>(_radio);auto* mod=radio->getMod();
    const uint32_t now=static_cast<uint32_t>(micros());
    if(_rxps_pulse.waiting()) {
      // GPIO only: any SPI/NSS transaction would wake and invalidate sleep.
      _rxps_pulse.sample(now,isChipBusy(),hasPendingRadioInterrupt());
      if(_rxps_pulse.waiting()) return;
    }
    if(_rxps_pulse.phase==RxpsPulse::Phase::Packet) return;
    if(_rxps_pulse.phase==RxpsPulse::Phase::Recovery) {
      if(isChipBusy()) {
        if(now-_rxps_pulse.armed_at<10000000UL) return;
        // Bounded exceptional wake, never credited as natural sleep evidence.
        if(!_rxps_pulse_retries) {
          ++_rxps_pulse.forced_wake;++_rxps_pulse_retries;
          const Module::BitWidth_t width=mod->spiConfig.widths[RADIOLIB_MODULE_SPI_WIDTH_STATUS];
          mod->spiConfig.widths[RADIOLIB_MODULE_SPI_WIDTH_STATUS]=Module::BITS_0;
          uint8_t status=0;
          const int16_t wake_rc=mod->SPIreadStream(RADIOLIB_SX126X_CMD_GET_STATUS,&status,1,false,false);
          mod->spiConfig.widths[RADIOLIB_MODULE_SPI_WIDTH_STATUS]=width;
          if(wake_rc) _rxps_pulse.noteError(wake_rc,RxpsPulse::Step::Recovery);
          _rxps_pulse.retry_at=now;return;
        }
        if(now-_rxps_pulse.retry_at<2000000UL) return;
        retireFailedPulse(now);return;
      }
      if(_rxps_pulse_retries && now-_rxps_pulse.retry_at<250000UL) return;
      if(_rxps_pulse_retries>=3) {
        retireFailedPulse(now);return;
      }
      ++_rxps_pulse_retries;
    }
    radio->invalidateMorseModemCache();
    mod->setRfSwitchState(Module::MODE_IDLE);
    const auto stop=mesh::rxpspulse::standbyRc(mod);
    if(!stop.ok()) { pulseFailure(stop.error,RxpsPulse::Step::Standby);return; }
    uint16_t irq=0;const auto probe=mesh::rxpspulse::readIrq(mod,irq);++_rxps_pulse.irq_reads;
    if(!probe.ok()) { pulseFailure(probe.error,RxpsPulse::Step::Irq);return; }
    const auto rtc=mesh::rxpspulse::stopRtc(mod);
    if(!rtc.ok()) {
      const auto step=rtc.step==mesh::rxpspulse::Step::StopRtc?RxpsPulse::Step::RtcStop:
          rtc.step==mesh::rxpspulse::Step::ReadRtcEvent?RxpsPulse::Step::RtcRead:RxpsPulse::Step::RtcEvent;
      pulseFailure(rtc.error,step);return;
    }
    ++_rxps_pulse.rtc;
    if(irq&RADIOLIB_SX126X_IRQ_RX_DONE || hasPendingRadioInterrupt()) {
      _rxps_pulse.packetOwned();state=1;return;
    }
    if(pulseActivity(irq) && !_rxps_pulse.cancel) { ++_rxps_pulse.aborts;_rxps_pulse.cancelRequest(); }
    _rxps_pulse.rearming();
    const int16_t rc=pulseContinuous();
    _rxps_pulse.receiveEntry(rc,static_cast<uint32_t>(micros()));
    if(rc) { _rxps_pulse.retry_at=static_cast<uint32_t>(micros());return; }
    state=1;_startrx_fails=0;_rx_retry_pending=false;_rx_retry_delay_ms=0;
    // Keep the same collector/cache selection; apply the usual RX settle.
    _nf_sample_from=millis()+NoiseFloorSettleMillis;
  }
  bool warmRxRefreshUsesFs() const override {
    const auto* radio=static_cast<const CustomSX1262*>(_radio);
    const bool enabled=(radio->getProfileSwitchFeatures()&mesh::RadioSwitchTuning::Fs)!=0;
#ifdef MC_SX1262_HIL
    return enabled && radio->hilUseFsOnRetune;
#else
    return enabled;
#endif
  }
  WarmRxRefresh::Outcome performWarmRxRefresh(WarmRxRefresh::Reason& reason) override {
    auto* radio=static_cast<CustomSX1262*>(_radio);
    if(!radio->warmRxRefreshReady()) { reason=WarmRxRefresh::Reason::Cache; return WarmRxRefresh::Outcome::Deferred; }
    const bool saved_warm=radio->standbyXOSC;
    radio->standbyXOSC=true;
    beginProfileRetune(true);
    radio->trackWarmRxRefresh(true);
    struct Scope {
      CustomSX1262* radio; bool saved; bool success=true;
      Scope(CustomSX1262* r,bool previous):radio(r),saved(previous) {}
      ~Scope() { radio->trackWarmRxRefresh(false); radio->endProfileSwitch(success); radio->standbyXOSC=saved; }
    } scope(radio,saved_warm);
    _warm_refresh.attempting=true;
    const uint8_t resume=beginReconfigure();
    _warm_refresh.attempting=false;
    if(resume==2) {
      // Preserve a FIFO completing during standby. Never rearm on this path.
      reason=WarmRxRefresh::Reason::Packet;
      return WarmRxRefresh::Outcome::Deferred;
    }
    if(resume!=1) {
      scope.success=false; reason=WarmRxRefresh::Reason::Command;
      const int16_t error=radio->profileSwitchFailed()?radio->warmRxRefreshError():WarmRxRefresh::ContextError;
      _warm_refresh.error(error,radio->profileSwitchFailed()?radio->warmRxRefreshStep():WarmRxRefresh::Step::Context);
      // This transaction never changes the tuple. Even a failed standby can
      // race a completion of that same tuple: let recvRaw verify/read it before
      // ordinary reentry, rather than generic repair claiming its FIFO.
      const bool packet_pending=hasPendingRadioInterrupt();
      if(packet_pending) state=1; // STATE_RX owns the unchanged-tuple event.
      _profile_refresh_required=_profile_repair_required=!packet_pending;
      _profile_retry_at=millis()+250UL;
      return WarmRxRefresh::Outcome::Failed;
    }
    const int16_t error=radio->startWarmRxRefreshReceive();
    if(error!=RADIOLIB_ERR_NONE) {
      scope.success=false; reason=WarmRxRefresh::Reason::Command;
      _warm_refresh.error(error,radio->warmRxRefreshStep());
      const bool packet_pending=hasPendingRadioInterrupt();
      state=packet_pending?1:0; // Preserve a completion before bounded repair.
      _profile_refresh_required=_profile_repair_required=!packet_pending;
      _profile_retry_at=millis()+250UL;
      return WarmRxRefresh::Outcome::Failed;
    }
    _warm_refresh.checkedWarmReceive(radio->warmRxRefreshReturnedUs());
    state=1; // STATE_RX; checked fast SetRx succeeded, preserving IRQ events.
    _startrx_fails=0; _rx_retry_pending=false; _rx_retry_delay_ms=0;
    reason=WarmRxRefresh::Reason::None;
    return WarmRxRefresh::Outcome::Applied;
  }
#endif
  int startReceiveMode() override {
#if MESH_WARM_RX_REFRESH
    if(_rxps_pulse.pending && _rxps_pulse.phase==RxpsPulse::Phase::Packet) return pulseContinuous();
#endif
    return startReceivePowerSaving();
  }
  int16_t armDutyCycle(RadioLibIrqFlags_t irq_flags, RadioLibIrqFlags_t irq_mask,
      uint32_t* effective_rx_us, uint32_t* effective_sleep_us) override {
    *effective_rx_us = _rx_ps_rx_us;
    *effective_sleep_us = _rx_ps_sleep_us;
    return ((CustomSX1262*)_radio)->startReceiveDutyCycle(
        _rx_ps_rx_us, _rx_ps_sleep_us, irq_flags, irq_mask);
  }

  int16_t stopDutyCycleHardware() override {
    const int16_t standby_error = _radio->standby();
    const int16_t rtc_error = ((CustomSX1262*)_radio)->stopRTC();
    return standby_error != RADIOLIB_ERR_NONE ? standby_error : rtc_error;
  }
  bool radioDeepInit() override {
    // A board hook takes precedence over direct NRST because it may also need
    // to recover a regulator, SPI bus, or I/O expander. With neither mechanism
    // available, do not report a normal re-init as a successful hard reset.
    if (!prepareRadioHardReset()) return false;

    // std_init() re-runs RadioLib begin(). It pulses a direct NRST itself;
    // expander-reset boards enter here with the radio already reset.
    bool success = ((CustomSX1262 *)_radio)->std_init();
#if defined(TBEAM_1W)
    // std_init() restores RadioLib's default RXEN table. Reapply the T-Beam
    // 1W's persisted external-LNA selection before receive mode is re-armed.
    if (success && _board->canControlLoRaFemLna()) {
      success = ((CustomSX1262 *)_radio)->setExternalRxLnaEnabled(
          _board->isLoRaFemLnaEnabled());
    }
#endif
    return success && _board->finishRadioHardReset();
  }
  bool supportsRadioDeepInit() const override {
    return supportsRadioHardResetPath();
  }

protected:
  bool applyRxBoostedGainMode(bool en) override {
    return ((CustomSX1262 *)_radio)->setRxBoostedGainMode(en) == RADIOLIB_ERR_NONE;
  }
public:
  bool supportsRxBoostedGainMode() const override { return true; }
  bool getRxBoostedGainMode() const override {
#if MESH_WARM_RX_REFRESH
    if(_rxps_pulse.pending) return _cur_rx_boosted_gain;
#endif
    return ((CustomSX1262 *)_radio)->getRxBoostedGainMode();
  }

  void doResetAGC() override {
    sx126xResetAGC((SX126x *)_radio, getRxBoostedGainMode()
#if defined(MESH_AGC_TIMING_DIAGNOSTICS) && MESH_AGC_TIMING_DIAGNOSTICS
        , &_agc_timing
#endif
    );
  }
};
