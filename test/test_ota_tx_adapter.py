"""Check physical OTA completion and independent radio-profile pacing."""
from pathlib import Path
import os
import shutil
import unittest

import test_ota_kiss_direct as direct_tests
from test_radio_receive_contract import method


ROOT = Path(__file__).resolve().parents[1]
FLAGS = ["-DENABLE_OTA", "-DMESH_ENABLE_MESHTEXT_CHAT=0", "-DMESH_ENABLE_ETHERNET_TUNNEL=0"]


class OtaTxAdapterTest(unittest.TestCase):
    compile_run = direct_tests.OtaKissDirectTest.compile_run

    @classmethod
    def setUpClass(cls):
        cls.compiler = os.environ.get("CXX") or shutil.which("g++") or shutil.which("clang++")
        if not cls.compiler:
            raise unittest.SkipTest("a host C++17 compiler is required")

    def test_direct_completion_preserves_bytes_and_affinity(self):
        code = direct_tests.dispatcher_harness().replace(
            "unsigned direct_calls=0, direct_rx=0;",
            "unsigned direct_calls=0, direct_rx=0, tx_ok=0, tx_failed=0; uint8_t tx_profile=255; uint32_t tx_generation=0;",
        ).replace(
            "uint32_t directOtaEpoch() const override",
            "void onDirectOtaRadioSendComplete(const uint8_t* b,size_t n,uint8_t p,uint32_t g,bool ok) override {\n"
            "    const uint8_t* msg=nullptr;uint16_t size=0;assert(ota::ota_direct_wire_decode(b,n,msg,size));\n"
            "    assert(hasForeignRadioFrame());assert(size==3 && msg[0]==ota::OTA_REQ && msg[1]==0xc0 && msg[2]==0xdb);\n"
            "    tx_profile=p;tx_generation=g;if(ok)++tx_ok;else ++tx_failed;\n"
            "  }\n  uint32_t directOtaEpoch() const override",
        ) + r'''
int main() {
  { Fixture f;directProfile(f);const auto gen=f.radio.p.generation[1];
    assert(enqueue(f));f.tick();assert(!f.d.tx_ok && !f.d.tx_failed);
    f.radio.complete=true;f.tick(50);
    assert(f.d.tx_ok==1 && !f.d.tx_failed && f.d.tx_profile==1 && f.d.tx_generation==gen);
    assert(f.d.done.empty() && !f.d.hasOutbound());f.tick(5000);assert(f.d.tx_ok==1);
  }
  { Fixture f;directProfile(f);const auto gen=f.radio.p.generation[1];
    assert(enqueue(f));++f.radio.p.generation[1];f.tick();
    assert(!f.radio.sends && !f.d.tx_ok && f.d.tx_failed==1 && f.d.tx_generation==gen);
  }
  { Fixture f;directProfile(f);assert(enqueue(f));f.tick();f.tick(1151);
    assert(!f.d.tx_ok && f.d.tx_failed==1 && f.d.done.empty());
  }
  { Fixture f;directProfile(f);f.radio.start_ok=false;assert(enqueue(f));f.tick();f.tick(10000);
    assert(!f.d.tx_ok && f.d.tx_failed==1 && f.d.done.empty());
  }
}
'''
        self.compile_run(code, FLAGS, dispatcher=True)

    def test_native_and_direct_pacing_are_per_profile_and_generation(self):
        code = direct_tests.dispatcher_harness().replace(
            "float getOtaPacketSpeedFactor() const override { return speed; }",
            "float getOtaPacketSpeedFactor() const override { return speed; }\n"
            "  float getOtaPacketSpeedFactor(uint8_t p,uint32_t) const override { return p==1 ? 0.1f:1.0f; }",
        ) + r'''
static void queueNative(Fixture& f,uint8_t profile) {
  auto* p=newPacket(f.d,profile==0 ? RADIO_TX_PRIMARY:RADIO_TX_SECONDARY);
  p->header=ROUTE_TYPE_FLOOD | (PAYLOAD_TYPE_OTA<<2);
  assert(f.d.sendPacket(p,0));
}
static void finish(Fixture& f) { f.radio.complete=true;f.tick(100); }
int main() {
  // A slow secondary cannot delay a native primary OTA frame, even when a
  // paced secondary is first in the queue. Both use normal CAD/budget logic.
  { Fixture f;directProfile(f);queueNative(f,1);f.tick();finish(f);
    queueNative(f,1);queueNative(f,0);f.tick();
    assert(f.radio.sends==2 && f.radio.active==0);
    finish(f);f.tick();assert(f.radio.sends==2);
    f.tick(800);assert(f.radio.sends==3 && f.radio.active==1);
  }
  // Direct completion creates the same profile-specific gap. Profile zero
  // remains available; changing generation retires profile one's old gap.
  { Fixture f;directProfile(f);assert(enqueue(f));f.tick();finish(f);
    assert(enqueue(f,0));f.tick();assert(f.radio.sends==2 && f.radio.active==0);finish(f);
    assert(enqueue(f));f.tick();assert(f.radio.sends==2);
    ++f.radio.p.generation[1];f.tick();assert(!f.d.hasOutbound());
    assert(enqueue(f));f.tick();assert(f.radio.sends==3 && f.radio.active==1);
  }
  // Native OTA completion also paces direct OTA on that physical profile.
  { Fixture f;directProfile(f);queueNative(f,1);f.tick();finish(f);
    assert(enqueue(f));f.tick(899);assert(f.radio.sends==1);
    f.tick(2);assert(f.radio.sends==2);
  }
}
'''
        self.compile_run(code, FLAGS, dispatcher=True)

    def test_mesh_completion_ignores_forwarding_and_retains_physical_route(self):
        source = (ROOT / "src/Mesh.cpp").read_text()
        code = r'''
#include <Packet.h>
#include <helpers/ota/OtaDirectWire.h>
#include <cassert>
namespace mesh { namespace ota {
struct OtaReplyRoute { uint8_t profile=255;uint32_t generation=0;bool direct=false; };
struct Manager {
  unsigned completed=0,failed=0;OtaReplyRoute route;uint32_t now=0;
  void noteResponseTransmitted(const uint8_t* p,uint16_t n,const OtaReplyRoute& r,uint32_t t) {
    assert(p && n==3 && p[0]==OTA_DATA);++completed;route=r;now=t;
  }
  void noteResponseFailed(const uint8_t* p,uint16_t n,const OtaReplyRoute& r,uint32_t t) {
    assert(p && n==3 && p[0]==OTA_DATA);++failed;route=r;now=t;
  }
  float adaptivePacketSpeed(uint8_t p,uint32_t g) const { assert(p==1 && g==42);return 0.5f; }
};
struct Context { Manager manager; } context;
bool exists=false;
Context* ota_context_if_active() { return exists ? &context:nullptr; }
} }
namespace mesh {
struct Clock { unsigned long getMillis() const { return 1234; } };
struct Tables { Packet* seen=nullptr;void markSeen(Packet* p) { seen=p; } };
static uint8_t otaTrafficPriority(const uint8_t*,uint16_t) { return 0; }
class Mesh {
public:
  Clock clock;Clock* _ms=&clock;unsigned lifecycle=0;float configured=1;
  Tables tables;Tables* _tables=&tables;uint8_t hops=3;
  Packet* queued=nullptr;uint32_t queue_delay=0;unsigned remembered=0;
  void watchForwardedAdvertEcho(Packet*) { ++lifecycle; }
  void armDirectRetryOnSendComplete(Packet*) { ++lifecycle; }
  void armFloodRetryOnSendComplete(Packet*) { ++lifecycle; }
  void clearPendingDirectRetryOnSendFail(Packet*) { ++lifecycle; }
  void clearPendingFloodRetryOnSendFail(Packet*) { ++lifecycle; }
  void rememberOtaForward(Packet*) { ++lifecycle;++remembered; }
  uint8_t getOtaHopLimit() const { return hops; }
  bool sendPacket(Packet* p,uint8_t,uint32_t delay) { queued=p;queue_delay=delay;return true; }
  bool sendOtaFlood(Packet*,uint32_t);
  float getOtaSpeedFactor() const { return configured; }
  float getOtaPacketSpeedFactor(uint8_t,uint32_t) const;
  void onSendComplete(Packet*);
  void onSendFail(Packet*);
  void onDirectOtaRadioSendComplete(const uint8_t*,size_t,uint8_t,uint32_t,bool);
};
@COMPLETE@
@FAILED@
@DIRECT@
@SPEED@
@SEND@
}
int main() {
  using namespace mesh;Mesh m;Packet p;
  p.header=ROUTE_TYPE_FLOOD | (PAYLOAD_TYPE_OTA<<2);p.path_len=0;p.payload_len=3;
  p.payload[0]=ota::OTA_DATA;p.radio_profile=1;p.radio_generation=42;
  m.onSendComplete(&p);m.onSendFail(&p);assert(!ota::exists && !ota::context.manager.completed);
  ota::exists=true;m.onSendComplete(&p);m.onSendFail(&p);
  auto& manager=ota::context.manager;
  assert(manager.completed==1 && manager.failed==1 && manager.now==1234);
  assert(manager.route.profile==1 && manager.route.generation==42 && !manager.route.direct);
  // Exercise sendOtaFlood itself: a zero-hop native OTA session uses DIRECT0,
  // while OtaReplyRoute::direct remains false (it is not the raw/KISS transport).
  m.hops=0;p.path_len=9;
  assert(m.sendOtaFlood(&p,17));
  assert(p.isRouteDirect() && p.getPathHashCount()==0 && m.queued==&p);
  assert(m.tables.seen==&p && m.queue_delay==17);
  const unsigned remembered=m.remembered;
  m.onSendComplete(&p);m.onSendFail(&p);
  assert(manager.completed==2 && manager.failed==2 && !manager.route.direct);
  assert(manager.route.profile==1 && manager.route.generation==42);
  assert(m.remembered==remembered); // Existing forwarding-cache flood guard stays intact.
  p.forwarded=true;m.onSendComplete(&p);m.onSendFail(&p);
  p.forwarded=false;p.path_len=1;m.onSendComplete(&p);m.onSendFail(&p);
  p.path_len=0;p.header=ROUTE_TYPE_FLOOD | (PAYLOAD_TYPE_GRP_TXT<<2);
  m.onSendComplete(&p);m.onSendFail(&p);m.onSendComplete(nullptr);m.onSendFail(nullptr);
  assert(manager.completed==2 && manager.failed==2);
  const uint8_t direct[]={0xf2,0,ota::OTA_DATA,0,0};
  m.onDirectOtaRadioSendComplete(direct,sizeof(direct),1,42,true);
  assert(manager.completed==3 && manager.route.direct && manager.route.profile==1);
  m.onDirectOtaRadioSendComplete(direct,sizeof(direct),1,42,false);
  assert(manager.failed==3 && manager.route.direct && manager.route.generation==42);
  m.onDirectOtaRadioSendComplete(direct,2,1,42,true);assert(manager.completed==3);
  assert(m.getOtaPacketSpeedFactor(1,42)==0.5f);m.configured=0.25f;
  assert(m.getOtaPacketSpeedFactor(1,42)==0.25f);
  ota::exists=false;m.onDirectOtaRadioSendComplete(direct,sizeof(direct),1,42,true);
  assert(manager.completed==3 && m.getOtaPacketSpeedFactor(1,42)==0.25f);
}
'''
        for token, signature in (
            ("@COMPLETE@", "void Mesh::onSendComplete("),
            ("@FAILED@", "void Mesh::onSendFail("),
            ("@DIRECT@", "void Mesh::onDirectOtaRadioSendComplete("),
            ("@SPEED@", "float Mesh::getOtaPacketSpeedFactor(uint8_t"),
            ("@SEND@", "bool Mesh::sendOtaFlood("),
        ):
            code = code.replace(token, method(source, signature))
        # Packet's constructor lives in Packet.cpp; no radio or scheduler is
        # needed to exercise the extracted production integration methods.
        code = code.replace("Packet p;", "Packet p{};").replace(
            "namespace mesh {\nstruct Clock", "namespace mesh {\nPacket::Packet() {}\nstruct Clock")
        self.compile_run(code, FLAGS)


if __name__ == "__main__":
    unittest.main()
