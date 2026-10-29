"""Execute the production OTA adapter through its final Packet route selection."""
from pathlib import Path
import os
import re
import shutil
import subprocess
import tempfile
import unittest

from test_radio_receive_contract import method


ROOT = Path(__file__).resolve().parents[1]

HARNESS = r'''
#include <cassert>
#include <cstring>
#include <initializer_list>
#include <vector>
#include <RadioProfiles.h>
#include <Packet.h>
#include <helpers/RadioDirectionPolicy.h>
#include <helpers/ota/OtaManager.h>
@CONSTANTS@
namespace mesh {
namespace ota {
struct Context {
  struct Manager {
    OtaReplyRoute route;
    OtaReplyRoute replyRoute() const { return route; }
  } manager;
};
static Context context;
Context* ota_context_if_active() { return &context; }
}
struct Manager {
  int free=40;
  std::vector<Packet*> queued;
  int getFreeCount() { return free; }
  int getOutboundTotal() { return int(queued.size()); }
  Packet* getOutboundByIdx(int i) { return queued[i]; }
};
using PacketManager=Manager;
struct Tables {
  int seen=0;
  uint8_t route=255;
  void markSeen(const Packet* p) { ++seen;route=p->getRouteType(); }
};
struct Radio {
  RadioProfiles config;
  const RadioProfiles* profiles() const { return &config; }
};
@HELPERS@
struct Mesh {
  Manager manager; Manager* _mgr=&manager;
  Tables tables; Tables* _tables=&tables;
  Radio radio; Radio* _radio=&radio;
  Packet packet;
  uint8_t hops=0, sent_mask=0, sent_priority=255;
  uint32_t sent_delay=0;
  int allocations=0, sends=0, remembered=0;
  bool temporary=true, admit=true;
  bool isAnyTempRadioActive() { return temporary; }
  uint8_t getOtaHopLimit() const { return hops; }
  Packet* obtainNewPacket() {
    ++allocations;
    if (!manager.free) return nullptr;
    --manager.free; packet=Packet();return &packet;
  }
  void releasePacket(Packet*) { ++manager.free; }
  void rememberOtaForward(const Packet*) { ++remembered; }
  bool sendPacket(Packet* p,uint8_t priority,uint32_t delay) {
    ++sends;sent_mask=getTransmitProfileMask(p);
    sent_priority=priority;sent_delay=delay;
    // Model Dispatcher ownership on both admission and rejection.
    releasePacket(p);return admit;
  }
  uint8_t getTransmitProfileMask(const Packet*) const;
  Packet* createOtaPacket(const uint8_t*,size_t);
  bool sendOtaFlood(Packet*,uint32_t delay=0);
  static bool otaSendAdapter(void*,const uint8_t*,uint16_t,bool);
};
@MASK@
@CREATE@
@SEND@
@ADAPTER@
}

static void profiles(mesh::Mesh& node,uint8_t mask) {
  auto& p=node.radio.config;
  p.primary_temporary=p.secondary_temporary=true;
  p.secondary.mode=mask==0 ? mesh::RadioProfileMode::Rx : mesh::RadioProfileMode::RxTx;
  p.cross=mask==3 ? mesh::RadioCrossMode::On : mesh::RadioCrossMode::Off;
  auto& route=mesh::ota::context.manager.route;
  route.profile=(mask==2 || mask==0) ? 1 : 0;
  route.generation=p.generation[route.profile];
}
int main() {
  // Every message keeps its existing primary/background priority and wire
  // payload. A stale nonempty path and transport route cannot survive reuse.
  for (uint8_t hops : {0,1,3,255}) {
    for (uint8_t type : {mesh::ota::OTA_ADV,mesh::ota::OTA_HAVE,
         mesh::ota::OTA_QUERY,mesh::ota::OTA_MANIFEST,mesh::ota::OTA_DATA,
         mesh::ota::OTA_PROOF,mesh::ota::OTA_REQ}) {
      mesh::Mesh node;profiles(node,1);node.hops=hops;
      auto* p=node.obtainNewPacket();
      p->header=(PAYLOAD_VER_2<<PH_VER_SHIFT)|(PAYLOAD_TYPE_OTA<<PH_TYPE_SHIFT)|ROUTE_TYPE_TRANSPORT_FLOOD;
      p->setPathHashSizeAndCount(3,4);p->payload[0]=type;p->payload[1]=0xF3;p->payload_len=2;
      p->tx_radio=mesh::RADIO_TX_PRIMARY;p->radio_bound=true;
      p->radio_origin_generation=123;p->radio_generation=456;
      assert(node.sendOtaFlood(p,789));
      assert(p->getRouteType()==(hops ? ROUTE_TYPE_FLOOD : ROUTE_TYPE_DIRECT));
      assert(p->getPathHashCount()==0 && p->getPathHashSize()==1);
      assert(p->getPayloadType()==PAYLOAD_TYPE_OTA && p->getPayloadVer()==PAYLOAD_VER_2);
      assert(p->payload_len==2 && p->payload[0]==type && p->payload[1]==0xF3);
      assert(p->tx_radio==mesh::RADIO_TX_PRIMARY && p->radio_bound);
      assert(p->radio_origin_generation==123 && p->radio_generation==456);
      assert(node.sent_priority==(mesh::ota::ota_is_transfer_message(type) ? OTA_TRANSFER_TX_PRIORITY : OTA_TX_PRIORITY));
      assert(node.sent_delay==789 && node.sent_mask==1);
      assert(node.remembered==1 && node.tables.seen==1 && node.tables.route==p->getRouteType());
    }
  }
  // Execute the production admission and radio-mask code before the production
  // route function: zero-hop must not weaken queue credit or the RX reserve.
  for (uint8_t hops : {0,3}) {
    for (uint8_t type : {mesh::ota::OTA_MANIFEST,mesh::ota::OTA_DATA,
         mesh::ota::OTA_PROOF,mesh::ota::OTA_LEAVES}) {
      for (uint8_t mask : {0,1,2,3}) {
        for (int queued=0;queued<=OTA_EGRESS_QUEUE_CREDIT+1;++queued) {
          for (int free=0;free<=OTA_EGRESS_MIN_FREE+3;++free) {
            mesh::Mesh node;profiles(node,mask);node.hops=hops;node.manager.free=free;
            mesh::Packet prior;prior.header=PAYLOAD_TYPE_OTA<<PH_TYPE_SHIFT;
            prior.payload[0]=type;prior.payload_len=1;
            for(int i=0;i<queued;++i)node.manager.queued.push_back(&prior);
            const int copies=mask==3 ? 2 : mask ? 1 : 0;
            const bool allowed=copies && queued+copies<=OTA_EGRESS_QUEUE_CREDIT
                && free>=OTA_EGRESS_MIN_FREE+copies;
            assert(node.otaSendAdapter(&node,&type,1,false)==allowed);
            assert(node.manager.free==free);
            assert(node.sends==int(allowed) && node.tables.seen==int(allowed));
            if (queued>=OTA_EGRESS_QUEUE_CREDIT || free<=OTA_EGRESS_MIN_FREE)
              assert(node.allocations==0);
            if (allowed) {
              assert(node.sent_mask==mask && !node.packet.radio_reply && !node.packet.radio_local);
              assert(node.packet.getRouteType()==(hops ? ROUTE_TYPE_FLOOD : ROUTE_TYPE_DIRECT));
              const auto route=mesh::ota::context.manager.route;
              assert(node.packet.radio_profile==route.profile && node.packet.radio_origin==route.profile);
              assert(node.packet.radio_generation==route.generation && node.packet.radio_origin_generation==route.generation);
              assert(!node.packet.radio_bound && node.sent_priority==OTA_TRANSFER_TX_PRIORITY);
            }
          }
        }
      }
    }
  }
  mesh::Mesh rejected;profiles(rejected,1);rejected.admit=false;
  const uint8_t data=mesh::ota::OTA_DATA;
  assert(!rejected.otaSendAdapter(&rejected,&data,1,true));
  assert(rejected.sends==1 && rejected.manager.free==40);
  rejected.temporary=false;const int count=rejected.allocations;
  assert(!rejected.otaSendAdapter(&rejected,&data,1,true) && rejected.allocations==count);
}
'''


