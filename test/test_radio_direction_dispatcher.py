"""Exercise the actual dispatcher with split receivers and a bridge-fed transmitter."""
from pathlib import Path
import os
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]

SOURCE = r'''
#include <Dispatcher.h>
#include <helpers/StaticPoolPacketManager.h>
#include <helpers/AbstractBridge.h>
#include <cassert>
#include <vector>
namespace mesh { namespace radioDirection {
Mode current = Mode::Both;
Mode mode() { return current; }
}}
namespace mesh { namespace bridge {
IngressRoute current{IngressProfile::Radio, true};
IngressProfile ingressProfile() { return current.profile; }
IngressRoute ingressRoute(const IngressSource*) { return current; }
}}
struct Clock : mesh::MillisecondClock {
  uint32_t now=100;
  unsigned long getMillis() override { return now; }
};
struct Radio : mesh::Radio {
  mesh::RadioProfiles p;
  unsigned rx=0,tx=0,calibrations=0,recoveries=0,loops=0;
  uint8_t prepared=0;
  bool cad=false;
  unsigned busy_mask=0;
  std::vector<unsigned> sent;
  mesh::RadioProfiles* profiles() override { return &p; }
  const mesh::RadioProfiles* profiles() const override { return &p; }
  int recvRaw(uint8_t*,int) override { ++rx; return 0; }
  uint32_t getEstAirtimeFor(int) override { return 10; }
  float packetScore(float,int) override { return 0; }
  bool startSendRaw(const uint8_t*,int) override { ++tx; sent.push_back(prepared); return true; }
  bool isSendComplete() override { return true; }
  void onSendFinished() override {}
  bool isInRecvMode() const override { return mesh::radioDirection::radioReceiveAllowed(); }
  void loop() override { ++loops; }
  void setCADEnabled(bool value) override { cad=value; }
  bool isReceiving() override { return busy_mask & (1U << prepared); }
  void triggerNoiseFloorCalibrate(int) override { ++calibrations; }
  bool recoverRadio(bool) override { ++recoveries; return true; }
  mesh::RadioParamApplyResult prepareTransmitProfile(uint8_t value) override {
    prepared=value; return mesh::RadioParamApplyResult::APPLIED;
  }
};
struct Dispatcher : mesh::Dispatcher {
  unsigned inbound=0;
  Dispatcher(Radio&r,Clock&c,StaticPoolPacketManager&m):mesh::Dispatcher(r,c,m){}
  mesh::DispatcherAction onRecvPacket(mesh::Packet*) override {
    ++inbound; return ACTION_RETRANSMIT_DELAYED(0,0);
  }
  using mesh::Dispatcher::getTransmitProfileMask;
  using mesh::Dispatcher::getNextQueueWakeDelay;
  bool getCADEnabled() const override { return true; }
};
struct Bridge : AbstractBridge {
  StaticPoolPacketManager& m;
  explicit Bridge(StaticPoolPacketManager& pool):m(pool){}
  bool receive(mesh::Packet* p) { if(!preparePacketIngress(p))return false; m.queueInbound(p,0);return true; }
  void begin() override {} void end() override {} void loop() override {}
  bool isRunning() const override { return true; }
  void sendPacket(mesh::Packet*) override {} void onPacketReceived(mesh::Packet*) override {}
};
int main() {
  using namespace mesh::radioDirection;
  Radio r; Clock c; StaticPoolPacketManager pool(12); Dispatcher d(r,c,pool);
  r.p.secondary.mode=mesh::RadioProfileMode::RxTx;
  r.p.cross=mesh::RadioCrossMode::On;
  r.p.generation[0]=11;r.p.generation[1]=33;
  current=Mode::Tx; d.begin(); Bridge b(pool);b.setIngressProfiles(&r.p);
  for(unsigned profile:{0U,1U}) {
    mesh::bridge::current={profile?mesh::bridge::IngressProfile::Radio2:mesh::bridge::IngressProfile::Radio,true};
    auto* p=d.obtainNewPacket();assert(p);p->header=ROUTE_TYPE_FLOOD|(PAYLOAD_TYPE_TXT_MSG<<PH_TYPE_SHIFT);
    p->payload_len=1;p->payload[0]=profile;
    assert(b.receive(p));
  }
  for(unsigned i=0;i<100;++i){c.now+=10;d.loop();}
  assert(d.inbound==2&&r.tx==2&&r.sent[0]==0&&r.sent[1]==1);
  assert(r.rx==0&&r.calibrations==0&&r.recoveries==0&&r.loops>0&&r.cad);
  c.now+=24UL*60*60*1000; d.loop();assert(r.recoveries==0&&r.rx==0);
  uint32_t delay=0;assert(!d.getNextNoiseFloorWakeDelay(delay));
  assert(!d.getNextQueueWakeDelay(delay));
  // A busy MediaMesh channel must neither time out into a forced send nor
  // starve a ready USA packet. Then send the retained frame when CAD clears.
  const auto initial=r.tx;
  r.busy_mask=2;
  for(unsigned profile:{1U,0U}) {
    mesh::bridge::current={profile?mesh::bridge::IngressProfile::Radio2:mesh::bridge::IngressProfile::Radio,true};
    auto* p=d.obtainNewPacket();assert(p);p->header=ROUTE_TYPE_FLOOD;
    p->payload_len=1;assert(b.receive(p));
  }
  for(unsigned i=0;i<1000;++i){c.now+=50;d.loop();}
  assert(r.tx==initial+1&&r.sent.back()==0);
  r.busy_mask=0;
  for(unsigned i=0;i<100;++i){c.now+=50;d.loop();}
  assert(r.tx==initial+2&&r.sent.back()==1);
  // RF already delayed before a mode change cannot be mistaken for bridge
  // input after RX is disabled.
  const auto received=d.inbound;
  auto* stale=d.obtainNewPacket();assert(stale);
  stale->header=ROUTE_TYPE_FLOOD;stale->forwarded=false;
  stale->radio_profile=0;stale->radio_generation=r.p.generation[0];
  pool.queueInbound(stale,0);c.now+=10;d.loop();assert(d.inbound==received);
  current=Mode::Rx;
  auto* blocked=d.obtainNewPacket();assert(blocked);blocked->header=ROUTE_TYPE_FLOOD;
  assert(d.getTransmitProfileMask(blocked)==0);
  assert(!d.sendPacket(blocked,0,0));
  const auto before=r.tx; c.now+=10;d.loop();assert(r.tx==before&&r.rx>0);
  current=Mode::Off;const auto reads=r.rx;
  for(unsigned i=0;i<10;++i){c.now+=10000;d.loop();}
  assert(r.tx==before&&r.rx==reads&&r.recoveries==0);
  current=Mode::Both;c.now+=10;d.loop();assert(r.rx>reads);
}
'''


