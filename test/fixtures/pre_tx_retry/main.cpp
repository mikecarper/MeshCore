#include <Dispatcher.h>
#include <helpers/StaticPoolPacketManager.h>
#include <cassert>
#include <cstdio>
#include <vector>
class Clock : public mesh::MillisecondClock { public: uint32_t now=100; unsigned long getMillis() override {return now;} };
class Radio : public mesh::Radio {
 public:
  enum Outcome { Busy, Error, Unknown, Success };
  std::vector<Outcome> outcomes; unsigned calls=0; bool sending=false;
  mesh::PreTransmitBlock block=mesh::PreTransmitBlock::None;
  std::vector<uint8_t> ids;
  void clearPreTransmitBlock() override {block=mesh::PreTransmitBlock::None;}
  mesh::PreTransmitBlock takePreTransmitBlock() override {auto b=block;block=mesh::PreTransmitBlock::None;return b;}
  int recvRaw(uint8_t*,int) override{return 0;}
  uint32_t getEstAirtimeFor(int) override{return 1;}
  float packetScore(float,int) override{return 0;}
  bool isInRecvMode() const override{return true;}
  bool startSendRaw(const uint8_t* raw,int len) override {
    assert(len>=3);ids.push_back(raw[len-1]);Outcome o=outcomes.at(calls++);
    if(o==Busy)block=mesh::PreTransmitBlock::Busy;
    else if(o==Error)block=mesh::PreTransmitBlock::Error;
    if(o==Success){sending=true;return true;}return false;
  }
  bool isSendComplete() override{return sending;}
  void onSendFinished() override{sending=false;}
};
class Dispatcher : public mesh::Dispatcher {
 public:
  unsigned failed=0,complete=0;
  Dispatcher(Radio& r,Clock& c,StaticPoolPacketManager& m):mesh::Dispatcher(r,c,m){}
  mesh::DispatcherAction onRecvPacket(mesh::Packet*) override{return ACTION_RELEASE;}
  void onSendFail(mesh::Packet*) override{++failed;}
  void onSendComplete(mesh::Packet*) override{++complete;}
};
struct Fixture {
 Clock c; Radio r; StaticPoolPacketManager m{5}; Dispatcher d{r,c,m};
 Fixture(){d.begin();}
 mesh::Packet* queue(uint8_t id,uint8_t pri){auto*p=d.obtainNewPacket();assert(p);p->header=ROUTE_TYPE_DIRECT;p->payload_len=1;p->payload[0]=id;assert(d.sendPacket(p,pri));return p;}
 void loop(uint32_t t){c.now=t;d.loop();}
};
int main(){
 {Fixture f;f.r.outcomes={Radio::Busy,Radio::Success,Radio::Success};auto*low=f.queue(11,4);
  f.loop(101);assert(f.r.calls==1 && f.m.getOutboundTotal()==1 && !f.d.failed && low->w12_pre_tx_deadline==30101);
  f.queue(22,0);f.loop(102);assert(f.r.calls==2 && f.r.ids[1]==22);f.loop(103);assert(f.d.complete==1);
  f.loop(302);assert(f.r.calls==3 && f.r.ids[2]==11);f.loop(303);assert(f.d.complete==2 && f.d.failed==0);}
 {Fixture f;f.r.outcomes={Radio::Unknown,Radio::Busy,Radio::Unknown};auto*p=f.queue(33,1);
  f.loop(101);assert(f.r.calls==1 && f.m.getOutboundTotal()==0);
  f.loop(302);assert(f.r.calls==2 && f.m.getOutboundTotal()==1 && p->w12_post_tx_retry_used);
  f.loop(503);assert(f.r.calls==3 && f.d.failed==1);f.loop(1000);assert(f.r.calls==3);}
 {Fixture f;f.r.outcomes={Radio::Error};f.queue(44,1);f.loop(101);assert(!f.d.failed && f.m.getOutboundTotal()==1);
  f.loop(30101);assert(f.d.failed==1 && f.r.calls==1);}
}
