#!/usr/bin/env python3
"""Real pre-TX deferral helper with real queues, packet metadata and age policy."""
from pathlib import Path
import os,subprocess,sys,tempfile
root=Path(__file__).resolve().parents[2];sys.path.insert(0,str(root/'test'))
from cpp_source import body
source=(root/'src/Dispatcher.cpp').read_text(encoding='utf8')
method=body(source,'bool Dispatcher::deferLabPreTransmit()').replace('bool Dispatcher::','bool ')
program=r'''
#include <cassert>
#include <helpers/RxReservePacketManager.h>
struct Clock {uint32_t now=100;uint32_t getMillis(){return now;}};
struct Test {
 enum class OutboundCancellation {None,Cancelled};
 mesh::PacketManager* _mgr; mesh::OutboundReservation lab_outbound_reservation={};
 mesh::Packet* outbound=nullptr; Clock clock,*_ms=&clock;
 bool lab_pre_tx_held=false,lab_has_blocked_packets=false,outbound_radio_retry_pending=false,outbound_radio_retry_used=false;
 uint32_t outbound_radio_retry_at=0,outbound_radio_prepare_deadline=0;
 OutboundCancellation outbound_cancellation=OutboundCancellation::None;
 uint32_t getCADFailRetryDelay(){return 200;}
 METHOD
};
int main(){
 { RxReservePacketManager m(4,0); Test t; t._mgr=&m;
   auto* low=m.allocNew(); auto* high=m.allocNew(); assert(low&&high);
   assert(m.queueOutbound(low,4,100)); assert(m.reserveNextOutbound(100,t.lab_outbound_reservation));
   t.outbound=low;t.outbound_radio_retry_used=true;assert(t.deferLabPreTransmit());
   assert(!t.outbound && !t.lab_outbound_reservation.packet && low->w12_post_tx_retry_used);
   assert(low->w12_pre_tx_deadline==30100 && m.queueOutbound(high,0,100));
   assert(m.getNextOutbound(100)==high);m.free(high);
   assert(!m.getNextOutbound(299));
   assert(m.reserveNextOutbound(300,t.lab_outbound_reservation) && t.lab_outbound_reservation.packet==low);
   t.outbound=low;t.clock.now=30100;assert(!t.deferLabPreTransmit());
   assert(t.outbound==low && t.lab_outbound_reservation.packet==low);
   assert(m.releaseOutboundReservation(t.lab_outbound_reservation));m.free(low); }
 { StaticPoolPacketManager m(2);Test t;t._mgr=&m;auto* a=m.allocNew();auto* b=m.allocNew();mesh::Packet extra;
   assert(m.queueOutbound(a,3,100));assert(m.reserveNextOutbound(100,t.lab_outbound_reservation));t.outbound=a;
   assert(m.queueOutbound(b,1,100)); assert(!m.queueOutbound(&extra,1,100));
   assert(t.deferLabPreTransmit() && !t.lab_pre_tx_held && !t.outbound);
   assert(m.getNextOutbound(100)==b);m.free(b);
   assert(m.getNextOutbound(300)==a);m.free(a); }
}
'''.replace('METHOD',method)
with tempfile.TemporaryDirectory() as d:
 cpp=Path(d)/'test.cpp';exe=Path(d)/'test';cpp.write_text(program)
 flags=['-std=c++17','-Wall','-Wextra','-Wno-unused-parameter','-DMESH_W12_PRAM_SEQ_TX_LAB=1']
 if os.name!='nt':flags+=['-fsanitize=address,undefined','-fno-omit-frame-pointer','-fno-pie','-no-pie']
 subprocess.run(['g++',*flags,'-I',str(root/'test/mocks'),'-I',str(root/'src'),str(root/'src/Packet.cpp'),str(root/'src/helpers/StaticPoolPacketManager.cpp'),str(cpp),'-o',str(exe)],check=True)
 subprocess.run([str(exe)],check=True,env=dict(os.environ,ASAN_OPTIONS='detect_leaks=0'))
print('PASS queue-preserving pre-TX retry, post-TX state, expiry and held-owner recovery')
