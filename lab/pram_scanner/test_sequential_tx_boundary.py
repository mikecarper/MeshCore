#!/usr/bin/env python3
"""Exercise the real final TX boundary with mocked hardware and IRQ races."""
from pathlib import Path
import subprocess, tempfile
root=Path(__file__).resolve().parents[2]
s=(root/'src/helpers/radiolib/W12PramScannerLab.inc').read_text()
start=s.index('  bool labSequentialTxClear() {')
end=s.index('  void clearPreTransmitBlock()',start)
method=s[start:end]
program=r'''
#include <cassert>
#include <vector>
#include <helpers/radiolib/W12PramSequentialLab.h>
#define RADIOLIB_ERR_NONE 0
#define RADIOLIB_CHANNEL_FREE -15
#define RADIOLIB_LORA_DETECTED -702
#define RADIOLIB_ERR_UNKNOWN -1
namespace mesh { namespace radioDirection { enum class Mode {Both,Rx}; Mode current=Mode::Both; Mode mode(){return current;} } }
struct CustomLR2021 {
 int sf=7, standby_count=0, set_count=0, clear_count=0;
 bool fail_standby=false, fail_restore=false, irq_on_standby=false;
 std::vector<int> seen; bool* irq=nullptr;
 int getSpreadingFactor(){return sf;}
 int standby(){++standby_count; if (irq_on_standby) *irq=true; return fail_standby ? -1:0;}
 int setSpreadingFactor(int value){++set_count; seen.push_back(value); sf=value; return fail_restore && set_count==5 ? -1:0;}
 int clearIrqFlags(uint32_t){++clear_count; return 0;}
};
struct Board {void serviceWatchdog(){}};
struct Params {float freq,bw; uint8_t sf;};
struct Profiles { uint32_t generation[2]={11,12}; Params p[2]={{910.525f,62.5f,7},{911.15f,250.0f,7}}; bool enabled(){return true;} const Params& params(int i){return p[i];} };
struct Test {
 bool _lab_seq_requested=true, _lab_seq_tx_lease=true, _lab_seq_tx_prepared=true, _lab_scan_active=false, _lab_uncertain=false;
 bool _params_valid=true, _profile_refresh_required=false, _profile_repair_required=false, _cw_active=false;
 bool irq=false, receiving=false; int state=1;
 uint8_t _lab_seq_tx_profile=0, _active_profile=0; uint32_t _profile_generation=11;
 uint32_t _lab_seq_tx_busy=0,_lab_seq_tx_error=0,_lab_seq_tx_clear=0;
 float _cur_freq=910.525f, _cur_bw=62.5f; uint8_t _cur_sf=7, _physical_preamble=32;
 mesh::w12pram::Profile _lab_snap[2]={{910525000,62500,7,5,32,11},{911150000,250000,7,5,32,12}};
 mesh::w12pram::SequentialPlan _lab_seq_plan={}; Profiles _profiles;
 CustomLR2021 radio; CustomLR2021* _radio=&radio; Board board; Board* _board=&board;
 int scan_result=RADIOLIB_CHANNEL_FREE, scans=0; bool rx_fail=false;
 Test(){radio.irq=&irq; assert(mesh::w12pram::sequentialPlan(_lab_snap,0,5,8,_lab_seq_plan));}
 bool isChipBusy(){return false;} bool hasPendingRadioInterrupt(){return irq;}
 bool isPacketPendingOrReceiving(){return irq||receiving;}
 int profilePreamble(int){return 32;}
 void labMarkUncertain(){_lab_uncertain=true;_lab_seq_tx_lease=false;}
 int performChannelScanWithTimeout(unsigned long){++scans;return scan_result;}
 unsigned long cadScanTimeoutMillis(uint8_t,float){return 100;}
 void claimRadioInterrupt(){irq=false;}
 void startRecv(){state=rx_fail?0:1;} bool isInRecvMode(){return state==1;}
''' + method + r'''
};
int main(){
 {Test t; assert(t.labSequentialTxClear()); assert(t.scans==4 && t.radio.sf==7);
  assert((t.radio.seen==std::vector<int>{5,6,7,8,7})); assert(t._lab_seq_tx_clear==1);
  assert(!t.labSequentialTxClear() && t._lab_seq_tx_error==1);}
 {Test t; t.scan_result=RADIOLIB_LORA_DETECTED; assert(!t.labSequentialTxClear());
  assert(t._lab_seq_tx_busy==1 && !t._lab_uncertain && t.state==1);}
 {Test t; t.scan_result=-5; assert(!t.labSequentialTxClear()); assert(t._lab_seq_tx_error==1);}
 {Test t; ++t._profiles.generation[1]; assert(!t.labSequentialTxClear() && t.radio.standby_count==0);}
 {Test t; t._lab_snap[1].freq_hz=910600000; assert(!t.labSequentialTxClear());}
 {Test t; t.radio.irq_on_standby=true; assert(!t.labSequentialTxClear()); assert(t._lab_uncertain && t.irq && !t.radio.clear_count);}
 {Test t; t.radio.fail_standby=true; assert(!t.labSequentialTxClear() && t._lab_uncertain);}
 {Test t; t.radio.fail_restore=true; assert(!t.labSequentialTxClear() && t._lab_uncertain);}
 {Test t; t._profiles.p[0].sf=9; t._cur_sf=9; t.radio.sf=9; assert(t.labSequentialTxClear()); assert(t.scans==5 && t.radio.sf==9);}
 {Test t; t.receiving=true; assert(!t.labSequentialTxClear() && t.radio.standby_count==0);}
}
'''
with tempfile.TemporaryDirectory() as d:
 cpp=Path(d)/'test.cpp'; exe=Path(d)/'test'; cpp.write_text(program)
 subprocess.run(['g++','-std=c++17','-Wall','-Wextra','-Werror','-I',str(root/'src'),str(cpp),'-o',str(exe)],check=True)
 subprocess.run([str(exe)],check=True)
print('PASS sequential final TX check, SF sweep and injected failures')