class OtaZeroHopRouteTest(unittest.TestCase):
    def test_production_adapter_direct_route_and_backpressure(self):
        compiler = os.environ.get("CXX") or shutil.which("g++") or shutil.which("clang++")
        if not compiler:
            self.skipTest("a host C++17 compiler is required")
        source = (ROOT / "src/Mesh.cpp").read_text(encoding="utf-8")
        header = (ROOT / "src/Mesh.h").read_text(encoding="utf-8")
        dispatcher = (ROOT / "src/Dispatcher.cpp").read_text(encoding="utf-8")
        constants = "\n".join(re.search(r"^#define " + name + r" \d+$", header, re.M)[0]
                              for name in ("OTA_EGRESS_QUEUE_CREDIT", "OTA_EGRESS_MIN_FREE",
                                           "OTA_TX_PRIORITY", "OTA_TRANSFER_TX_PRIORITY"))
        helpers = "\n".join(method(source, signature) for signature in (
            "static bool isPrimaryOtaTraffic(", "static uint8_t otaTrafficPriority(",
            "static bool isPacedOtaResponse(", "static int queuedPacedOtaResponses("))
        code = HARNESS.replace("@CONSTANTS@", constants).replace("@HELPERS@", helpers)
        code = code.replace("@MASK@", method(dispatcher, "uint8_t Dispatcher::getTransmitProfileMask(")
                            .replace("Dispatcher::", "Mesh::"))
        code = code.replace("@CREATE@", method(source, "Packet* Mesh::createOtaPacket("))
        code = code.replace("@ADAPTER@", method(source, "bool Mesh::otaSendAdapter("))
        send = method(source, "bool Mesh::sendOtaFlood(")
        with tempfile.TemporaryDirectory(prefix="ota-zero-hop-") as directory:
            cpp, binary = Path(directory) / "test.cpp", Path(directory) / "test.exe"
            command = [compiler, "-std=c++17", "-Wall", "-Wextra", "-I", str(ROOT / "src"),
                       "-I", str(ROOT / "test/mocks"), str(cpp), str(ROOT / "src/Packet.cpp"),
                       "-o", str(binary)]
            variants = [("production", send, True),
                        ("zero-hop-regression", send.replace(
                            "getOtaHopLimit() == 0 ? ROUTE_TYPE_DIRECT : ROUTE_TYPE_FLOOD",
                            "ROUTE_TYPE_FLOOD"), False)]
            for label, candidate, expected in variants:
                with self.subTest(variant=label):
                    cpp.write_text(code.replace("@SEND@", candidate), encoding="utf-8")
                    compiled = subprocess.run(command, capture_output=True, text=True, timeout=60)
                    self.assertEqual(compiled.returncode, 0, compiled.stdout + compiled.stderr)
                    ran = subprocess.run([str(binary)], capture_output=True, text=True, timeout=20)
                    if expected:
                        self.assertEqual(ran.returncode, 0, ran.stdout + ran.stderr)
                    else:
                        self.assertNotEqual(ran.returncode, 0, "the former FLOOD0 behavior must fail")
                        self.assertIn("p->getRouteType()", ran.stderr)


if __name__ == "__main__":
    unittest.main()
