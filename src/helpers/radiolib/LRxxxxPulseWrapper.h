#pragma once
#include "RadioLibWrappers.h"
#include "RxpsPulseTiming.h"
#include <math.h>

// LR11xx and LR2021 share their checked status interface and 32.768 kHz RTC,
// but their concrete chip helper encodes different duty-cycle commands.
template<class Driver>
class LRxxxxPulseWrapper : public RadioLibWrapper {
protected:
#if MESH_WARM_RX_REFRESH
  Driver* pulseRadio() const { return static_cast<Driver*>(_radio); }
  uint32_t agcPulseTimerHz() const override { return 32768UL; }
  uint32_t defaultAGCPulseSleepUs() const override {
    return mesh::rxpspulse::bufferedSleepRequestUs(pulseRadio()->agcPulseTransitionUs(),32768,0xFFFFFF);
  }
  // An autonomous scanner can transfer/reacquire checked ownership. A witnessed
  // LR2021 sleep may require its guarded owned cold rebuild; never discard FIFO.
  virtual int16_t finishAGCPulseOwner() { return RADIOLIB_ERR_NONE; }
  void pulseError(int16_t rc,RxpsPulse::Step step) {
    _rxps_pulse.noteError(rc,step);_rxps_pulse.recovering(static_cast<uint32_t>(micros()));
  }
  void retireLRPulse(uint32_t now) {
    const bool packet=hasPendingRadioInterrupt();
    _rxps_pulse.abandon(now);state=packet?1:0;
    _profile_refresh_required=_profile_repair_required=!packet;
    _profile_retry_at=packet?0:millis()+250UL;
    finishAGCScanLease();
  }
  bool startAGCPulse() override {
    auto* radio=pulseRadio();const uint32_t transition=radio->agcPulseTransitionUs();
    const uint32_t sleep=getAGCPulseSleep();
    if(!_rxps_pulse.configure(sleep,transition,32768)) {
      admitAGCPulse(micros());pulseError(RADIOLIB_ERR_INVALID_SLEEP_PERIOD,RxpsPulse::Step::Stage);return true;
    }
    uint32_t irq=0;uint8_t mode=0;int16_t rc=radio->agcPulseStatus(irq,mode);++_rxps_pulse.irq_reads;
    if(rc) { admitAGCPulse(micros());pulseError(rc,RxpsPulse::Step::Irq);return true; }
    if(radio->agcPulseActivity(irq) || hasPendingRadioInterrupt()) { _rxps_pulse.deferred();return false; }
    if(_cur_sf<5 || _cur_sf>12 || !isfinite(_cur_bw) || _cur_bw<=0 || currentPreambleLength()<8) {
      admitAGCPulse(micros());pulseError(RADIOLIB_ERR_INVALID_RX_PERIOD,RxpsPulse::Step::Stage);return true;
    }
    const double symbol_us=double(uint32_t(1)<<_cur_sf)*1000.0/double(_cur_bw);
    const double encoded_sleep=double(_rxps_pulse.sleep_ticks)*1000000.0/32768.0;
    // Retain the LR11xx production extended-preamble guard. This is a receive
    // window, not time asleep; no half-millisecond retune is substituted.
    const double required=(currentPreambleLength()+11.0)*symbol_us+1000.0;
    const double needed=(required-encoded_sleep)*0.5;
    const double raw=ceil((needed>symbol_us?needed:symbol_us)*32768.0/1000000.0);
    admitAGCPulse(micros());_rxps_pulse_retries=0;
    if(!isfinite(raw) || raw<1 || raw>0xFFFFFF || raw*1000000.0/32768.0>3000000.0) {
      pulseError(RADIOLIB_ERR_INVALID_RX_PERIOD,RxpsPulse::Step::Stage);return true;
    }
    _rxps_pulse.rx_ticks=static_cast<uint32_t>(raw);
    _rxps_pulse.rx_us=static_cast<uint32_t>(ceil(raw*1000000.0/32768.0));
    rc=radio->agcPulseStandby();
    if(rc) { pulseError(rc,RxpsPulse::Step::Standby);return true; }
    rc=radio->agcPulseStatus(irq,mode);++_rxps_pulse.irq_reads;
    if(rc) { pulseError(rc,RxpsPulse::Step::Irq);return true; }
    if(radio->agcPulsePacketDone(irq) || hasPendingRadioInterrupt()) {
      ++_rxps_pulse.aborts;_rxps_pulse.packetOwned();state=1;return true;
    }
    if(radio->agcPulseActivity(irq)) { ++_rxps_pulse.aborts;_rxps_pulse.cancelRequest();_rxps_pulse.phase=RxpsPulse::Phase::Stop;return true; }
    rc=radio->agcPulseStage();
    if(rc) { pulseError(rc,RxpsPulse::Step::Stage);return true; }
    const uint32_t arm_started=static_cast<uint32_t>(micros());
    rc=radio->agcPulseArm(_rxps_pulse.rx_ticks,_rxps_pulse.sleep_ticks);
    const uint32_t arm_finished=static_cast<uint32_t>(micros());
    if(rc) { pulseError(rc,RxpsPulse::Step::Arm);return true; }
    const uint64_t budget=2ULL*_rxps_pulse.rx_us+sleep+radio->agcPulsePacketBudgetUs()+2000000ULL;
    _rxps_pulse.armed(arm_finished,uint32_t(budget<60000000ULL?budget:60000000ULL),arm_finished-arm_started);return true;
  }
  void serviceAGCPulse() override {
    auto* radio=pulseRadio();const uint32_t now=micros();
    if(_rxps_pulse.waiting()) {
      // GPIO only, including the entire asserted-BUSY sleep interval.
      _rxps_pulse.sample(now,isChipBusy(),hasPendingRadioInterrupt());
      if(_rxps_pulse.waiting()) return;
    }
    if(_rxps_pulse.phase==RxpsPulse::Phase::Packet) return;
    if(_rxps_pulse.phase==RxpsPulse::Phase::Recovery && isChipBusy()) {
      if(now-_rxps_pulse.armed_at<10000000UL) return;
      if(!_rxps_pulse_retries) {
        ++_rxps_pulse.forced_wake;++_rxps_pulse_retries;
        const int16_t wake_rc=radio->agcPulseStandby();
        if(wake_rc) _rxps_pulse.noteError(wake_rc,RxpsPulse::Step::Recovery);
        _rxps_pulse.retry_at=now;return;
      }
      if(now-_rxps_pulse.retry_at<2000000UL) return;
      retireLRPulse(now);return;
    }
    if(_rxps_pulse.phase==RxpsPulse::Phase::Recovery && _rxps_pulse_retries>=3) {
      retireLRPulse(now);return;
    }
    int16_t rc=radio->agcPulseStandby();
    if(rc) { ++_rxps_pulse_retries;pulseError(rc,RxpsPulse::Step::Standby);return; }
    uint32_t irq=0;uint8_t mode=0;rc=radio->agcPulseStatus(irq,mode);++_rxps_pulse.irq_reads;
    if(rc) { ++_rxps_pulse_retries;pulseError(rc,RxpsPulse::Step::Irq);return; }
    rc=radio->agcPulseStopClock();
    if(rc) { ++_rxps_pulse_retries;pulseError(rc,RxpsPulse::Step::RtcStop);return; }
    ++_rxps_pulse.rtc;
    if(radio->agcPulsePacketDone(irq) || hasPendingRadioInterrupt()) { _rxps_pulse.packetOwned();state=1;return; }
    if(radio->agcPulseActivity(irq)) { ++_rxps_pulse.aborts;_rxps_pulse.cancelRequest(); }
    _rxps_pulse.rearming();rc=radio->startReceive();
    if(!rc) {
      rc=radio->agcPulseStatus(irq,mode);++_rxps_pulse.irq_reads;
      if(!rc && radio->agcPulsePacketDone(irq)) { _rxps_pulse.cancelRequest();_rxps_pulse.packetOwned();state=1;return; }
      if(!rc && mode!=4) rc=RxpsPulse::ModeError;
      if(!rc) ++_rxps_pulse.verified;
    }
    if(!rc) rc=finishAGCPulseOwner();
    if(rc) { ++_rxps_pulse_retries;pulseError(rc,RxpsPulse::Step::SetRx);return; }
    validateAGCScanLease();_rxps_pulse.receiveEntry(0,micros());state=1;
    _startrx_fails=0;_rx_retry_pending=false;_rx_retry_delay_ms=0;
    _nf_sample_from=millis()+NoiseFloorSettleMillis;finishAGCScanLease();
  }
#endif
public:
  LRxxxxPulseWrapper(Driver& radio,mesh::MainBoard& board):RadioLibWrapper(radio,board) { }
#if MESH_WARM_RX_REFRESH
  bool supportsWarmRxRefresh() const override { return true; }
  uint32_t rxPowerSavingTransitionUs() const override { return pulseRadio()->agcPulseTransitionUs(); }
#endif
};
