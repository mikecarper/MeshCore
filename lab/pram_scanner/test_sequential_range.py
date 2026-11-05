#!/usr/bin/env python3
"""Validate range changes through the real scanner admission method."""
from pathlib import Path
import subprocess, tempfile, sys
root=Path(__file__).resolve().parents[2]; sys.path.insert(0,str(root/'test'))
from cpp_source import body
source=(root/'src/helpers/radiolib/W12PramScannerLab.inc').read_text()
method=body(source,'bool labSequentialSetRange(')
program=r'''
#include <cassert>
#include <helpers/radiolib/W12PramSequentialLab.h>
struct Test {
 bool _lab_scan_active=false,_lab_uncertain=false,_cw_active=false;
 unsigned char state=1,_lab_seq_first=5,_lab_seq_last=8;
 struct {bool active=false;bool enabled(){return active;}} _lab_auto;
 mesh::w12pram::SequentialPlan _lab_seq_plan;
 METHOD
};
int main(){ Test t;
 assert(t.labSequentialSetRange(6,9) && t._lab_seq_first==6 && t._lab_seq_last==9);
 assert(t.labSequentialSetRange(7,10) && t._lab_seq_first==7 && t._lab_seq_last==10);
 assert(!t.labSequentialSetRange(5,9) && t._lab_seq_first==7);
 assert(!t.labSequentialSetRange(4,7) && !t.labSequentialSetRange(8,7));
 t._lab_scan_active=true; assert(!t.labSequentialSetRange(5,8));
 t._lab_scan_active=false; t._lab_auto.active=true; assert(!t.labSequentialSetRange(5,8));
 t._lab_auto.active=false; t._lab_uncertain=true; assert(!t.labSequentialSetRange(5,8));
}
'''.replace('METHOD',method)
with tempfile.TemporaryDirectory() as d:
 cpp=Path(d)/'test.cpp'; exe=Path(d)/'test'; cpp.write_text(program)
 subprocess.run(['g++','-std=c++17','-Wall','-Wextra','-Werror','-I',str(root/'src'),str(cpp),'-o',str(exe)],check=True)
 subprocess.run([str(exe)],check=True)
print('PASS sequential range admission and overrun rejection')
