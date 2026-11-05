#!/usr/bin/env python3
"""Actual direct-OTA foreign-slot retry, timeout, epoch, and uncertain-start behavior."""
from pathlib import Path
import os,subprocess,sys,tempfile
root=Path(__file__).resolve().parents[1];sys.path.insert(0,str(root/'test'))
from test_ota_kiss_direct import dispatcher_harness
s=dispatcher_harness()
s=s.replace('  unsigned sends=0, finishes=0, recovery=0, processed=0;', '''
  unsigned sends=0, finishes=0, recovery=0, processed=0;
  PreTransmitBlock next_block=PreTransmitBlock::None, block=PreTransmitBlock::None;
  void clearPreTransmitBlock() override {block=PreTransmitBlock::None;}
  PreTransmitBlock takePreTransmitBlock() override {auto v=block;block=PreTransmitBlock::None;return v;}
''')
s=s.replace('''    ++sends;sent.assign(bytes,bytes+len);sending=start_ok;complete=false;return start_ok;''','''
    ++sends;sent.assign(bytes,bytes+len);
    if (next_block!=PreTransmitBlock::None) {block=next_block;return false;}
    sending=start_ok;complete=false;return start_ok;''')
s=s.replace('''  unsigned direct_calls=0, direct_rx=0;''','''  std::vector<bool> direct_done;
  void onDirectOtaRadioSendComplete(const uint8_t*,size_t,uint8_t,uint32_t,bool ok) override {direct_done.push_back(ok);}
  unsigned direct_calls=0, direct_rx=0;''')
s+=r'''
int main(){
 {Fixture f;directProfile(f);f.radio.next_block=PreTransmitBlock::Busy;
  assert(enqueue(f));f.tick();assert(f.radio.sends==1 && f.d.hasForeignRadioFrame() && f.d.direct_done.empty());
  f.radio.next_block=PreTransmitBlock::None;f.tick(201);assert(f.radio.sends==2);
  f.radio.complete=true;f.tick();assert(f.d.direct_done.size()==1 && f.d.direct_done[0]);}
 {Fixture f;directProfile(f);f.radio.next_block=PreTransmitBlock::Error;
  assert(enqueue(f));f.tick();assert(f.d.hasForeignRadioFrame());f.tick(30000);
  assert(f.radio.sends==1 && f.d.direct_done.size()==1 && !f.d.direct_done[0]);}
 {Fixture f;directProfile(f);f.radio.start_ok=false;assert(enqueue(f));f.tick();f.tick(30000);
  assert(f.radio.sends==1 && f.d.direct_done.size()==1 && !f.d.direct_done[0]);}
 {Fixture f;directProfile(f);f.radio.next_block=PreTransmitBlock::Busy;assert(enqueue(f));f.tick();
  ++f.d.epoch;f.tick(201);assert(f.radio.sends==1 && f.d.direct_done.size()==1 && !f.d.direct_done[0]);}
}
'''
with tempfile.TemporaryDirectory() as d:
 cpp=Path(d)/'test.cpp';exe=Path(d)/'test';cpp.write_text(s,encoding='utf8')
 flags=['-std=c++17','-DENABLE_OTA=1','-DMESH_ENABLE_OTA_KISS_DIRECT=1','-DMESH_ENABLE_FOREIGN_RADIO=1','-DMESH_W12_PRAM_SEQ_TX_LAB=1']
 if os.name!='nt':flags+=['-fsanitize=address,undefined','-fno-omit-frame-pointer','-fno-pie','-no-pie']
 cmd=['g++',*flags,'-I',str(root/'test/mocks'),'-I',str(root/'src'),*[str(root/p) for p in ['src/Dispatcher.cpp','src/Packet.cpp','src/helpers/StaticPoolPacketManager.cpp']],str(cpp),'-o',str(exe)]
 result=subprocess.run(cmd,capture_output=True,text=True)
 if result.returncode: raise RuntimeError(result.stderr[-12000:])
 subprocess.run([str(exe)],check=True,env=dict(os.environ,ASAN_OPTIONS='detect_leaks=0'))
print('PASS actual direct OTA pre-TX retry, deadline, epoch and uncertain failure')
