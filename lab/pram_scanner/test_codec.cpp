#include "helpers/radiolib/W12PramScannerCodec.h"
#include "helpers/radiolib/W12RxDutyTiming.h"
#include "helpers/radiolib/W12PramLabTrace.h"
#include <cassert>
#include <cstring>
#include <initializer_list>
using namespace mesh::w12pram;
int main() {
  assert(std::strcmp(recoveryOriginName(RecoveryOrigin::None),"none")==0);
  assert(std::strcmp(recoveryOriginName(RecoveryOrigin::Other),"other")==0);
  assert(std::strcmp(recoveryOriginName(RecoveryOrigin::NoCad),"no-cad")==0);
  assert(std::strcmp(recoveryOriginName(RecoveryOrigin::StatusPoll),"status-poll")==0);
  assert(std::strcmp(recoveryOriginName(RecoveryOrigin::RxStatus),"rx-status")==0);
  assert(std::strcmp(recoveryOriginName(RecoveryOrigin::RxResume),"rx-resume")==0);
  const uint8_t stat[]={0x04, 0x04};
  auto& trace=mesh::w12pram::spiTrace(); trace.reset(); trace.add(0x1234,stat,sizeof stat); assert(trace.count==0);
  trace.active=true; for (unsigned i=0;i<10;i++) trace.add(uint16_t(0x100+i),stat,sizeof stat);
  assert(trace.count==10 && trace.first[0]==0x100 && trace.first[7]==0x107);
  trace.reset(); assert(!trace.active && trace.count==0 && trace.first[0]==0);
  mesh::w12rxps::Timing timing{};
  assert(mesh::w12rxps::encode(65625,60000,1000,timing));
  assert(timing.rx_ticks==2150 && timing.cycle_ticks==4083);
  assert(mesh::w12rxps::encode(1000000,1000000,1000,timing));
  assert(timing.rx_ticks==32768 && timing.cycle_ticks==65503);
  assert(!mesh::w12rxps::encode(0,60000,1000,timing));
  assert(!mesh::w12rxps::encode(65625,1000,1000,timing));
  assert(!mesh::w12rxps::encode(UINT32_MAX,UINT32_MAX,1000,timing));
  Profile a={910525000,62500,7,5,32,8}, b={911150000,250000,7,5,32,9};
  Packed x{},y{};
  assert(pack(a,x) && pack(b,y));
  assert(x.frequency==910525000 && x.modem==0x203C1073);
  assert(y.frequency==911150000 && y.modem==0x203C1075);
  a.cr=4; assert(pack(a,x) && x.modem==0x203C0073); // Wire CR zero is 4/4.
  a.cr=3; assert(!pack(a,x));
  a.cr=9; assert(!pack(a,x));
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
  Status stalled{}; stalled.state=3;
  assert(mayRecoverStalledScan(stalled,false,false)); // RX stuck without an event
  assert(!mayRecoverStalledScan(stalled,true,false)); // queued software ISR
  assert(!mayRecoverStalledScan(stalled,false,true)); // physical IRQ asserted
  stalled.state=4; stalled.irq=1u<<18;
  assert(!mayRecoverStalledScan(stalled,false,false)); // parked packet owns FIFO
  stalled.state=3;
  assert(!mayRecoverStalledScan(stalled,false,false)); // RxDone raced park
  stalled.state=4; stalled.irq=0;
  assert(mayRecoverStalledScan(stalled,false,false)); // parked with no packet
  stalled.state=3; stalled.hooked=1; stalled.timeout_known=true; stalled.timeout_armed=false;
  assert(mayRepairHeaderTimer(stalled,false,false,false));
  assert(!mayRepairHeaderTimer(stalled,true,false,false));
  assert(!mayRepairHeaderTimer(stalled,false,true,false));
  assert(!mayRepairHeaderTimer(stalled,false,false,true));
  for (unsigned bit : {6u,9u,18u,21u,22u,23u}) {
    stalled.irq=1u<<bit; assert(!mayRepairHeaderTimer(stalled,false,false,false));
  }
  stalled.irq=0; stalled.timeout_armed=true;
  assert(!mayRepairHeaderTimer(stalled,false,false,false));
  assert(idleBudget(true,true,false,true,100,0,10)==10);
  assert(idleBudget(true,true,false,true,997,0,10)==3);
  assert(idleBudget(true,true,false,true,1000,0,10)==0);
  assert(idleBudget(true,true,false,true,5,UINT32_MAX-4,10)==10); // wrap
  assert(idleBudget(false,true,false,true,100,0,10)==0);
  assert(idleBudget(true,false,false,true,100,0,10)==0);
  assert(idleBudget(true,true,true,true,100,0,10)==0);
  assert(idleBudget(true,true,false,false,100,0,10)==0);
  uint8_t req[9]={};benchRequest(1,y,req);assert(req[0]==6 && req[1]==1 && req[2]==0x36 && req[6]==0x85 && req[7]==0x30 && req[8]==32);
  Status late={};late.hooked=1;late.state=1;assert(classify(late)==RxEvent::Ignore);
  late.state=4;late.last_hit=1;late.irq=1u<<18;assert(classify(late)==RxEvent::Deliver);
  late.last_hit=2;assert(classify(late)==RxEvent::Invalid);
  late.last_hit=1;late.irq=0;assert(classify(late)==RxEvent::Invalid);
  late.state=1;late.hooked=0;assert(classify(late)==RxEvent::Invalid);
  raw[3]=0x80; Status stopped=decode(raw); assert(stopped.stopped && !stopped.hooked);
  assert(timeoutObservation(stopped)==0); // old blob has no timer observation
  raw[3]=0x05; Status timer_off=decode(raw);
  assert(timer_off.hooked && !timer_off.stopped && timeoutObservation(timer_off)==1);
  raw[3]=0x87; Status timer_on_stopped=decode(raw);
  assert(timer_on_stopped.hooked && timer_on_stopped.stopped && timeoutObservation(timer_on_stopped)==2);
  raw[3]=0x02; Status reserved=decode(raw);
  assert(!reserved.hooked && !reserved.stopped && timeoutObservation(reserved)==0); // validity bit wins
  raw[3]=0x0F; Status repaired=decode(raw);
  assert(repaired.hooked && repaired.timeout_known && repaired.timeout_armed && repaired.timer_repaired);
  assert(std::strcmp(timeoutObservationName(0),"unknown")==0);
  assert(std::strcmp(timeoutObservationName(1),"off")==0);
  assert(std::strcmp(timeoutObservationName(2),"armed")==0);
  raw[3]=1;
  Status s=decode(raw); assert(s.state==4 && s.last_hit==1 && s.current==0 && s.hooked==1 && s.cads==7 && s.falses==8 && s.lost==9 && s.packets==10 && s.rounds==11 && s.events==12);
}
