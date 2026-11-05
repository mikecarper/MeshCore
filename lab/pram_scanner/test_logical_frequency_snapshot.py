#!/usr/bin/env python3
"""Capture the configured frequencies even if the source settings change."""
from pathlib import Path
import re
import subprocess
import tempfile

root = Path(__file__).resolve().parents[2]
source = (root / 'src/helpers/radiolib/W12PramScannerLab.inc').read_text()
declaration = re.search(r'float _lab_logical_mhz\[2\] = \{\};', source).group()
capture = re.search(r'_lab_logical_mhz\[i\]=p\.freq;', source).group()
delivery = re.search(r'_lab_last_profile=ch; _lab_last=_lab_snap\[ch\]; '
                     r'_lab_last_logical_mhz=_lab_logical_mhz\[ch\];', source).group()
program = r'''
#include <cassert>
#include <cstdint>
struct Params { float freq; };
class Scanner {
public:
''' + declaration + r'''
  int _lab_last_profile=-1, _lab_last=0, _lab_snap[2]={11,22};
  float _lab_last_logical_mhz=0;
  void capture(uint8_t i, const Params& p) { ''' + capture + r''' }
  void deliver(uint8_t ch) { ''' + delivery + r''' }
};
int main() {
  Scanner s;
  static_assert(sizeof(s._lab_logical_mhz) == 2 * sizeof(float), "compact snapshot");
  Params p0{904.525f}, p1{904.850f};
  s.capture(0, p0); s.capture(1, p1);
  p0.freq=910.525f; p1.freq=911.150f;
  s.deliver(0); assert(s._lab_last_profile==0 && s._lab_last==11 && s._lab_last_logical_mhz==904.525f);
  s.deliver(1); assert(s._lab_last_profile==1 && s._lab_last==22 && s._lab_last_logical_mhz==904.850f);
}
'''
with tempfile.TemporaryDirectory() as td:
    cpp = Path(td) / 'test.cpp'
    exe = Path(td) / 'test'
    cpp.write_text(program)
    subprocess.run(['g++', '-std=c++17', str(cpp), '-o', str(exe)], check=True)
    subprocess.run([str(exe)], check=True)
print('PASS immutable per-profile scanner frequency snapshots')
