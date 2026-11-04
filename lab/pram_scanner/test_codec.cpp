#include "helpers/radiolib/W12PramScannerCodec.h"
#include <cassert>
using namespace mesh::w12pram;
int main() {
  Profile a={910525000,62500,7,5,32,8}, b={911150000,250000,7,5,32,9};
  Packed x{},y{};
  assert(pack(a,x) && pack(b,y));
  assert(x.frequency==910525000 && x.modem==0x203C1073);
  assert(y.frequency==911150000 && y.modem==0x203C1075);
  b.sf=8;b.cr=7;assert(pack(b,y) && y.modem==0x20403085);
  b.bw_hz=100000;assert(!pack(b,y));
  b.bw_hz=250000;b.sf=9;assert(!pack(b,y));
  b.sf=7;b.preamble=0;assert(!pack(b,y));
  b.preamble=32;b.freq_hz=2400000000u;assert(!pack(b,y));
  uint8_t raw[40]={4,1,0,1,7,0,0,0,8,0,0,0,9,0,0,0,10,0,0,0,11,0,0,0,12,0,0,0};
  Status stop={};stop.stopped=true;assert(pauseResult(stop)==PauseResult::Stopped);
  stop.hooked=1;assert(pauseResult(stop)==PauseResult::Invalid);
  stop.stopped=false;stop.state=1;assert(pauseResult(stop)==PauseResult::Busy);
  stop.state=0;assert(pauseResult(stop)==PauseResult::Invalid);
  assert(mayResume(true,false,true,false,false,false,true));
  assert(!mayResume(false,false,true,false,false,false,true));
  assert(!mayResume(true,true,true,false,false,false,true));
  assert(!mayResume(true,false,false,false,false,false,true));
  assert(!mayResume(true,false,true,true,false,false,true));
  assert(!mayResume(true,false,true,false,true,false,true));
  assert(!mayResume(true,false,true,false,false,true,true));
  assert(!mayResume(true,false,true,false,false,false,false));
  uint8_t req[9]={};benchRequest(1,y,req);assert(req[0]==6 && req[1]==1 && req[2]==0x36 && req[6]==0x85 && req[7]==0x30 && req[8]==32);
  Status late={};late.hooked=1;late.state=1;assert(classify(late)==RxEvent::Ignore);
  late.state=4;late.last_hit=1;late.irq=1u<<18;assert(classify(late)==RxEvent::Deliver);
  late.last_hit=2;assert(classify(late)==RxEvent::Invalid);
  late.last_hit=1;late.irq=0;assert(classify(late)==RxEvent::Invalid);
  late.state=1;late.hooked=0;assert(classify(late)==RxEvent::Invalid);
  raw[3]=0x80; Status stopped=decode(raw); assert(stopped.stopped && !stopped.hooked); raw[3]=1;
  Status s=decode(raw); assert(s.state==4 && s.last_hit==1 && s.current==0 && s.hooked==1 && s.cads==7 && s.falses==8 && s.lost==9 && s.packets==10 && s.rounds==11 && s.events==12);
}
