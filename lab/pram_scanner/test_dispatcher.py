from pathlib import Path
import os, subprocess, sys, tempfile
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'test'))
from test_radio_direction_dispatcher import SOURCE
src=SOURCE.replace('unsigned rx=0,tx=0,calibrations=0,recoveries=0,loops=0;', '''
  unsigned rx=0,tx=0,calibrations=0,recoveries=0,loops=0;
  int remaining=0; mesh::W12PramRxSnapshot latest;
''')
src=src.replace('int recvRaw(uint8_t*,int) override { ++rx; return 0; }', '''
  int recvRaw(uint8_t* bytes,int) override {
    ++rx; if (!remaining) return 0;
    uint8_t profile=remaining==2?0:1;
    bytes[0]=ROUTE_TYPE_FLOOD|(PAYLOAD_TYPE_TXT_MSG<<PH_TYPE_SHIFT);
    bytes[1]=0; bytes[2]=static_cast<uint8_t>(remaining);
    latest={profile ? 911150000u : 910525000u,profile ? 911151250u : 910526250u,
            profile ? 250000u : 62500u,p.generation[profile],32,profile,7,5,true};
    --remaining; return 3;
  }
  uint8_t receiveProfile() const override { return latest.profile; }
  bool getW12PramRxSnapshot(mesh::W12PramRxSnapshot& out) const override { out=latest; return out.valid; }
''')
src=src.replace('unsigned inbound=0;', '''
  unsigned inbound=0; std::vector<mesh::W12PramRxSnapshot> snapshots; bool change_profile=false;
  void logRxRaw(float,float,const uint8_t*,int) override { if(change_profile) static_cast<Radio*>(_radio)->latest.profile ^= 1; }
  int calcRxDelayForPacket(const mesh::Packet*,float,uint32_t) override {return 2000;}
''')
src=src.replace('++inbound; return ACTION_RETRANSMIT_DELAYED(0,0);','++inbound; snapshots.push_back(packet->w12_rx); return ACTION_RELEASE;')
src=src.replace('onRecvPacket(mesh::Packet*)','onRecvPacket(mesh::Packet* packet)')
src=src[:src.index('int main() {')]+'''
int main() {
  Radio r; Clock c; StaticPoolPacketManager pool(5); Dispatcher d(r,c,pool);
  r.p.secondary.mode=mesh::RadioProfileMode::RxTx;
  r.p.generation[0]=11;r.p.generation[1]=33;
  d.begin();r.remaining=2;
  for(int i=0;i<2;++i){c.now+=10;d.loop();}
  assert(d.inbound==0 && r.remaining==0);
  c.now+=2500;for(int i=0;i<10;++i){c.now+=10;d.loop();}
  assert(d.inbound==2 && d.snapshots.size()==2);
  assert(d.snapshots[0].valid && d.snapshots[0].profile==0 && d.snapshots[0].generation==11);
  assert(d.snapshots[0].logical_freq_hz==910525000u && d.snapshots[0].bw_hz==62500u);
  assert(d.snapshots[1].valid && d.snapshots[1].profile==1 && d.snapshots[1].generation==33);
  assert(d.snapshots[1].actual_freq_hz==911151250u && d.snapshots[1].bw_hz==250000u);
  d.change_profile=true;r.remaining=1;c.now+=10;d.loop();c.now+=2500;for(int i=0;i<5;++i){c.now+=10;d.loop();}
  assert(d.inbound==2 && d.snapshots.size()==2); // discard instead of mislabel
}
'''
with tempfile.TemporaryDirectory() as d:
    p=Path(d);(p/'main.cpp').write_text(src);(p/'Mesh.h').write_text('#pragma once\n#include <Dispatcher.h>\n')
    cmd=[os.environ.get('CXX','g++'),'-std=c++17','-O1','-DMESH_W12_PRAM_EXPERIMENT=1','-DMESH_ENABLE_RADIO_DIRECTION=1','-DMESH_ENABLE_BRIDGE_INGRESS=1','-I',str(p),'-I',str(ROOT/'test/mocks'),'-I',str(ROOT/'src'),str(p/'main.cpp'),str(ROOT/'src/Dispatcher.cpp'),str(ROOT/'src/Packet.cpp'),str(ROOT/'src/helpers/StaticPoolPacketManager.cpp'),'-o',str(p/'test')]
    x=subprocess.run(cmd,capture_output=True,text=True,timeout=60)
    if x.returncode: raise SystemExit(x.stdout+x.stderr)
    subprocess.run([str(p/'test')],check=True,timeout=20)
print('PASS actual Dispatcher preserves each profile snapshot through delayed packet queue')
