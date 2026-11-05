#pragma once
#include <stdint.h>
#include <stddef.h>
#include <stdio.h>

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
    void add(uint32_t value) { if(!n||value<minimum) minimum=value; if(!n||value>maximum) maximum=value; last=value;++n; }
  };
  uint32_t req=0,admit=0,defer=0,fail=0,ok=0,cycles=0,aborts=0,missed=0,rearm_fail=0;
  uint32_t rx_us=117844,sleep_us=4000,rx_ticks=7542,sleep_ticks=89;
  uint32_t high=0,wake=0,cancelled=0,forced_wake=0;
  uint32_t low=0,early=0,rtc=0,verified=0,irq_reads=0;
  int16_t error=0;
  Step failed_step=Step::None;
  Phase phase=Phase::Idle;
  Samples total,busy_high,recovery;
  bool pending=false,packet=false,cancel=false,had_cycle=false,failed=false,low_seen=false;
  uint32_t started=0,armed_at=0,high_at=0,deadline_us=0,retry_at=0;
private:
  Result _completion=Result::None;
  uint32_t _cadence_ms=0,_generation=0,_attempt_generation=0;
public:
  bool owns() const { return pending && phase!=Phase::Packet; }
  bool waiting() const { return phase==Phase::Listen || phase==Phase::Sleep; }
  bool configure(uint32_t requested_sleep,uint32_t transition_us) {
    if(pending || requested_sleep<=transition_us) return false;
    const uint32_t ticks=(uint32_t)(((uint64_t)(requested_sleep-transition_us)*8)/125);
    if(!ticks || ticks>0xFFFFFF) return false;
    sleep_us=requested_sleep;sleep_ticks=ticks;return true;
  }
  void request(uint32_t now) { ++req;started=now;failed=false;packet=false;cancel=false;had_cycle=false; }
  void deferred() { ++defer; }
  void admitted(uint32_t now) { ++admit;pending=true;phase=Phase::Arm;started=now;_attempt_generation=_generation; }
  void armed(uint32_t now,uint32_t timeout_us) { armed_at=now;deadline_us=timeout_us;phase=Phase::Listen;low_seen=false; }
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
      ++missed;noteError(TimeoutError,Step::Witness);
      packet=irq && !busy;if(packet) ++aborts;
      phase=busy?Phase::Recovery:Phase::Stop;return;
    }
    if(irq && !busy) { packet=true;++aborts;phase=Phase::Stop;return; }
    if(phase==Phase::Listen && !busy && !low_seen) { low_seen=true;++low; }
    if(phase==Phase::Listen && busy) {
      ++high;high_at=now;phase=Phase::Sleep;
      // The 2ms allowance covers command-return/timer-origin uncertainty,
      // not permission to count the command's initial BUSY as a sleep window.
      if(!low_seen || now-armed_at<(rx_us>2000?rx_us-2000:0)) {
        ++early;++missed;noteError(WitnessError,Step::Witness);
      }
    }
    else if(phase==Phase::Sleep && !busy) {
      ++wake;
      if(!failed && now-high_at>=250 && now-high_at<=sleep_us+2000UL) { ++cycles;had_cycle=true;busy_high.add(now-high_at); }
      else if(!failed) { ++missed;noteError(WitnessError,Step::Witness); }
      phase=Phase::Stop;
    }
  }
  void packetOwned() { packet=true;phase=Phase::Packet; }
  void rearming() { phase=Phase::Rearm; }
  void recovering(uint32_t now) { phase=Phase::Recovery;retry_at=now; }
  void receiveEntry(int16_t result,uint32_t now) {
    if(!pending) return;
    if(result) { ++rearm_fail;noteError(result,Step::SetRx);phase=Phase::Recovery;return; }
    if(phase!=Phase::Rearm && phase!=Phase::Packet && phase!=Phase::Recovery) return;
    const uint32_t elapsed=now-started;
    if(failed) { ++fail;recovery.add(elapsed);_completion=Result::Failed; }
    else if(had_cycle && !cancel) { ++ok;total.add(elapsed);_completion=Result::Applied; }
    else { ++defer;_completion=Result::Deferred; }
    if(_attempt_generation!=_generation || !_cadence_ms) _completion=Result::None;
    pending=false;phase=Phase::Idle;
  }
  void abandon(uint32_t now) {
    if(!pending) return;
    if(!failed) noteError(WitnessError,Step::Recovery);
    ++fail;recovery.add(now-started);pending=false;phase=Phase::Idle;
    _completion=(_attempt_generation==_generation && _cadence_ms)?Result::Failed:Result::None;
  }
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
    else snprintf(out,cap,"> v=2,method=rxps,interval=%lu,req=%lu,admit=%lu,defer=%lu,fail=%lu,ok=%lu,err=%d",
      (unsigned long)seconds,(unsigned long)req,(unsigned long)admit,(unsigned long)defer,(unsigned long)fail,(unsigned long)ok,(int)error);
  }
};
