#pragma once
#include <stdint.h>
#include <stddef.h>
#include <stdio.h>
#include <string.h>

// One real RXPS operation temporarily extends the primary scan visit. This
// measures scanner absence, including primary listening, not analog RX-off time.
class RxpsPulseScanLease {
public:
  bool active=false, stale=false, fast_retry=false;
  uint32_t generation[2]={0,0}, applied_generation=0, visit_stamp=0;
  uint64_t visit_started_us=0;
  uint16_t preamble=0;
  uint32_t admitted=0, deferred=0, invalidated=0, completed=0;
  uint32_t started=0, last_us=0, max_us=0;

  RxpsPulseScanLease() { memset(static_cast<void*>(this),0,sizeof(*this)); }

  void begin(uint32_t now,uint32_t primary,uint32_t secondary,uint32_t applied,
      uint16_t physical_preamble,uint32_t stamp,uint64_t stamp_us) {
    active=true;stale=false;fast_retry=false;++admitted;started=now;
    generation[0]=primary;generation[1]=secondary;applied_generation=applied;
    preamble=physical_preamble;visit_stamp=stamp;visit_started_us=stamp_us;
  }
  bool validate(bool dual,uint8_t profile,uint32_t primary,uint32_t secondary,
      uint32_t applied,uint16_t physical_preamble) {
    if(!active) return true;
    const bool same=dual && profile==0 && generation[0]==primary
        && generation[1]==secondary && applied_generation==applied && preamble==physical_preamble;
    if(!same && !stale) { stale=true;++invalidated; }
    return !stale;
  }
  void finish(uint32_t now) {
    if(!active) return;
    last_us=now-started;if(last_us>max_us) max_us=last_us;
    ++completed;active=false;
  }
  void format(bool dual,char* out,size_t cap) const {
    snprintf(out,cap,"> v=1,dual=%u,lease=%u,admit=%lu,defer=%lu,stale=%lu,n=%lu,last_us=%lu,max_us=%lu",
        dual?1U:0U,active?1U:0U,(unsigned long)admitted,(unsigned long)deferred,
        (unsigned long)invalidated,(unsigned long)completed,(unsigned long)last_us,(unsigned long)max_us);
  }
};
