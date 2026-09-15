#pragma once
#include <stdint.h>

// Lab scheduling only: RX is already active during post-BUSY settling.
struct ChannelVisitClock {
  uint32_t rxStamp=0, listenAt=0;
  void begin(uint32_t rxStarted,uint32_t settledAt,bool delayEnabled) {
    rxStamp=rxStarted;listenAt=delayEnabled?settledAt:rxStarted;
  }
  uint32_t dwell(uint32_t now,uint32_t rxStarted) {
    // Receiving/processing a packet restarts ordinary RX, not a channel hop.
    if(rxStarted!=rxStamp) { rxStamp=rxStarted;listenAt=rxStarted; }
    return uint32_t(now-listenAt);
  }
};

struct ChannelCycleClock {
  bool started=false, clean=false;
  uint32_t origin=0;
  bool hop(unsigned next,uint32_t now,bool held,uint32_t& elapsed) {
    if(held) clean=false;
    if(next!=0) return false;
    const bool sample=started && clean;
    elapsed=uint32_t(now-origin);
    started=true;clean=true;origin=now;
    return sample;
  }
};
