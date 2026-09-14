#pragma once
// HIL only. Observe existing SPI reads/clears without changing their bytes.
// Optional early IRQ polling is performed by the channel scheduler, not here.
struct ChannelTraceEvent {
  uint32_t us, a, b;
  uint16_t irq;
  uint8_t channel, kind;
};
struct ChannelTrace {
  enum : uint8_t { Arm=1, Irq=2, Clear=3, Hop=4, Hold=5, Packet=6, Timeout=7 };
  static constexpr unsigned Capacity=4096;
  ChannelTraceEvent events[Capacity];
  bool active=false, enabled=false;
  unsigned size=0, overflow=0;
  uint32_t origin=0, sequence=0, pollAt=0, pollCount=0, pollTotalUs=0, pollMaxUs=0;
  uint16_t irq=0;
  uint32_t rfWord=0, txWord=0, txCommandUs=0;
  uint8_t channel=0;
  void add(uint8_t kind,uint32_t a=0,uint32_t b=0) {
    if(!active) return;
    if(size==Capacity) { ++overflow;return; }
    events[size++]={uint32_t(micros()-origin),a,b,irq,channel,kind};
  }
  void arm(uint32_t seq,uint8_t current,uint8_t expected,uint32_t visitAge) {
    size=overflow=0;origin=micros();sequence=seq;channel=current;irq=0;
    pollAt=origin;pollCount=pollTotalUs=pollMaxUs=0;active=enabled;
    add(Arm,expected,visitAge);
  }
  void observe(const uint8_t* out,size_t len,const uint8_t* in) {
    if(!out || !in) return;
    // Track actual command bytes even outside a capture, not just SW labels.
    if(len==5 && out[0]==0x86)
      rfWord=uint32_t(out[1])<<24 | uint32_t(out[2])<<16 | uint32_t(out[3])<<8 | out[4];
    if(len==4 && out[0]==0x83) { txCommandUs=micros();txWord=rfWord; }
    if(!active) return;
    // Pinned SX126x Module: opcode, status dummy, two big-endian IRQ bytes.
    if(len==4 && out[0]==0x12) {
      const uint16_t value=uint16_t(in[2])<<8 | in[3];
      if(value!=irq) { irq=value;add(Irq); }
    } else if(len==3 && out[0]==0x02 && irq) {
      add(Clear,uint16_t(out[1])<<8 | out[2]);
      // Do not predict the clear's success: next actual read establishes it.
    }
  }
} channelTrace;
