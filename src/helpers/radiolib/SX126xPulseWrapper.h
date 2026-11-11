#pragma once
#include "RadioLibWrappers.h"
#include "SX126xReceiveMode.h"
#include "RxpsPulseTiming.h"
#include "SX126xRxpsPulse.h"

// The checked SX126x duty-cycle sequence is shared by concrete drivers. Each
// driver stages its complete existing LoRa tuple; no fast-retune cache or FS
// shortcut is required. BUSY always comes from that driver's actual HAL.
template <typename Driver>
class SX126xPulseWrapper : public RadioLibWrapper {
public:
  SX126xPulseWrapper(Driver& radio, mesh::MainBoard& board)
      : RadioLibWrapper(radio, board) {}
#if MESH_WARM_RX_REFRESH
  bool supportsWarmRxRefresh() const override { return true; }
  uint32_t rxPowerSavingTransitionUs() const override {
    const uint32_t tcxo=static_cast<const Driver*>(_radio)->getTcxoDelay();
    return tcxo>UINT32_MAX-1000UL ? UINT32_MAX : tcxo+1000UL;
  }
protected:
  int8_t readReceiveMode() override {
    return _rxps_pulse.owns() ? -1 : sx126xReceiveMode(static_cast<SX126x*>(_radio));
  }
public:
  bool isChipBusy() override {
    auto* mod=static_cast<Driver*>(_radio)->getMod();
    return mod->getGpio()!=RADIOLIB_NC && mod->hal->digitalRead(mod->getGpio());
  }
protected:
  uint32_t defaultAGCPulseSleepUs() const override {
    const uint32_t tcxo=static_cast<const Driver*>(_radio)->getTcxoDelay();
    if(tcxo>UINT32_MAX-1000UL) return 0;
    return mesh::rxpspulse::bufferedSleepRequestUs(
        tcxo+1000UL,64000UL,0xFFFFFFUL);
  }
  static bool pulseActivity(uint16_t irq) {
    return irq & (RADIOLIB_SX126X_IRQ_RX_DONE | RADIOLIB_SX126X_IRQ_PREAMBLE_DETECTED
        | RADIOLIB_SX126X_IRQ_HEADER_VALID | RADIOLIB_SX126X_IRQ_SYNC_WORD_VALID);
  }
  void pulseFailure(int16_t rc,RxpsPulse::Step step) {
    _rxps_pulse.noteError(rc,step);_rxps_pulse.recovering(static_cast<uint32_t>(micros()));
  }
  bool startAGCPulse() override {
    auto* radio=static_cast<Driver*>(_radio);
    auto* mod=radio->getMod();
    const uint32_t transition=rxPowerSavingTransitionUs();
    // Re-resolve after recovery too: an oscillator fallback must not retain
    // another board's or an earlier oscillator mode's absolute duration.
    const uint32_t sleep=getAGCPulseSleep();
    if(!_rxps_pulse.configure(sleep,transition)) {
      admitAGCPulse(static_cast<uint32_t>(micros()));
      pulseFailure(RADIOLIB_ERR_INVALID_SLEEP_PERIOD,RxpsPulse::Step::Stage);return true;
    }
    uint16_t irq=0;
    auto probe=mesh::rxpspulse::readIrq(mod,irq);++_rxps_pulse.irq_reads;
    if(!probe.ok()) {
      admitAGCPulse(static_cast<uint32_t>(micros()));_rxps_pulse_retries=0;
      pulseFailure(probe.error,RxpsPulse::Step::Irq);return true;
    }
    if(pulseActivity(irq) || hasPendingRadioInterrupt()) {
      // No command was admitted; do not stop or acknowledge a packet owner.
      _rxps_pulse.deferred();return false;
    }
    if(_cur_sf<5 || _cur_sf>12 || !mesh::rxpspulse::finiteFloat(_cur_bw) || _cur_bw<=0 || sleep<=transition) {
      admitAGCPulse(static_cast<uint32_t>(micros()));
      pulseFailure(RADIOLIB_ERR_INVALID_RX_PERIOD,RxpsPulse::Step::Stage);return true;
    }
    const float symbol_us=(float(uint32_t(1)<<_cur_sf)*1000.0f)/_cur_bw;
    const float encoded_sleep=float(_rxps_pulse.sleep_ticks)*15.625f;
    const float sync=_cur_sf<=6?RX_POWERSAVING_SYNC_SYMBOLS_LOW_SF:RX_POWERSAVING_SYNC_SYMBOLS;
    const uint16_t preamble=currentPreambleLength();
    const float required=(preamble+sync+RX_POWERSAVING_HEADER_SYMBOLS+RX_POWERSAVING_TIMER_SLACK_SYMBOLS)*symbol_us;
    const float needed=(required-encoded_sleep)*0.5f;
    if(preamble<8 || !mesh::rxpspulse::finiteFloat(required) || !mesh::rxpspulse::finiteFloat(needed) || needed>3000000.0f) {
      admitAGCPulse(static_cast<uint32_t>(micros()));
      pulseFailure(RADIOLIB_ERR_INVALID_RX_PERIOD,RxpsPulse::Step::Stage);return true;
    }
    const uint32_t rx=snapRxPowerSavingUpToTick(ceilRxPowerSavingValue(needed>symbol_us?needed:symbol_us));
    mesh::rxpspulse::Periods periods;
    const auto valid=mesh::rxpspulse::encodePeriods(rx,sleep,transition,periods);
    admitAGCPulse(static_cast<uint32_t>(micros()));_rxps_pulse_retries=0;
    // Reject unusable geometry once per scheduled attempt, staying awake.
    if(!mesh::rxpspulse::finiteFloat(symbol_us) || !valid.ok()
        || rx>3000000UL) {
      pulseFailure(RADIOLIB_ERR_INVALID_RX_PERIOD,RxpsPulse::Step::Stage);return true;
    }
    _rxps_pulse.rx_us=rx;_rxps_pulse.rx_ticks=periods.rxTicks;_rxps_pulse.sleep_ticks=periods.sleepTicks;
    radio->invalidatePulseReceiveState();
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
    const uint32_t arm_started=static_cast<uint32_t>(micros());
    result=mesh::rxpspulse::armDutyCycle(mod,periods);
    const uint32_t arm_finished=static_cast<uint32_t>(micros());
    if(!result.ok()) { pulseFailure(result.error,RxpsPulse::Step::Arm);return true; }
    // A preamble can extend the window and become a maximum-sized packet.
    // Preserve that owner before declaring a missing natural cycle. These are
    // cached PHY airtime bounds, with no SPI or NSS during the lease.
    const uint64_t budget=2ULL*rx+sleep+radio->pulsePacketBudgetUs()+2000000ULL;
    _rxps_pulse.armed(arm_finished,uint32_t(budget<60000000ULL?budget:60000000ULL),arm_finished-arm_started);
    return true;
  }
  int16_t pulseContinuous() {
    auto* radio=static_cast<Driver*>(_radio);
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
    finishAGCScanLease();
  }
  void serviceAGCPulse() override {
    auto* radio=static_cast<Driver*>(_radio);auto* mod=radio->getMod();
    const uint32_t now=static_cast<uint32_t>(micros());
#if defined(SENSECAP_INDICATOR_LORA)
    bool stale_irq_wake=false;
    uint32_t input_generation=0;
#endif
    if(_rxps_pulse.waiting()) {
      // GPIO only: any SPI/NSS transaction would wake and invalidate sleep.
#if defined(SENSECAP_INDICATOR_LORA)
      const auto before=_rxps_pulse.phase;
      input_generation=agcInputReadGeneration();
      bool busy=false,irq=false;
      _rxps_pulse.sample(now,(busy=isChipBusy()),(irq=hasPendingRadioInterrupt()));
      stale_irq_wake=before==RxpsPulse::Phase::Sleep && irq && !busy
          && _rxps_pulse.canReplayStaleIrqWake()
          && prepareStaleAGCIrqWake(now,input_generation);
#else
      _rxps_pulse.sample(now,isChipBusy(),hasPendingRadioInterrupt());
#endif
      if(_rxps_pulse.waiting()) return;
    }
    if(_rxps_pulse.phase==RxpsPulse::Phase::Packet) return;
    if(_rxps_pulse.phase==RxpsPulse::Phase::Recovery) {
      if(isChipBusy()) {
        if(now-_rxps_pulse.armed_at<10000000UL) return;
        // Bounded exceptional wake, never credited as natural sleep evidence.
        if(!_rxps_pulse_retries) {
          ++_rxps_pulse.forced_wake;++_rxps_pulse_retries;
          uint8_t status=0;
          const int16_t wake_rc=sx126xReadStatus(mod,status);
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
    radio->invalidatePulseReceiveState();
    mod->setRfSwitchState(Module::MODE_IDLE);
    const auto stop=mesh::rxpspulse::standbyRc(mod);
    if(!stop.ok()) { pulseFailure(stop.error,RxpsPulse::Step::Standby);return; }
    uint16_t irq=0;const auto probe=mesh::rxpspulse::readIrq(mod,irq);++_rxps_pulse.irq_reads;
    if(!probe.ok()) { pulseFailure(probe.error,RxpsPulse::Step::Irq);return; }
#if defined(SENSECAP_INDICATOR_LORA)
    if(stale_irq_wake && irq==0 && _rxps_pulse.canReplayStaleIrqWake()
        && retireStaleAGCIrqWake(input_generation)) {
      _rxps_pulse.replayStaleIrqWake(now);
    }
#endif
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
    validateAGCScanLease();
    _rxps_pulse.receiveEntry(rc,static_cast<uint32_t>(micros()));
    if(rc) { _rxps_pulse.retry_at=static_cast<uint32_t>(micros());return; }
    state=1;_startrx_fails=0;_rx_retry_pending=false;_rx_retry_delay_ms=0;
    // Keep the same collector/cache selection; apply the usual RX settle.
    _nf_sample_from=millis()+NoiseFloorSettleMillis;
    finishAGCScanLease();
  }
#endif
};
