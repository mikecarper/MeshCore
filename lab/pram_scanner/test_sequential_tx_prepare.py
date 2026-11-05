#!/usr/bin/env python3
"""Compile the real TX-profile handoff with failed pause/base operations."""
from pathlib import Path
import subprocess, tempfile
root=Path(__file__).resolve().parents[2]
s=(root/'src/helpers/radiolib/W12PramScannerLab.inc').read_text()
a=s.index('  mesh::RadioParamApplyResult prepareTransmitProfile(uint8_t p, bool reply) override {')
b=s.index('  mesh::RadioParamApplyResult tryRestoreCodingRate(',a)
method=s[a:b]
program=r'''
#include <cassert>
#include <cstdint>
#define MESH_W12_PRAM_SEQUENTIAL_LAB 1
#define MESH_W12_PRAM_SEQ_TX_LAB 1
namespace mesh {
 enum class RadioParamApplyResult {APPLIED,BUSY,FAILED};
 namespace radioDirection { enum class Mode{Both,Rx}; Mode current=Mode::Both; Mode mode(){return current;} }
}
using Result=mesh::RadioParamApplyResult;
struct Base {Result base=Result::APPLIED; virtual Result prepareTransmitProfile(uint8_t,bool){return base;} };
struct Profiles {bool allowed=true; bool canTransmit(uint8_t,bool){return allowed;} };
struct Test:Base {
 bool _lab_seq_requested=true, _lab_seq_tx_prepared=false, _lab_seq_tx_lease=false;
 bool _lab_scan_active=true, _lab_uncertain=false, _lab_resume_pending=false;
 uint8_t _lab_seq_tx_profile=0; unsigned _lab_tx_pauses=0, _lab_resume_at=0;
 Profiles _profiles; Result pause=Result::APPLIED;
 unsigned millis(){return 1000;}
 Result labPauseScanner(bool,uint8_t){if(pause==Result::APPLIED)_lab_scan_active=false;return pause;}
''' + method.replace('RadioLibWrapper::prepareTransmitProfile','Base::prepareTransmitProfile') + r'''
};
int main(){
 {Test t; assert(t.prepareTransmitProfile(1,false)==Result::APPLIED);
  assert(t._lab_seq_tx_lease && t._lab_seq_tx_prepared && t._lab_seq_tx_profile==1);
  assert(t.prepareTransmitProfile(1,false)==Result::APPLIED && t._lab_seq_tx_prepared);
  assert(t.prepareTransmitProfile(0,false)==Result::BUSY && !t._lab_seq_tx_prepared);}
 {Test t; t.pause=Result::BUSY; assert(t.prepareTransmitProfile(0,false)==Result::BUSY);
  assert(!t._lab_seq_tx_lease && !t._lab_seq_tx_prepared);}
 {Test t; t.base=Result::FAILED; assert(t.prepareTransmitProfile(0,false)==Result::FAILED);
  assert(t._lab_seq_tx_lease && !t._lab_seq_tx_prepared);}
 {Test t; t._lab_scan_active=false; assert(t.prepareTransmitProfile(0,false)==Result::BUSY);}
 {Test t; mesh::radioDirection::current=mesh::radioDirection::Mode::Rx;
  assert(t.prepareTransmitProfile(0,false)==Result::BUSY);}
}
'''
with tempfile.TemporaryDirectory() as d:
 cpp=Path(d)/'test.cpp';exe=Path(d)/'test';cpp.write_text(program)
 subprocess.run(['g++','-std=c++17','-Wall','-Wextra','-Werror',str(cpp),'-o',str(exe)],check=True)
 subprocess.run([str(exe)],check=True)
print('PASS sequential guarded TX prepare and one-use admission')
