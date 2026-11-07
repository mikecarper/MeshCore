#!/usr/bin/env python3
"""Compile the production receive-airtime hook with controlled packet metadata."""
from pathlib import Path
import subprocess, tempfile
root=Path(__file__).resolve().parents[2]
s=(root/'src/helpers/radiolib/W12PramScannerLab.inc').read_text()
start=s.index('  uint32_t getReceivedAirtimeFor(')
end=s.index('\n#endif',start)
hook=s[start:end].replace(' override {',' {')
start=s.index('    uint8_t received_cr=0;')
end=s.index('\n#endif',start)
metadata=s[start:end]
assert s.index('received_params_valid=true') < s.index('r->readData(bytes,len)')
assert s.index('r->readData(bytes,len)') < s.index('_lab_seq_rx_valid=received_params_valid;')
program='''
#include <cassert>
#include <helpers/radiolib/W12PramSequentialLab.h>
constexpr int RADIOLIB_ERR_NONE=0;
struct Chip {
 uint8_t raw_cr=0; int result=0;
 int getLoRaPacketStatus(uint8_t* cr, bool* crc) { *cr=raw_cr; *crc=true; return result; }
};
struct Test {
 bool _lab_seq_requested=true, _lab_scan_active=true, _lab_seq_rx_valid=true, _lab_seq_rx_crc=true;
 int _lab_last_profile=0; unsigned char _lab_seq_rx_cr=5; unsigned int _lab_seq_rx_unknown=0;
 mesh::w12pram::Profile _lab_last={904525000,62500,5,5,32,11};
''' + hook + '''
 uint8_t readHeader(uint8_t raw, int result=0) {
   Chip chip; chip.raw_cr=raw; chip.result=result; auto* r=&chip;
''' + metadata + '''
   return received_params_valid ? received_cr : 0;
 }
};
int main(){ Test t;
 assert(t.readHeader(0)==4);
 assert(t.readHeader(1)==5);
 assert(t.readHeader(4)==8);
 assert(t.readHeader(5)==0 && t.readHeader(255)==0 && t.readHeader(0,-1)==0);
 assert(t.getReceivedAirtimeFor(0,50,123)==78);
 assert(t.getReceivedAirtimeFor(1,50,123)==123);
 t._lab_last.sf=8; t._lab_seq_rx_cr=6;
 assert(t.getReceivedAirtimeFor(0,50,123)==501);
 t._lab_last.sf=12; t._lab_last.bw_hz=500000; t._lab_seq_rx_cr=4;
 assert(t.getReceivedAirtimeFor(0,255,123)==1772);
 t._lab_seq_rx_valid=false; assert(t.getReceivedAirtimeFor(0,50,123)==0);
 assert(t._lab_seq_rx_unknown==1);
 t._lab_scan_active=false; assert(t.getReceivedAirtimeFor(0,50,123)==123);
}
'''
with tempfile.TemporaryDirectory() as d:
 cpp=Path(d)/'test.cpp'; exe=Path(d)/'test'; cpp.write_text(program)
 subprocess.run(['g++','-std=c++17','-Wall','-Wextra','-Werror','-I',str(root/'src'),str(cpp),'-o',str(exe)],check=True)
 subprocess.run([str(exe)],check=True)
print('PASS sequential airtime integration, header metadata and fallback')
