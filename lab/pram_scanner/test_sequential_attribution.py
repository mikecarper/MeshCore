#!/usr/bin/env python3
"""Compile the actual scanner's slot-to-profile attribution block."""
from pathlib import Path
import subprocess
import tempfile

root = Path(__file__).resolve().parents[2]
source = (root / 'src/helpers/radiolib/W12PramScannerLab.inc').read_text()
start = source.index('    uint8_t slot_count=2;')
end = source.index('    const int len=r->getPacketLength();', start)
mapping = source[start:end]
program = r'''
#include <cassert>
#define MESH_W12_PRAM_SEQUENTIAL_LAB 1
#include <helpers/radiolib/W12PramSequentialLab.h>
struct Scanner {
  bool _lab_seq_requested=true, uncertain=false;
  mesh::w12pram::SequentialPlan _lab_seq_plan;
  mesh::w12pram::SequentialSlot _lab_seq_hit;
  mesh::w12pram::Status _lab_status;
  mesh::w12pram::Profile _lab_snap[2];
  struct { uint32_t generation[2]={11,12}; } _profiles;
  uint8_t logical=255, sf=0;
  void labMarkUncertain() { uncertain=true; }
  int map(mesh::w12pram::Status s) {
''' + mapping + r'''
    logical=ch; sf=actual->sf; return 1;
  }
};
int main() {
  Scanner scanner;
  scanner._lab_snap[0]={904525000,62500,7,5,32,11};
  scanner._lab_snap[1]={904850000,250000,7,5,32,12};
  assert(mesh::w12pram::sequentialPlan(scanner._lab_snap,0,5,8,scanner._lab_seq_plan));
  mesh::w12pram::Status status={}; status.hooked=1; status.state=4; status.irq=1u<<18;
  for (uint8_t i=0;i<4;++i) { status.last_hit=i; assert(scanner.map(status)); assert(scanner.logical==0 && scanner.sf==5+i); }
  status.last_hit=4; assert(scanner.map(status) && scanner.logical==1 && scanner.sf==7);
  ++scanner._profiles.generation[1]; status.last_hit=0;
  assert(!scanner.map(status) && scanner.uncertain);
}
'''
with tempfile.TemporaryDirectory() as directory:
    cpp, exe = Path(directory) / 'mapping.cpp', Path(directory) / 'mapping'
    cpp.write_text(program)
    subprocess.run(['g++','-std=c++17','-Wall','-Wextra','-Werror','-I',str(root/'src'),str(cpp),'-o',str(exe)],check=True)
    subprocess.run([str(exe)],check=True)
print('PASS sequential slot attribution and stale-generation rejection')
