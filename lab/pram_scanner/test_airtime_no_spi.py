#!/usr/bin/env python3
"""RXPS watchdog airtime must not reach LR2021's SPI-reading ToA method."""
from pathlib import Path
import subprocess
import tempfile

root = Path(__file__).resolve().parents[2]
source = (root / 'src/helpers/radiolib/W12PramScannerLab.inc').read_text()
start = source.index('  uint32_t getEstAirtimeFor(')
end = source.index('  float packetScore(', start)
method = source[start:end]
program = r'''
#include <cassert>
#include <cstdint>
class RadioLibWrapper {
public:
  int driver_calls=0;
  virtual uint32_t getEstAirtimeFor(int) { ++driver_calls; return 99; }
};
class Wrapper : public RadioLibWrapper {
public:
  bool _lab_scan_active=false, _rx_ps_armed=false, _params_valid=true;
  int _lab_last_profile=-1; uint8_t _active_profile=0, _cur_cr=5;
  int profile_calls=0, last_profile=-1, last_len=-1, last_cr=-1;
  uint32_t getProfileAirtime(uint8_t p, int len, uint8_t cr=0) {
    ++profile_calls; last_profile=p; last_len=len; last_cr=cr; return 123;
  }
''' + method + r'''
};
int main() {
  Wrapper w;
  assert(w.getEstAirtimeFor(10)==99 && w.driver_calls==1);
  w._rx_ps_armed=true;
  assert(w.getEstAirtimeFor(123)==123 && w.driver_calls==1);
  assert(w.last_profile==0 && w.last_len==123 && w.last_cr==5);
  w._lab_scan_active=true; w._lab_last_profile=1;
  assert(w.getEstAirtimeFor(55)==123 && w.last_profile==1 && w.driver_calls==1);
  w._lab_scan_active=false; w._rx_ps_armed=false;
  assert(w.getEstAirtimeFor(8)==99 && w.driver_calls==2);
}
'''
with tempfile.TemporaryDirectory() as td:
    cpp = Path(td) / 'test.cpp'; exe = Path(td) / 'test'; cpp.write_text(program)
    subprocess.run(['g++', '-std=c++17', str(cpp), '-o', str(exe)], check=True)
    subprocess.run([str(exe)], check=True)
print('PASS RXPS airtime uses cached profile without SPI')
