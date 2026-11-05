#!/usr/bin/env python3
"""Verify exact startSendRaw classifies only known pre-TX blocks as retryable."""
from pathlib import Path
import subprocess,tempfile,sys
root=Path(__file__).resolve().parents[2];sys.path.insert(0,str(root/'test'))
from cpp_source import body
s=(root/'src/helpers/radiolib/CustomLR2021Wrapper.h').read_text(encoding='utf8')
method=body(s,'bool startSendRaw(const uint8_t* bytes, int len)')
method=method.replace('RadioLibWrapper::startSendRaw','Base::startSendRaw').replace(' override {',' {')
program=r'''
#include <cassert>
#include <cstdint>
#define MESH_W12_PRAM_SEQUENTIAL_LAB 1
#define MESH_W12_PRAM_SEQ_TX_LAB 1
#define MESH_W12_PRAM_EXPERIMENT 1
namespace mesh {enum class PreTransmitBlock:uint8_t {None,Busy,Error};}
struct Base {unsigned calls=0;bool ok=false;bool startSendRaw(const uint8_t*,int){++calls;return ok;}};
struct Test:Base {
 bool _lab_seq_requested=true,_lab_scan_active=false,_lab_uncertain=false;
 bool allow=false,fail_busy=false; uint32_t _lab_seq_tx_busy=0;
 mesh::PreTransmitBlock _lab_seq_tx_block=mesh::PreTransmitBlock::None;
 bool labSequentialTxClear(){if(!allow && fail_busy) ++_lab_seq_tx_busy;return allow;}
 void invalidateScanTuple(){}
 METHOD
};
int main(){uint8_t b=0;
 {Test t;t.fail_busy=true;assert(!t.startSendRaw(&b,1));assert(t.calls==0 && t._lab_seq_tx_block==mesh::PreTransmitBlock::Busy);}
 {Test t;assert(!t.startSendRaw(&b,1));assert(t.calls==0 && t._lab_seq_tx_block==mesh::PreTransmitBlock::Error);}
 {Test t;t.allow=true;assert(!t.startSendRaw(&b,1));assert(t.calls==1 && t._lab_seq_tx_block==mesh::PreTransmitBlock::None);}
 {Test t;t.allow=true;t.ok=true;assert(t.startSendRaw(&b,1));assert(t.calls==1 && t._lab_seq_tx_block==mesh::PreTransmitBlock::None);}
}
'''.replace('METHOD',method)
with tempfile.TemporaryDirectory() as d:
 cpp=Path(d)/'test.cpp';exe=Path(d)/'test';cpp.write_text(program)
 subprocess.run(['g++','-std=c++17','-Wall','-Wextra','-Werror',str(cpp),'-o',str(exe)],check=True)
 subprocess.run([str(exe)],check=True)
print('PASS typed guard blocks versus ambiguous transmit-start failure')
