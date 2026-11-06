"""Exercise the production duplicate hook and flood policy in a receive pipeline."""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]

HARNESS = r'''
#include <cassert>
#include <cstdint>
#include <cstring>
#include <new>
#include <helpers/FloodProfilePolicy.h>
constexpr int ACTION_RELEASE=0, ACTION_FORWARD=1, PAYLOAD_TYPE_OTA=9, PAYLOAD_TYPE_TRACE=10;
using DispatcherAction=int;
struct Packet {
  uint8_t radio_profile, type, id; bool flood=true, forwarded=false;
  bool isRouteFlood() const {return flood;}
  bool isRouteDirect() const {return !flood;}
  uint8_t getPayloadType() const {return type;}
  void calculatePacketHash(uint8_t* out) const {memset(out,0,MAX_HASH_SIZE);out[0]=type;out[1]=id;}
};
struct Policy {bool enabled[2]={false,true}; bool floodProfileAllowed(uint8_t p)const{return p<2&&enabled[p];}};
struct Cli {Policy policy; bool batteryAllowsForward(bool)const{return true;} Policy& radioProfiles(){return policy;}};
struct Tables {bool seen=false; bool wasSeen(Packet*)const{return seen;}};
struct Mesh {
  Tables tables; Tables* _tables=&tables; Cli _cli; Policy& policy=_cli.policy;
  mesh::flood_profiles::DeferredFloods* deferred_floods_=nullptr;
  struct {bool disable_fwd=false; int trace_when_repeat_off=0;} _prefs;
  bool filter=false, global_off=false, scoped=false, hop_block=false, queue_ok=true;
  int deliveries=0, forwards=0, dropped=0; uint32_t now=1;
  ~Mesh(){delete deferred_floods_;}
  uint32_t millis() const {return now;}
  bool filterRecvFloodPacket(Packet*) {return filter;}
  bool deferredFloodCandidate(Packet* p,uint8_t profile,bool consume) {return deferred_floods_&&deferred_floods_->match(p,policy,now,profile,consume);}
  bool allowPacketForward(const Packet* packet) {
@FORWARD_GATE@
    return true;
  }
  DispatcherAction routeRecvPacket(Packet* p) {
    if(global_off||scoped||hop_block)return ACTION_RELEASE;
    if(!allowPacketForward(p))return ACTION_RELEASE;
    return ACTION_FORWARD;
  }
  DispatcherAction receive(Packet* pkt, bool locally_consumed=false, bool valid_advert=true) {
@HOOK@
    if (!_tables->wasSeen(pkt)) {
      _tables->seen=true;
      if (valid_advert) { ++deliveries; if(!locally_consumed) return routeRecvPacket(pkt); }
    }
    return ACTION_RELEASE;
  }
  void dispatch(Packet* p, DispatcherAction action) {
    if(action!=ACTION_FORWARD)return;
    if(!queue_ok){++dropped;return;}
    ++forwards; p->forwarded=true;
    deferredFloodCandidate(p,p->radio_profile,true);
  }
};
int main() {
  { Mesh m; Packet a{0,1,1},b{1,1,1};
    m.dispatch(&a,m.receive(&a));assert(m.deliveries==1&&m.forwards==0);
    m.dispatch(&b,m.receive(&b));assert(m.deliveries==1&&m.forwards==1);
    m.dispatch(&b,m.receive(&b));assert(m.forwards==1);
    Packet crossed{0,1,1,true,true};assert(!mesh::flood_profiles::allowEgress(&crossed,m.policy));
  }
  { Mesh m; Packet a{0,2,2},b{1,2,2};
    m.dispatch(&a,m.receive(&a,true));m.dispatch(&b,m.receive(&b));assert(m.deliveries==1&&m.forwards==0);
  }
  { Mesh m; Packet a{0,4,3},b{1,4,3};
    m.dispatch(&a,m.receive(&a,false,false));m.dispatch(&b,m.receive(&b));assert(m.forwards==0);
  }
  { Mesh m; Packet a{0,1,4},b{1,1,4};m.receive(&a);m.filter=true;
    m.dispatch(&b,m.receive(&b));assert(m.forwards==0);m.filter=false;m.scoped=true;
    m.dispatch(&b,m.receive(&b));assert(m.forwards==0);m.scoped=false;m.hop_block=true;
    m.dispatch(&b,m.receive(&b));assert(m.forwards==0);m.hop_block=false;
    m.dispatch(&b,m.receive(&b));assert(m.forwards==1);
  }
  { Mesh m; Packet a{0,1,5},b{1,1,5};m.receive(&a);m.queue_ok=false;
    m.dispatch(&b,m.receive(&b));assert(m.dropped==1);m.queue_ok=true;
    m.dispatch(&b,m.receive(&b));assert(m.forwards==1);
  }
  { Mesh m; Packet a{0,1,8},b{1,1,8};m.receive(&a);m._prefs.disable_fwd=true;
    m.dispatch(&b,m.receive(&b));assert(m.forwards==0);m._prefs.disable_fwd=false;
    m.dispatch(&b,m.receive(&b));assert(m.forwards==1);
  }
  { Mesh m; Packet a{0,1,6},b{1,1,6};m.receive(&a);m.now+=120000;
    m.dispatch(&b,m.receive(&b));assert(m.forwards==0);
  }
  { Mesh m; Packet a{0,PAYLOAD_TYPE_OTA,7},b{1,PAYLOAD_TYPE_OTA,7};m.receive(&a);
    m.dispatch(&b,m.receive(&b));assert(m.forwards==0);
  }
  { mesh::flood_profiles::DeferredFloods cache; Policy p; Packet a{0,1,1},b{1,1,1};
    const uint32_t start=UINT32_MAX-100;cache.remember(&a,p,start);
    assert(cache.match(&b,p,start+119999,1));assert(!cache.match(&b,p,start+120000,1));
    for(uint8_t i=1;i<=9;i++){Packet x{0,1,i};cache.remember(&x,p,10);}
    assert(!cache.match(&b,p,11,1)); Packet last{1,1,9};assert(cache.match(&last,p,11,1));
  }
}
'''


