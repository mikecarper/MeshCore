#pragma once

#include <stdint.h>
#include <stddef.h>
#include <stdio.h>
#include <string.h>

#include <helpers/RadioAGCConfig.h>

// Experimental same-channel RX refresh diagnostics. The existing Dispatcher
// AGC scheduler owns cadence; this class has no independent timer. These are software
// command spans, not physical analog outage or proof of an analog AGC reset.
class WarmRxRefresh {
public:
  static constexpr int16_t ContextError = -11001;
  enum class Outcome : uint8_t { Deferred, Failed, Applied };
  enum class Reason : uint8_t { None, Packet, Busy, State, Policy, Cache, Command };
  enum class Step : uint8_t { None, Fs, Standby, ClearIrq, SetRx, Context };
  struct Samples {
    uint32_t count=0, last=0, minimum=0, maximum=0;
    void add(uint32_t value) {
      if (!count || value<minimum) minimum=value;
      if (!count || value>maximum) maximum=value;
      last=value; ++count;
    }
  };
  uint32_t requests=0, admissions=0, deferrals=0, failures=0, successes=0;
  uint32_t rearm_failures=0, request_us=0;
  int16_t last_error=0;
  Reason reason=Reason::None;
  Step last_failed_step=Step::None;
  bool pending=false, attempting=false, used_fs=false, method_seen=false;
  Samples warm_gap, recovery_gap;
private:
  uint32_t _request_started=0, _transition_started=0, _checked_at=0;
  bool _checked=false;
public:
  WarmRxRefresh() { memset(static_cast<void*>(this),0,sizeof(*this)); }

  void request(uint32_t now_us) { ++requests; _request_started=now_us; _checked=false; reason=Reason::None; }
  void transition(uint32_t now_us,bool fs) {
    ++admissions; _transition_started=now_us; pending=true;
    used_fs=fs; method_seen=true;
  }
  void error(int16_t code,Step step) {
    last_error=code; last_failed_step=step;
  }
  void checkedWarmReceive(uint32_t now_us) { _checked=true; _checked_at=now_us; }
  Outcome finish(Outcome outcome,Reason why,uint32_t now_us) {
    request_us=now_us-_request_started; reason=why;
    if(outcome==Outcome::Applied && (!_checked || !pending)) {
      outcome=Outcome::Failed; reason=Reason::Command; error(ContextError,Step::Context);
    }
    if (outcome==Outcome::Applied) {
      ++successes; warm_gap.add(_checked_at-_transition_started); pending=false;
    } else {
      if (outcome==Outcome::Deferred) ++deferrals; else ++failures;
    }
    return outcome;
  }
  void receiveEntry(int16_t result,uint32_t now_us) {
    if (!pending) return;
    if (result!=0) { ++rearm_failures; return; }
    recovery_gap.add(now_us-_transition_started); pending=false;
  }
  static const char* reasonName(Reason value) {
    switch(value) {
      case Reason::Packet:return "packet"; case Reason::Busy:return "busy";
      case Reason::State:return "state"; case Reason::Policy:return "policy";
      case Reason::Cache:return "cache"; case Reason::Command:return "command";
      default:return "none";
    }
  }
  static const char* stepName(Step value) {
    switch(value) {
      case Step::Fs:return "fs"; case Step::Standby:return "standby";
      case Step::ClearIrq:return "clearirq"; case Step::SetRx:return "setrx";
      case Step::Context:return "context"; default:return "none";
    }
  }
  void format(uint8_t page,uint32_t seconds,char* reply,size_t capacity) const {
    if(page==1) snprintf(reply,capacity,
        "> v=1,n=%lu,last_us=%lu,min_us=%lu,max_us=%lu,pending=%u,mode=%s,reason=%s,request_us=%lu",
        (unsigned long)warm_gap.count,(unsigned long)warm_gap.last,
        (unsigned long)warm_gap.minimum,(unsigned long)warm_gap.maximum,pending?1U:0U,
        !method_seen?"none":used_fs?"fs":"standby",reasonName(reason),
        (unsigned long)request_us);
    else if(page==2) snprintf(reply,capacity,
        "> v=1,n=%lu,last_us=%lu,min_us=%lu,max_us=%lu,rearm_fail=%lu,step=%s",
        (unsigned long)recovery_gap.count,(unsigned long)recovery_gap.last,
        (unsigned long)recovery_gap.minimum,(unsigned long)recovery_gap.maximum,
        (unsigned long)rearm_failures,stepName(last_failed_step));
    else snprintf(reply,capacity,
        "> v=1,method=warm,interval=%lu,req=%lu,admit=%lu,defer=%lu,fail=%lu,ok=%lu,err=%d",
        (unsigned long)seconds,(unsigned long)requests,(unsigned long)admissions,
        (unsigned long)deferrals,(unsigned long)failures,(unsigned long)successes,(int)last_error);
  }
};
