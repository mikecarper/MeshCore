#!/usr/bin/env python3
"""Exercise actual foreign-send failure dispatch with fake radio and time."""
from pathlib import Path
import subprocess,tempfile
root=Path(__file__).resolve().parents[2]
s=(root/'src/Dispatcher.cpp').read_text(encoding='utf8')
a=s.index('  foreign_radio_start = _ms->getMillis();',s.index('bool Dispatcher::checkForeignRadioSend()'))
b=s.index('  foreign_radio_sending = true;',a)
fragment=s[a:b]
program=r'''
#include <cassert>
#include <cstdint>
#define MESH_W12_PRAM_SEQ_TX_LAB 1
enum class PreTransmitBlock:uint8_t {None,Busy,Error};
struct Clock {uint32_t now=100; uint32_t getMillis(){return now;}};
struct Radio {
 PreTransmitBlock next=PreTransmitBlock::None, stored=PreTransmitBlock::None;
 bool ok=false, may_have_transmitted=false; unsigned calls=0;
 void clearPreTransmitBlock(){stored=PreTransmitBlock::None;}
 PreTransmitBlock takePreTransmitBlock(){auto v=stored;stored=PreTransmitBlock::None;return v;}
 bool startSendRaw(uint8_t*,unsigned){++calls;stored=next;return ok;}
};
struct Test {
 Clock clk,*_ms=&clk; Radio radio,*_radio=&radio; uint8_t foreign_radio_bytes[2]={};
 unsigned foreign_radio_length=2; uint32_t foreign_radio_start=0,foreign_pre_tx_deadline=0,foreign_radio_retry_at=0;
 unsigned failed=0; uint32_t getCADFailRetryDelay(){return 200;}
 void finishForeignRadioFrame(bool,bool){++failed;}
 bool run(){
''' + fragment + r'''
 return true; }
};
int main(){
 {Test t; t.radio.next=PreTransmitBlock::Busy; assert(!t.run());
  assert(t.failed==0 && t.foreign_pre_tx_deadline==30100 && t.foreign_radio_retry_at==300);
  t.clk.now=30099; assert(!t.run() && t.failed==0);
  t.clk.now=30100; assert(!t.run() && t.failed==1);}
 {Test t; t.radio.next=PreTransmitBlock::Error; assert(!t.run() && t.failed==0);}
 {Test t; t.radio.next=PreTransmitBlock::None; assert(!t.run() && t.failed==1);}
 {Test t; t.radio.ok=true; assert(t.run() && !t.failed);}
}
'''
with tempfile.TemporaryDirectory() as d:
 cpp=Path(d)/'test.cpp';exe=Path(d)/'test';cpp.write_text(program)
 subprocess.run(['g++','-std=c++17','-Wall','-Wextra','-Werror',str(cpp),'-o',str(exe)],check=True)
 subprocess.run([str(exe)],check=True)
print('PASS foreign pre-transmit retry, bounded timeout and ambiguous-failure distinction')