class DeferredFloodForwardingTest(unittest.TestCase):
    def test_production_hook_and_policy(self):
        compiler = shutil.which('g++') or shutil.which('clang++')
        if not compiler: self.skipTest('C++ compiler unavailable')
        mesh = (ROOT / 'src/Mesh.cpp').read_text(encoding='utf-8')
        start = mesh.index('  if (pkt->isRouteFlood() && filterRecvFloodPacket(pkt))')
        end = mesh.index('  DispatcherAction action = ACTION_RELEASE;', start)
        hook = mesh[start:end]
        self.assertLess(hook.index('filterRecvFloodPacket'), hook.index('deferredFloodCandidate'))
        dispatcher = (ROOT / 'src/Dispatcher.cpp').read_text(encoding='utf-8')
        self.assertLess(dispatcher.index('if (!queueOutboundPacket(pkt, priority, _delay))'),
                        dispatcher.index('onForwardPacketQueued(pkt, ingress_profile)'))
        role = (ROOT / 'examples/simple_repeater/MyMesh.cpp').read_text(encoding='utf-8')
        gate_start = role.index('bool MyMesh::allowPacketForward(const mesh::Packet *packet) {')
        gate_start = role.index('\n', gate_start) + 1
        gate_end = role.index('#if !defined(PORTABLE_MQTT_OBSERVER)', gate_start)
        gate = role[gate_start:gate_end]
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder); source = path / 'test.cpp'; binary = path / 'test.exe'
            source.write_text(HARNESS.replace('@HOOK@', hook).replace('@FORWARD_GATE@', gate), encoding='utf-8')
            subprocess.run([compiler, '-std=c++17', '-I' + str(ROOT / 'src'), str(source), '-o', str(binary)], check=True)
            subprocess.run([str(binary)], check=True)


if __name__ == '__main__': unittest.main()
