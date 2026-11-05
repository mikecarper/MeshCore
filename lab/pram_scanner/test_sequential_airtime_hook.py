#!/usr/bin/env python3
"""Compile the production receive-airtime hook with controlled packet metadata."""
from pathlib import Path
import subprocess, tempfile
root=Path(__file__).resolve().parents[2]
s=(root/'src/helpers/radiolib/W12PramScannerLab.inc').read_text()
start=s.index('  uint32_t getReceivedAirtimeFor(')
end=s.index('\n#endif',start)
hook=s[start:end].replace(' override {',' {')
assert s.index('received_params_valid=true') < s.index('r->readData(bytes,len)')
assert s.index('r->readData(bytes,len)') < s.index('_lab_seq_rx_valid=received_params_valid;')
program='''
#include <cassert>
#include <helpers/radiolib/W12PramSequentialLab.h>
struct Test {
 bool _lab_seq_requested=true, _lab_scan_active=true, _lab_seq_rx_valid=true, _lab_seq_rx_crc=true;
 int _lab_last_profile=0; unsigned char _lab_seq_rx_cr=5; unsigned int _lab_seq_rx_unknown=0;
 mesh::w12pram::Profile _lab_last={904525000,62500,5,5,32,11};
''' + hook + '''
};
int main(){ Test t;
 assert(t.getReceivedAirtimeFor(0,50,123)==78);
 assert(t.getReceivedAirtimeFor(1,50,123)==123);
 t._lab_last.sf=8; t._lab_seq_rx_cr=6;
 assert(t.getReceivedAirtimeFor(0,50,123)==501);
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
