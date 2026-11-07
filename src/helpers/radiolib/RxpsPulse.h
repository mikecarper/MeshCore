#pragma once
#include <stdint.h>
#include <stddef.h>
#include <stdio.h>
#include <string.h>

// One asynchronously observed duty-cycle sleep/wake, not a warm FS refresh.
// BUSY observations are sampled GPIO evidence, not a calibrated power trace.
class RxpsPulse {
public:
  static constexpr int16_t TimeoutError=-11021, WitnessError=-11022, ModeError=-11023;
  enum class Phase:uint8_t { Idle, Arm, Listen, Sleep, Stop, Packet, Rearm, Recovery };
  enum class Result:uint8_t { None, Applied, Deferred, Failed };
  enum class Step:uint8_t { None, Standby, Irq, Stage, Arm, Witness, RtcStop, RtcRead, RtcEvent, SetRx, Mode, Recovery };
  struct Samples {
    uint32_t n=0,last=0,minimum=0,maximum=0;
    void add(uint32_t value) {
#if MESH_COMPACT_STM32_RADIO
      // Compact firmware omits these detailed timing pages. Keep its actual
      // deadline/witness timestamps and success/cycle counters independently.
      (void)value;
#else
      if(!n||value<minimum) minimum=value;
      if(!n||value>maximum) maximum=value;
      last=value;++n;
#endif
    }
  };
  uint32_t req=0,admit=0,defer=0,fail=0,ok=0,cycles=0,aborts=0,missed=0,rearm_fail=0;
  uint32_t rx_us=117844,sleep_us=0,rx_ticks=7542,sleep_ticks=0;
  uint32_t high=0,wake=0,cancelled=0,forced_wake=0;
  uint32_t low=0,early=0,rtc=0,verified=0,irq_reads=0;
  int16_t error=0;
  Step failed_step=Step::None;
  Phase phase=Phase::Idle;
  Samples total,busy_high,recovery;
  bool pending=false,packet=false,cancel=false,had_cycle=false,failed=false,low_seen=false;
  bool host_timed=false;
  uint32_t started=0,armed_at=0,high_at=0,deadline_us=0;
  // Listening and recovery are exclusive phases. Reuse one word for the
  // first-edge bound while listening and the retry timestamp during recovery.
  union { uint32_t retry_at=0; uint32_t _minimum_listen_us; };
private:
  Result _completion=Result::None;
  bool _witness_invalid=false;
  uint32_t _cadence_ms=0,_generation=0,_attempt_generation=0;
public:
  RxpsPulse() {
    // Scalar state has an all-zero representation; restore its two nonzero
    // timing defaults after one bounded clear instead of many literal stores.
    memset(static_cast<void*>(this),0,sizeof(*this));rx_us=117844;rx_ticks=7542;
  }
  bool owns() const { return pending && phase!=Phase::Packet; }
  bool waiting() const { return phase==Phase::Listen || phase==Phase::Sleep; }
  bool configure(uint32_t requested_sleep,uint32_t transition_us,uint32_t timer_hz=64000) {
    if(pending || requested_sleep<=transition_us || !timer_hz) return false;
    const uint64_t ticks=(uint64_t(requested_sleep-transition_us)*timer_hz)/1000000;
    if(!ticks || ticks>0xFFFFFF) return false;
    sleep_us=requested_sleep;sleep_ticks=static_cast<uint32_t>(ticks);return true;
  }
  void request(uint32_t now) { ++req;started=now;failed=false;packet=false;cancel=false;had_cycle=false;_witness_invalid=false; }
  void deferred() { ++defer; }
  void admitted(uint32_t now) { ++admit;pending=true;phase=Phase::Arm;started=now;_attempt_generation=_generation; }
  static constexpr uint32_t clockQuantumUs() {
#if defined(NRF52_PLATFORM)
    // The nRF core falls back to a 1024 Hz RTOS clock when DWT is disabled.
    // A conservative whole millisecond also works when DWT is enabled.
    return 1000;
#else
    return 1;
#endif
  }
  void armed(uint32_t now,uint32_t timeout_us,uint32_t arm_call_us=0,
             uint32_t clock_quantum_us=clockQuantumUs()) {
    armed_at=now;deadline_us=timeout_us;phase=Phase::Listen;low_seen=false;
    // The radio starts counting during the arm command, before its SPI/BUSY
    // return. Include that measured latency and one timestamp quantum in the
    // existing 2 ms observer allowance. This is a sanity bound, not a radio
    // oscillator accuracy specification. Never accept an immediate command
    // BUSY, or infer a cycle from a command that consumed most of RX itself.
    const uint64_t allowance=2000ULL+arm_call_us+clock_quantum_us;
    const uint32_t earliest=allowance<rx_us?rx_us-static_cast<uint32_t>(allowance):0;
    _minimum_listen_us=earliest>rx_us/2?earliest:rx_us/2;
    if(arm_call_us>rx_us/2) _witness_invalid=true;
  }
  // A host-timed device has no autonomous BUSY edge witness. Its caller must
  // read back Sleep before starting this timer, and check RX reentry later.
  void beginHostSleep(uint32_t now,uint32_t minimum_us) {
    host_timed=true;armed_at=high_at=now;deadline_us=minimum_us;phase=Phase::Sleep;
  }
  bool hostSleepElapsed(uint32_t now) const {
    return host_timed && pending && phase==Phase::Sleep && now-armed_at>=deadline_us;
  }
  bool finishHostSleep(uint32_t now) {
    if(!hostSleepElapsed(now)) return false;
    ++cycles;had_cycle=true;phase=Phase::Stop;return true;
  }
  void noteError(int16_t code,Step step) {
    // Retain the first command failure in this attempt across later successes.
    if(!failed) { error=code;failed_step=step; }
    failed=true;
  }
  void cancelRequest() { if(pending && !cancel) { cancel=true;++cancelled; } }
  void cadence(uint32_t ms) {
    if(ms==_cadence_ms) return;
    _cadence_ms=ms;++_generation;_completion=Result::None;cancelRequest();
  }
  void sample(uint32_t now,bool busy,bool irq) {
    if(!waiting()) return;
    // A late observation cannot manufacture a successful cycle. Recovery of
    // an expired high interval is explicitly separate from natural evidence.
    if(now-armed_at>=deadline_us) {
      ++missed;_witness_invalid=true;
      packet=irq && !busy;if(packet) ++aborts;
      phase=busy?Phase::Recovery:Phase::Stop;return;
    }
    if(irq && !busy) { packet=true;++aborts;phase=Phase::Stop;return; }
    if(phase==Phase::Listen && !busy && !low_seen) { low_seen=true;++low; }
    if(phase==Phase::Listen && busy) {
      ++high;high_at=now;phase=Phase::Sleep;
      if(!low_seen || now-armed_at<_minimum_listen_us) {
        ++early;++missed;_witness_invalid=true;
      }
    }
    else if(phase==Phase::Sleep && !busy) {
      ++wake;
      if(!failed && !_witness_invalid && now-high_at>=250 && now-high_at<=sleep_us+2000UL) { ++cycles;had_cycle=true;busy_high.add(now-high_at); }
      else if(!failed && !_witness_invalid) { ++missed;_witness_invalid=true; }
      phase=Phase::Stop;
    }
  }
  void packetOwned() { packet=true;phase=Phase::Packet; }
#if defined(SENSECAP_INDICATOR_LORA)
  bool canReplayStaleIrqWake() const {
    return pending && phase==Phase::Stop && packet && !failed && !cancel
        && low_seen && !had_cycle && !_witness_invalid;
  }
  bool replayStaleIrqWake(uint32_t observed_at) {
    if(!canReplayStaleIrqWake()) return false;
    // Correct exactly the provisional IRQ abort, including uint32 wrap. The
    // original sample timestamp, not later SPI latency, decides the witness.
    --aborts;packet=false;phase=Phase::Sleep;
    sample(observed_at,false,false);
    return true;
  }
#endif
  void rearming() { phase=Phase::Rearm; }
  void recovering(uint32_t now) { phase=Phase::Recovery;retry_at=now; }
  void receiveEntry(int16_t result,uint32_t now) {
    if(!pending) return;
    if(result) { ++rearm_fail;noteError(result,Step::SetRx);phase=Phase::Recovery;return; }
    if(phase!=Phase::Rearm && phase!=Phase::Packet && phase!=Phase::Recovery) return;
    const uint32_t elapsed=now-started;
    if(failed) { ++fail;recovery.add(elapsed);_completion=Result::Failed; }
    else if(had_cycle && !cancel) { ++ok;total.add(elapsed);_completion=Result::Applied; }
    else {
      // Missing/ambiguous GPIO timing is an unconfirmed maintenance attempt,
      // not a command failure. Quietly discard it after checked RX recovery;
      // the scheduler may retry within its bounded scheduling slack.
      ++defer;_completion=Result::Deferred;
    }
    if(_attempt_generation!=_generation || !_cadence_ms) _completion=Result::None;
    pending=false;phase=Phase::Idle;
  }
  void abandon(uint32_t now) {
    if(!pending) return;
    if(!failed) noteError(WitnessError,Step::Recovery);
    ++fail;recovery.add(now-started);pending=false;phase=Phase::Idle;
    _completion=(_attempt_generation==_generation && _cadence_ms)?Result::Failed:Result::None;
  }
  bool hasCompletion() const { return _completion!=Result::None; }
  Result takeCompletion() { const Result result=_completion;_completion=Result::None;return result; }
  static const char* phaseName(Phase p) {
    switch(p) { case Phase::Arm:return "arm";case Phase::Listen:return "listen";case Phase::Sleep:return "sleep";
      case Phase::Stop:return "stop";case Phase::Packet:return "packet";case Phase::Rearm:return "rearm";case Phase::Recovery:return "recovery";default:return "idle"; }
  }
  static const char* stepName(Step s) {
    switch(s) { case Step::Standby:return "standby";case Step::Irq:return "irq";case Step::Stage:return "stage";case Step::Arm:return "arm";
      case Step::Witness:return "witness";case Step::RtcStop:return "rtcstop";case Step::RtcRead:return "rtcread";case Step::RtcEvent:return "rtcevent";
      case Step::SetRx:return "setrx";case Step::Mode:return "mode";case Step::Recovery:return "recovery";default:return "none"; }
  }
  void format(uint8_t page,uint32_t seconds,char* out,size_t cap) const {
#if MESH_COMPACT_STM32_RADIO
    if(page!=0 && page!=3) { snprintf(out,cap,"> unsupported");return; }
#endif
    if(page==1) snprintf(out,cap,"> v=2,n=%lu,last_us=%lu,min_us=%lu,max_us=%lu,pending=%u,phase=%s",
      (unsigned long)total.n,(unsigned long)total.last,(unsigned long)total.minimum,(unsigned long)total.maximum,pending?1U:0U,phaseName(phase));
    else if(page==2) snprintf(out,cap,"> v=2,n=%lu,last_us=%lu,min_us=%lu,max_us=%lu,rearm_fail=%lu,step=%s",
      (unsigned long)recovery.n,(unsigned long)recovery.last,(unsigned long)recovery.minimum,(unsigned long)recovery.maximum,(unsigned long)rearm_fail,stepName(failed_step));
    else if(page==3) snprintf(out,cap,"> v=1,rx=%lu,sleep=%lu,rx_ticks=%lu,sleep_ticks=%lu,cycles=%lu,abort=%lu,miss=%lu",
      (unsigned long)rx_us,(unsigned long)sleep_us,(unsigned long)rx_ticks,(unsigned long)sleep_ticks,(unsigned long)cycles,(unsigned long)aborts,(unsigned long)missed);
    else if(page==4) snprintf(out,cap,"> v=1,high=%lu,wake=%lu,n=%lu,last_us=%lu,min_us=%lu,max_us=%lu,cancel=%lu,forced=%lu",
      (unsigned long)high,(unsigned long)wake,(unsigned long)busy_high.n,(unsigned long)busy_high.last,(unsigned long)busy_high.minimum,(unsigned long)busy_high.maximum,(unsigned long)cancelled,(unsigned long)forced_wake);
    else if(page==5) snprintf(out,cap,"> v=1,low=%lu,early=%lu,rtc=%lu,verify=%lu,irq=%lu",
      (unsigned long)low,(unsigned long)early,(unsigned long)rtc,(unsigned long)verified,(unsigned long)irq_reads);
    else snprintf(out,cap,"> v=2,method=%s,interval=%lu,req=%lu,admit=%lu,defer=%lu,fail=%lu,ok=%lu,err=%d",
      host_timed?"hostsleep":"rxps",(unsigned long)seconds,(unsigned long)req,(unsigned long)admit,(unsigned long)defer,(unsigned long)fail,(unsigned long)ok,(int)error);
  }
};