class RadioDirectionDispatcherTests(unittest.TestCase):
    def test_split_bridge_forwarding_direction_and_watchdog_lifecycle(self):
        for soft in (False, True):
            with self.subTest(soft=soft), tempfile.TemporaryDirectory() as temporary:
                work = Path(temporary)
                source = work / 'main.cpp'
                source.write_text(SOURCE)
                (work/'Mesh.h').write_text('#pragma once\n#include <Dispatcher.h>\n')
                executable = work / 'test'
                flags = ['-DMESH_ENABLE_RADIO_DIRECTION=1', '-DMESH_ENABLE_BRIDGE_INGRESS=1']
                if soft:
                    flags += ['-DRADIO_LIVENESS_SOFT_ONLY=1']
                built = subprocess.run([os.environ.get('CXX', 'c++'), '-std=c++17', '-O1',
                    '-fsanitize=address,undefined', '-fno-sanitize-recover=all', '-fno-pie', '-no-pie',
                    *flags, '-I', str(work), '-I', str(ROOT/'test/mocks'), '-I', str(ROOT/'src'), str(source),
                    str(ROOT/'src/Dispatcher.cpp'), str(ROOT/'src/Packet.cpp'),
                    str(ROOT/'src/helpers/StaticPoolPacketManager.cpp'), '-o', str(executable)],
                    capture_output=True, text=True, timeout=60)
                self.assertEqual(built.returncode, 0, built.stdout+built.stderr)
                tested = subprocess.run([str(executable)], capture_output=True, text=True,
                    timeout=20, env=dict(os.environ, ASAN_OPTIONS='detect_leaks=0'))
                self.assertEqual(tested.returncode, 0, tested.stdout+tested.stderr)


if __name__ == '__main__':
    unittest.main()
